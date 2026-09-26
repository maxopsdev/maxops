"""Action registry for managing check action handlers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Any, List, Optional

from app.actions.base import ActionExecutionContext
from app.schemas.check import CheckActionResponse


ActionHandler = Callable[[ActionExecutionContext], Optional[CheckActionResponse]]


@dataclass
class ActionMetadata:
    """Metadata for an action handler."""
    action_key: str
    name: str
    description: str
    resource_types: List[str]  # Which resource types this action applies to
    handler: Optional[ActionHandler] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    executable: bool = True


class ActionRegistry:
    """Central registry for all action handlers."""

    def __init__(self) -> None:
        """Initialize the action registry."""
        self._actions: Dict[str, ActionMetadata] = {}

    def register(self, metadata: ActionMetadata) -> None:
        """
        Register an action handler.

        Args:
            metadata: Action metadata including handler and parameters

        Raises:
            ValueError: If the key is duplicated or executability and handler
                presence are inconsistent.
        """
        if metadata.action_key in self._actions:
            raise ValueError(f"Action '{metadata.action_key}' is already registered")
        if metadata.executable and metadata.handler is None:
            raise ValueError(
                f"Executable action '{metadata.action_key}' requires a handler"
            )
        if not metadata.executable and metadata.handler is not None:
            raise ValueError(
                f"Advisory action '{metadata.action_key}' must not have a handler"
            )
        self._actions[metadata.action_key] = metadata

    def get_action(self, action_key: str) -> Optional[ActionMetadata]:
        """
        Get action metadata by key.

        Args:
            action_key: The action identifier

        Returns:
            ActionMetadata if found, None otherwise
        """
        return self._actions.get(action_key)

    def list_actions(self, resource_type: Optional[str] = None) -> List[ActionMetadata]:
        """
        List all registered actions, optionally filtered by resource type.

        Args:
            resource_type: Optional resource type filter

        Returns:
            List of action metadata
        """
        actions = list(self._actions.values())
        if resource_type:
            actions = [a for a in actions if resource_type in a.resource_types]
        return actions

    def execute(self, context: ActionExecutionContext) -> Optional[CheckActionResponse]:
        """
        Execute an action by key.

        Args:
            context: Action execution context

        Returns:
            CheckActionResponse if action was handled, None otherwise
        """
        metadata = self.get_action(context.action_key)
        if not metadata:
            return None

        if not metadata.executable:
            raise RuntimeError(
                f"Action '{context.action_key}' is advisory-only and cannot be executed"
            )
        if metadata.handler is None:
            raise RuntimeError(f"Action '{context.action_key}' has no handler")
        return metadata.handler(context)


# Global registry instance
action_registry = ActionRegistry()
