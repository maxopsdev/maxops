"""Utility functions."""
# Legacy imports - only import if available (for backward compatibility)
try:
    from app.utils.policy_parser import PolicyParser, PolicyValidator
    from app.utils.filter_registry import validate_filter, get_available_filters
    __all__ = ["PolicyParser", "PolicyValidator", "validate_filter", "get_available_filters"]
except ImportError:
    # Removed by the checks refactoring; absent in current trees.
    PolicyParser = None
    PolicyValidator = None
    validate_filter = None
    get_available_filters = None
    __all__ = []

# Execution registry is still available
try:
    from app.utils.execution_registry import execution_registry
    if execution_registry:
        __all__.append("execution_registry")
except ImportError:
    execution_registry = None

