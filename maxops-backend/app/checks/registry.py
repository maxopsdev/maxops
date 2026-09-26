"""Check registry for managing AWS resource checks."""
from typing import Dict, Any, Callable, List, Optional
from dataclasses import dataclass, field


@dataclass
class CheckMetadata:
    """Metadata for a check function."""
    check_id: str
    name: str
    description: str
    resource_type: str
    check_function: Callable
    default_action: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    cost_calculation: Optional[Callable] = None


class CheckRegistry:
    """Central registry for all check functions."""

    def __init__(self):
        """Initialize the check registry."""
        self._checks: Dict[str, CheckMetadata] = {}

    def register(self, metadata: CheckMetadata) -> None:
        """
        Register a check function.

        Args:
            metadata: Check metadata including function and parameters
        """
        if metadata.check_id in self._checks:
            raise ValueError(f"Check '{metadata.check_id}' is already registered")
        self._checks[metadata.check_id] = metadata

    def get_check(self, check_id: str) -> Optional[CheckMetadata]:
        """
        Get check metadata by ID.

        Args:
            check_id: The check identifier

        Returns:
            CheckMetadata if found, None otherwise
        """
        return self._checks.get(check_id)

    def list_checks(self, resource_type: Optional[str] = None) -> List[CheckMetadata]:
        """
        List all registered checks, optionally filtered by resource type.

        Args:
            resource_type: Optional resource type filter (e.g., 'ec2', 'rds')

        Returns:
            List of check metadata
        """
        checks = list(self._checks.values())
        if resource_type:
            checks = [c for c in checks if c.resource_type == resource_type]
        return checks

    def execute_check(
        self,
        check_id: str,
        aws_adapter,
        parameters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute a check by ID.

        Args:
            check_id: The check identifier
            aws_adapter: AWS adapter instance
            parameters: Optional parameters to override defaults

        Returns:
            List of resources that matched the check criteria

        Raises:
            ValueError: If check_id not found
        """
        metadata = self.get_check(check_id)
        if not metadata:
            raise ValueError(f"Check '{check_id}' not found in registry")

        # Merge default parameters with provided parameters
        params = {**metadata.parameters, **(parameters or {})}

        # Execute check function
        return metadata.check_function(aws_adapter, **params)


# Global registry instance
check_registry = CheckRegistry()
