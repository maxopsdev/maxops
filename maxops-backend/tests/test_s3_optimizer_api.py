"""Stored-result API coverage for the S3 optimizer."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.s3_optimizer import get_s3_optimizer_detail, list_s3_optimizer_buckets
from app.database import Base
from app.models.inventory import S3Inventory
from fastapi import HTTPException


@pytest.fixture
def s3_session():
    """Provide an isolated in-memory inventory session."""

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_s3_optimizer_detail_returns_stored_unknown_result(s3_session):
    """Unknown telemetry is a valid stored response, not a missing row."""

    s3_session.add(
        S3Inventory(
            inventory_id=41,
            resource_id="bucket-a",
            region="us-east-1",
            metadata_json={
                "s3_optimizer": {
                    "inventory_id": 41,
                    "resource_id": "bucket-a",
                    "telemetry_status": "unknown",
                    "error": "CUR cache unavailable",
                }
            },
        )
    )
    s3_session.commit()

    result = get_s3_optimizer_detail(41, db=s3_session)

    assert result["telemetry_status"] == "unknown"
    assert result["error"] == "CUR cache unavailable"


def test_s3_optimizer_detail_404s_only_for_missing_inventory(s3_session):
    """A missing inventory row is the only detail lookup that raises 404."""

    with pytest.raises(HTTPException) as exc_info:
        get_s3_optimizer_detail(999, db=s3_session)

    assert exc_info.value.status_code == 404


def test_s3_optimizer_list_returns_paginated_stored_summary(s3_session):
    """The list endpoint exposes the stable summary fields without recompute."""

    s3_session.add(
        S3Inventory(
            inventory_id=42,
            resource_id="bucket-b",
            region="us-east-1",
            metadata_json={
                "s3_optimizer": {
                    "telemetry_status": "usable",
                    "confidence": "medium",
                    "pattern": {"pattern_label": "actively_read"},
                    "recommendation": {"policy": "STANDARD_IA", "reason": "observed"},
                }
            },
        )
    )
    s3_session.commit()

    response = list_s3_optimizer_buckets(limit=1, offset=0, region="us-east-1", db=s3_session)

    assert response["total"] == 1
    assert response["items"] == [
        {
            "inventory_id": 42,
            "resource_id": "bucket-b",
            "region": "us-east-1",
            "telemetry_status": "usable",
            "confidence": "medium",
            "pattern_label": "actively_read",
            "recommendation": {"policy": "STANDARD_IA", "reason": "observed"},
        }
    ]
