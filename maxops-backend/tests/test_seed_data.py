"""Policy seed regression tests."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.policy import Policy
from app.utils.seed_data import seed_policies


def test_seed_policies_readds_only_a_missing_optimizer_policy():
    """Non-forced seeding adds a deleted check without rewriting peers."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_policies(session)
        optimizer_ids = {
            "s3_bucket_unused",
            "s3_bucket_low_access",
            "s3_bucket_retrieval_cost_dominant",
        }
        seeded = {
            row.check_id: row
            for row in session.query(Policy).filter(Policy.check_id.in_(optimizer_ids)).all()
        }
        assert set(seeded) == optimizer_ids

        untouched = {
            check_id: (row.name, row.description, row.parameters_json, row.status)
            for check_id, row in seeded.items()
            if check_id != "s3_bucket_low_access"
        }
        session.delete(seeded["s3_bucket_low_access"])
        session.commit()

        assert seed_policies(session) == 1

        restored = session.query(Policy).filter(Policy.check_id.in_(optimizer_ids)).all()
        assert {row.check_id for row in restored} == optimizer_ids
        assert {
            row.check_id: (row.name, row.description, row.parameters_json, row.status)
            for row in restored
            if row.check_id != "s3_bucket_low_access"
        } == untouched
