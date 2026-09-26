"""Pricing registry for managing check pricing handlers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Any, List, Optional

from app.pricing.base import PricingContext


PricingHandler = Callable[[PricingContext], None]


@dataclass
class PricingMetadata:
    """Metadata for a pricing handler."""
    check_id: str
    name: str
    description: str
    resource_type: str
    handler: PricingHandler
    parameters: Dict[str, Any] = field(default_factory=dict)


class PricingRegistry:
    """Central registry for all pricing handlers."""

    def __init__(self) -> None:
        """Initialize the pricing registry."""
        self._pricing: Dict[str, PricingMetadata] = {}

    def register(self, metadata: PricingMetadata) -> None:
        """
        Register a pricing handler.

        Args:
            metadata: Pricing metadata including handler and parameters

        Raises:
            ValueError: If check_id is already registered
        """
        if metadata.check_id in self._pricing:
            raise ValueError(f"Pricing for check '{metadata.check_id}' is already registered")
        self._pricing[metadata.check_id] = metadata

    def get_pricing(self, check_id: str) -> Optional[PricingMetadata]:
        """
        Get pricing metadata by check_id.

        Args:
            check_id: The check identifier

        Returns:
            PricingMetadata if found, None otherwise
        """
        return self._pricing.get(check_id)

    def list_pricing(self, resource_type: Optional[str] = None) -> List[PricingMetadata]:
        """
        List all registered pricing handlers, optionally filtered by resource type.

        Args:
            resource_type: Optional resource type filter

        Returns:
            List of pricing metadata
        """
        pricing_list = list(self._pricing.values())
        if resource_type:
            pricing_list = [p for p in pricing_list if p.resource_type == resource_type]
        return pricing_list

    def apply_pricing(self, context: PricingContext) -> None:
        """
        Apply pricing for the given check_id.

        Args:
            context: Pricing context with check_id and resources
        """
        metadata = self.get_pricing(context.check_id)
        if metadata:
            # Merge context parameters with registered parameters if needed
            metadata.handler(context)
        # If no handler found, pricing is not applied (some checks don't have pricing)


# Global registry instance
pricing_registry = PricingRegistry()
