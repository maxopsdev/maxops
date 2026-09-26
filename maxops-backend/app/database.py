"""Database configuration and session management."""
import json

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pathlib import Path
from app.config import settings


def _resolve_database_url(database_url: str) -> str:
    """Resolve relative SQLite database paths against the backend project root."""
    sqlite_prefix = "sqlite:///"
    if not database_url.startswith(sqlite_prefix):
        return database_url

    raw_path = database_url[len(sqlite_prefix):]
    if not raw_path.startswith("./"):
        return database_url

    backend_root = Path(__file__).resolve().parent.parent
    absolute_path = (backend_root / raw_path[2:]).resolve()
    return f"{sqlite_prefix}{absolute_path.as_posix()}"


# Create database engine
engine = create_engine(
    _resolve_database_url(settings.database_url),
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {}
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db():
    """Dependency for getting database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    # Ensure all model modules are imported so Base metadata includes every table.
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _sync_existing_schema()
    _ensure_resource_tags_backfilled()


def _sync_existing_schema():
    """Apply lightweight additive schema updates for existing local databases."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    index_names_by_table: dict[str, set[str]] = {}
    with engine.connect() as connection:
        index_rows = connection.execute(
            text("SELECT tbl_name, name FROM sqlite_master WHERE type='index' AND name IS NOT NULL")
        ).all()
        for tbl_name, index_name in index_rows:
            index_names_by_table.setdefault(str(tbl_name), set()).add(str(index_name))
    statements: list[str] = []
    drop_index_statements: list[str] = []

    if "user_settings" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("user_settings")}
        if "accounts" not in existing_columns:
            statements.append("ALTER TABLE user_settings ADD COLUMN accounts JSON")
        if "regions" not in existing_columns:
            statements.append("ALTER TABLE user_settings ADD COLUMN regions JSON")

    if "account_settings" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("account_settings")}
        if "environment_options" not in existing_columns:
            statements.append("ALTER TABLE account_settings ADD COLUMN environment_options JSON")
        if "onboarding_step" not in existing_columns:
            statements.append("ALTER TABLE account_settings ADD COLUMN onboarding_step VARCHAR(50) DEFAULT 'information' NOT NULL")
        if "onboarding_data" not in existing_columns:
            statements.append("ALTER TABLE account_settings ADD COLUMN onboarding_data JSON")
        if "cur_pricing_enabled" not in existing_columns:
            statements.append("ALTER TABLE account_settings ADD COLUMN cur_pricing_enabled BOOLEAN")
        if "setup_aws_profile" not in existing_columns:
            statements.append("ALTER TABLE account_settings ADD COLUMN setup_aws_profile VARCHAR(100)")

    if "resource_exemptions" in table_names:
        exemption_columns = {column["name"] for column in inspector.get_columns("resource_exemptions")}
        if "snooze_reason" not in exemption_columns:
            statements.append("ALTER TABLE resource_exemptions ADD COLUMN snooze_reason TEXT")

    create_index_statements: list[str] = []
    if "ec2_inventory" in table_names:
        ec2_columns = {column["name"] for column in inspector.get_columns("ec2_inventory")}
        if "usage_profile" not in ec2_columns:
            statements.append("ALTER TABLE ec2_inventory ADD COLUMN usage_profile VARCHAR(100)")
        if "avg_cpu_utilization" not in ec2_columns:
            statements.append("ALTER TABLE ec2_inventory ADD COLUMN avg_cpu_utilization FLOAT")
        if "avg_memory_utilization" not in ec2_columns:
            statements.append("ALTER TABLE ec2_inventory ADD COLUMN avg_memory_utilization FLOAT")
        if "avg_network_in" not in ec2_columns:
            statements.append("ALTER TABLE ec2_inventory ADD COLUMN avg_network_in FLOAT")
        if "avg_network_out" not in ec2_columns:
            statements.append("ALTER TABLE ec2_inventory ADD COLUMN avg_network_out FLOAT")
        if "monthly_cost_estimate" not in ec2_columns:
            statements.append("ALTER TABLE ec2_inventory ADD COLUMN monthly_cost_estimate FLOAT")
        if "metric_history_json" not in ec2_columns:
            statements.append("ALTER TABLE ec2_inventory ADD COLUMN metric_history_json JSON")

        existing_indexes = index_names_by_table.get("ec2_inventory", set())
        if "ix_ec2_inventory_usage_profile" not in existing_indexes:
            create_index_statements.append("CREATE INDEX ix_ec2_inventory_usage_profile ON ec2_inventory (usage_profile)")

    if "rds_inventory" in table_names:
        rds_columns = {column["name"] for column in inspector.get_columns("rds_inventory")}
        if "avg_cpu_utilization" not in rds_columns:
            statements.append("ALTER TABLE rds_inventory ADD COLUMN avg_cpu_utilization FLOAT")
        if "avg_connections" not in rds_columns:
            statements.append("ALTER TABLE rds_inventory ADD COLUMN avg_connections FLOAT")
        if "avg_read_iops" not in rds_columns:
            statements.append("ALTER TABLE rds_inventory ADD COLUMN avg_read_iops FLOAT")
        if "avg_write_iops" not in rds_columns:
            statements.append("ALTER TABLE rds_inventory ADD COLUMN avg_write_iops FLOAT")
        if "monthly_cost_estimate" not in rds_columns:
            statements.append("ALTER TABLE rds_inventory ADD COLUMN monthly_cost_estimate FLOAT")
        if "metric_history_json" not in rds_columns:
            statements.append("ALTER TABLE rds_inventory ADD COLUMN metric_history_json JSON")

    if "elasticache_inventory" in table_names:
        elasticache_columns = {column["name"] for column in inspector.get_columns("elasticache_inventory")}
        if "avg_curritems" not in elasticache_columns:
            statements.append("ALTER TABLE elasticache_inventory ADD COLUMN avg_curritems FLOAT")
        if "avg_keycount" not in elasticache_columns:
            statements.append("ALTER TABLE elasticache_inventory ADD COLUMN avg_keycount FLOAT")
        if "monthly_cost_estimate" not in elasticache_columns:
            statements.append("ALTER TABLE elasticache_inventory ADD COLUMN monthly_cost_estimate FLOAT")
        if "metric_history_json" not in elasticache_columns:
            statements.append("ALTER TABLE elasticache_inventory ADD COLUMN metric_history_json JSON")

    if "sagemaker_inventory" in table_names:
        sagemaker_columns = {
            column["name"] for column in inspector.get_columns("sagemaker_inventory")
        }
        additive_columns = {
            "resource_subtype": "VARCHAR(50)",
            "instance_type": "VARCHAR(100)",
            "instance_count": "INTEGER",
            "status": "VARCHAR(50)",
            "created_at": "DATETIME",
            "metric_history_json": "JSON",
        }
        for column_name, column_type in additive_columns.items():
            if column_name not in sagemaker_columns:
                statements.append(
                    f"ALTER TABLE sagemaker_inventory ADD COLUMN {column_name} {column_type}"
                )

    # Cleanup: remove legacy single-tag expression indexes so tag optimization stays generic.
    legacy_tag_specific_indexes = [
        "ix_ec2_inventory_tag_cost_center",
        "ix_rds_inventory_tag_cost_center",
        "ix_s3_inventory_tag_cost_center",
        "ix_dynamodb_inventory_tag_cost_center",
        "ix_ebs_inventory_tag_cost_center",
        "ix_elasticache_inventory_tag_cost_center",
    ]
    existing_index_names = {name for names in index_names_by_table.values() for name in names}
    for index_name in legacy_tag_specific_indexes:
        if index_name in existing_index_names:
            drop_index_statements.append(f"DROP INDEX IF EXISTS {index_name}")

    if "resource_tags" in table_names:
        existing_indexes = index_names_by_table.get("resource_tags", set())
        if "idx_resource_tags_type_key_value" not in existing_indexes:
            create_index_statements.append(
                "CREATE INDEX IF NOT EXISTS idx_resource_tags_type_key_value "
                "ON resource_tags (resource_type, tag_key_normalized, tag_value_normalized)"
            )
        if "idx_resource_tags_type_value" not in existing_indexes:
            create_index_statements.append(
                "CREATE INDEX IF NOT EXISTS idx_resource_tags_type_value "
                "ON resource_tags (resource_type, tag_value_normalized)"
            )
        if "idx_resource_tags_type_inventory" not in existing_indexes:
            create_index_statements.append(
                "CREATE INDEX IF NOT EXISTS idx_resource_tags_type_inventory "
                "ON resource_tags (resource_type, inventory_id)"
            )

    if not statements and not create_index_statements and not drop_index_statements:
        return

    with engine.begin() as connection:
        for statement in drop_index_statements:
            connection.execute(text(statement))

        for statement in statements:
            connection.execute(text(statement))

        if "user_settings" in table_names:
            connection.execute(
                text(
                    """
                    UPDATE user_settings
                    SET accounts = json_array(account)
                    WHERE accounts IS NULL AND account IS NOT NULL AND trim(account) != ''
                    """
                )
            )

        if "account_settings" in table_names:
            rows = connection.execute(
                text("SELECT id, environment FROM account_settings WHERE environment_options IS NULL")
            ).mappings()
            for row in rows:
                values = [
                    "Development",
                    "Staging",
                    "Production",
                    "UAT",
                    "QA",
                    "Testing",
                    "Sandbox",
                    row.get("environment"),
                ]
                normalized: list[str] = []
                seen: set[str] = set()
                for value in values:
                    item = str(value or "").strip()
                    if not item:
                        continue
                    key = item.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    normalized.append(item)
                connection.execute(
                    text("UPDATE account_settings SET environment_options = :environment_options WHERE id = :id"),
                    {"environment_options": json.dumps(normalized), "id": row["id"]},
                )
            connection.execute(
                text(
                    """
                    UPDATE user_settings
                    SET regions = json_array(region)
                    WHERE regions IS NULL AND region IS NOT NULL AND trim(region) != ''
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE account_settings
                    SET onboarding_step = CASE
                        WHEN onboarding_completed = 1 THEN 'completed'
                        WHEN account IS NOT NULL AND trim(account) != '' THEN 'account'
                        ELSE COALESCE(onboarding_step, 'information')
                    END
                    WHERE onboarding_step IS NULL OR trim(onboarding_step) = ''
                    """
                )
            )

        if "ec2_inventory" in table_names:
            connection.execute(
                text(
                    """
                    UPDATE ec2_inventory
                    SET usage_profile = json_extract(metadata_json, '$.usage_profile')
                    WHERE usage_profile IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE ec2_inventory
                    SET avg_cpu_utilization = CAST(json_extract(metadata_json, '$.avg_cpu_utilization') AS FLOAT)
                    WHERE avg_cpu_utilization IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE ec2_inventory
                    SET avg_memory_utilization = CAST(json_extract(metadata_json, '$.avg_memory_utilization') AS FLOAT)
                    WHERE avg_memory_utilization IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE ec2_inventory
                    SET avg_network_in = CAST(json_extract(metadata_json, '$.avg_network_in') AS FLOAT)
                    WHERE avg_network_in IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE ec2_inventory
                    SET avg_network_out = CAST(json_extract(metadata_json, '$.avg_network_out') AS FLOAT)
                    WHERE avg_network_out IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE ec2_inventory
                    SET monthly_cost_estimate = CAST(json_extract(metadata_json, '$.monthly_cost_estimate') AS FLOAT)
                    WHERE monthly_cost_estimate IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE ec2_inventory
                    SET metric_history_json = json_extract(metadata_json, '$.metric_history')
                    WHERE metric_history_json IS NULL
                    """
                )
            )

        if "rds_inventory" in table_names:
            connection.execute(
                text(
                    """
                    UPDATE rds_inventory
                    SET avg_cpu_utilization = CAST(json_extract(metadata_json, '$.avg_cpu_utilization') AS FLOAT)
                    WHERE avg_cpu_utilization IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE rds_inventory
                    SET avg_connections = CAST(json_extract(metadata_json, '$.avg_connections') AS FLOAT)
                    WHERE avg_connections IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE rds_inventory
                    SET avg_read_iops = CAST(json_extract(metadata_json, '$.avg_read_iops') AS FLOAT)
                    WHERE avg_read_iops IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE rds_inventory
                    SET avg_write_iops = CAST(json_extract(metadata_json, '$.avg_write_iops') AS FLOAT)
                    WHERE avg_write_iops IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE rds_inventory
                    SET monthly_cost_estimate = CAST(json_extract(metadata_json, '$.monthly_cost_estimate') AS FLOAT)
                    WHERE monthly_cost_estimate IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE rds_inventory
                    SET metric_history_json = json_extract(metadata_json, '$.metric_history')
                    WHERE metric_history_json IS NULL
                    """
                )
            )

        if "elasticache_inventory" in table_names:
            connection.execute(
                text(
                    """
                    UPDATE elasticache_inventory
                    SET avg_curritems = CAST(json_extract(metadata_json, '$.avg_curritems') AS FLOAT)
                    WHERE avg_curritems IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE elasticache_inventory
                    SET avg_keycount = CAST(json_extract(metadata_json, '$.avg_keycount') AS FLOAT)
                    WHERE avg_keycount IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE elasticache_inventory
                    SET monthly_cost_estimate = CAST(json_extract(metadata_json, '$.monthly_cost_estimate') AS FLOAT)
                    WHERE monthly_cost_estimate IS NULL
                    """
                )
            )
            connection.execute(
                text(
                    """
                    UPDATE elasticache_inventory
                    SET metric_history_json = json_extract(metadata_json, '$.metric_history')
                    WHERE metric_history_json IS NULL
                    """
                )
            )

        for statement in create_index_statements:
            connection.execute(text(statement))


def _ensure_resource_tags_backfilled():
    """Backfill normalized tags for existing inventory rows if table is empty."""
    inspector = inspect(engine)
    if "resource_tags" not in set(inspector.get_table_names()):
        return

    from app.models.inventory import ResourceTag
    from app.utils.resource_tags import backfill_resource_tags

    db = SessionLocal()
    try:
        existing = db.query(ResourceTag).count()
        if existing > 0:
            return
        inserted = backfill_resource_tags(db)
        db.commit()
        if inserted:
            print(f"Populated resource_tags with {inserted} rows")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def seed_db_if_empty():
    """Seed database with default policies if empty."""
    from app.utils.seed_data import seed_database
    from app.database import SessionLocal
    
    db = SessionLocal()
    try:
        result = seed_database(db)
        if result['seeded']:
            print(f"✅ {result['message']}")
    finally:
        db.close()
