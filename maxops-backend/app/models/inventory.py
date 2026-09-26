"""Inventory models for imported synthetic and AWS payload data."""
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, Float, Index, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base


class MaxOpsInventory(Base):
    """Shared MaxOps-level inventory for all resource types."""
    __tablename__ = "maxops_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False)
    resource_type = Column(String(100), nullable=False, index=True)
    resource_id = Column(String(255), nullable=False, index=True)
    resource_name = Column(String(255), nullable=True)
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)

    check_id = Column(String(255), nullable=True, index=True)
    finding_type = Column(String(100), nullable=True, index=True)
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    severity = Column(String(20), nullable=True, index=True)
    confidence_score = Column(Float, nullable=True)
    risk_score = Column(Float, nullable=True)
    recommended_action = Column(String(100), nullable=True)
    recommended_actions_json = Column(JSON, nullable=True)
    available_actions_json = Column(JSON, nullable=True)
    potential_savings_monthly = Column(Float, nullable=True)
    potential_savings_yearly = Column(Float, nullable=True)
    evidence_json = Column(JSON, nullable=True)
    current_config_json = Column(JSON, nullable=True)
    target_config_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("resource_type", "inventory_id", name="uq_maxops_inventory_type_inventory"),
        Index("idx_maxops_inventory_check_generated", "check_id", "generated_at"),
    )


class Ec2Inventory(Base):
    """EC2-specific imported inventory payloads."""
    __tablename__ = "ec2_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, unique=True, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="ec2")
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    availability_zone = Column(String(50), nullable=True)
    state = Column(String(50), nullable=True, index=True)
    instance_type = Column(String(100), nullable=True)
    usage_profile = Column(String(100), nullable=True, index=True)
    launch_time = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    avg_cpu_utilization = Column(Float, nullable=True)
    avg_memory_utilization = Column(Float, nullable=True)
    avg_network_in = Column(Float, nullable=True)
    avg_network_out = Column(Float, nullable=True)
    monthly_cost_estimate = Column(Float, nullable=True)
    metric_history_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class S3Inventory(Base):
    """S3-specific imported inventory payloads."""
    __tablename__ = "s3_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, unique=True, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="s3")
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    state = Column(String(50), nullable=True, index=True)
    creation_date = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    versioning_status = Column(String(50), nullable=True, index=True)
    logging_enabled = Column(String(10), nullable=True, index=True)
    inventory_configuration_count = Column(Integer, nullable=True)
    replication_rule_count = Column(Integer, nullable=True)
    bucket_size_gb = Column(Float, nullable=True)
    object_count = Column(Integer, nullable=True)
    estimated_monthly_cost = Column(Float, nullable=True)
    tags_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RdsInventory(Base):
    """RDS-specific imported inventory payloads."""
    __tablename__ = "rds_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, unique=True, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="rds")
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    availability_zone = Column(String(50), nullable=True)
    state = Column(String(50), nullable=True, index=True)
    engine = Column(String(100), nullable=True, index=True)
    db_instance_class = Column(String(100), nullable=True, index=True)
    created_at_source = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    avg_cpu_utilization = Column(Float, nullable=True)
    avg_connections = Column(Float, nullable=True)
    avg_read_iops = Column(Float, nullable=True)
    avg_write_iops = Column(Float, nullable=True)
    monthly_cost_estimate = Column(Float, nullable=True)
    metric_history_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ElasticacheInventory(Base):
    """ElastiCache-specific imported inventory payloads."""
    __tablename__ = "elasticache_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, unique=True, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="elasticache")
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    availability_zone = Column(String(50), nullable=True)
    state = Column(String(50), nullable=True, index=True)
    engine = Column(String(100), nullable=True, index=True)
    engine_version = Column(String(100), nullable=True)
    cache_node_type = Column(String(100), nullable=True, index=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    monthly_cost_estimate = Column(Float, nullable=True)
    avg_curritems = Column(Float, nullable=True)
    avg_keycount = Column(Float, nullable=True)
    metric_history_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AsgInventory(Base):
    """Auto Scaling Group inventory used by the ASG rightsizer."""
    __tablename__ = "asg_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="asg")
    account_id = Column(String(100), nullable=False, index=True)
    region = Column(String(50), nullable=False, index=True)
    state = Column(String(50), nullable=True, index=True)
    min_size = Column(Integer, nullable=False)
    desired_capacity = Column(Integer, nullable=False)
    max_size = Column(Integer, nullable=False)
    instance_type = Column(String(100), nullable=True, index=True)
    platform_normalized = Column(String(100), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    tags_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "region",
            "resource_id",
            name="uq_asg_inventory_account_region_resource",
        ),
    )


class EbsInventory(Base):
    """EBS-specific imported inventory payloads."""
    __tablename__ = "ebs_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, unique=True, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="ebs")
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    availability_zone = Column(String(50), nullable=True)
    state = Column(String(50), nullable=True, index=True)
    volume_type = Column(String(100), nullable=True, index=True)
    attached = Column(String(10), nullable=True, index=True)
    size_gb = Column(Float, nullable=True)
    iops = Column(Integer, nullable=True)
    throughput = Column(Integer, nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    avg_iops = Column(Float, nullable=True)
    avg_throughput_mb = Column(Float, nullable=True)
    monthly_cost_estimate = Column(Float, nullable=True)
    metric_history_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SageMakerInventory(Base):
    """SageMaker notebook, endpoint, and training-job inventory."""

    __tablename__ = "sagemaker_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, unique=True, index=True)
    resource_subtype = Column(String(50), nullable=False, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="sagemaker")
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    instance_type = Column(String(100), nullable=True, index=True)
    instance_count = Column(Integer, nullable=True)
    status = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    metadata_json = Column(JSON, nullable=True)
    metric_history_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DynamoDbInventory(Base):
    """DynamoDB-specific imported inventory payloads."""
    __tablename__ = "dynamodb_inventory"

    id = Column(Integer, primary_key=True, index=True)
    inventory_id = Column(Integer, nullable=False, unique=True, index=True)
    resource_id = Column(String(255), nullable=False, unique=True, index=True)
    resource_name = Column(String(255), nullable=True)
    resource_type = Column(String(100), nullable=False, default="dynamodb")
    account_id = Column(String(100), nullable=True, index=True)
    region = Column(String(50), nullable=True, index=True)
    state = Column(String(50), nullable=True, index=True)
    billing_mode = Column(String(50), nullable=True, index=True)
    table_name = Column(String(255), nullable=True, index=True)
    table_class = Column(String(100), nullable=True, index=True)
    generated_at = Column(DateTime(timezone=True), nullable=True, index=True)
    item_count = Column(Integer, nullable=True)
    read_capacity_units = Column(Float, nullable=True)
    write_capacity_units = Column(Float, nullable=True)
    monthly_cost_estimate = Column(Float, nullable=True)
    metric_history_json = Column(JSON, nullable=True)
    tags_json = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    aws_payload_json = Column(JSON, nullable=True)
    source_path = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ResourceTag(Base):
    """Normalized resource tags for fast tag search across all resource types."""
    __tablename__ = "resource_tags"

    id = Column(Integer, primary_key=True, index=True)
    resource_type = Column(String(100), nullable=False, index=True)
    inventory_id = Column(Integer, nullable=False, index=True)
    resource_id = Column(String(255), nullable=False, index=True)
    tag_key = Column(String(255), nullable=False)
    tag_value = Column(String(1024), nullable=True)
    tag_key_normalized = Column(String(255), nullable=False, index=True)
    tag_value_normalized = Column(String(1024), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "inventory_id",
            "tag_key_normalized",
            name="uq_resource_tags_resource_key",
        ),
        Index(
            "idx_resource_tags_type_key_value",
            "resource_type",
            "tag_key_normalized",
            "tag_value_normalized",
        ),
        Index(
            "idx_resource_tags_type_value",
            "resource_type",
            "tag_value_normalized",
        ),
        Index(
            "idx_resource_tags_type_inventory",
            "resource_type",
            "inventory_id",
        ),
    )
