"""Pricing cache model."""
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Index

from app.database import Base


class PricingCache(Base):
    """Cache of AWS public pricing for resource parameters."""

    __tablename__ = "pricing_cache"

    id = Column(Integer, primary_key=True, index=True)
    resource_type = Column(String, nullable=False, index=True)
    region = Column(String, nullable=False, index=True)
    parameters_hash = Column(String, nullable=False, index=True)
    parameters_json = Column(JSON, nullable=False)
    price_per_unit = Column(Float, nullable=True)
    unit = Column(String, nullable=True)
    currency = Column(String, nullable=False, default="USD")
    source = Column(String, nullable=False, default="aws_pricing")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


Index(
    "uq_pricing_cache_resource_region_params",
    PricingCache.resource_type,
    PricingCache.region,
    PricingCache.parameters_hash,
    unique=True,
)
