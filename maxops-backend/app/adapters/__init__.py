"""Cloud provider adapters."""
from app.adapters.base import CloudAdapter
from app.adapters.aws.adapter import AWSAdapter

__all__ = ["CloudAdapter", "AWSAdapter"]

