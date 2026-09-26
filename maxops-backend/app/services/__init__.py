"""Business logic services."""

__all__ = ["PolicyService"]


def __getattr__(name: str):
    if name == "PolicyService":
        from app.services.policy_service import PolicyService

        return PolicyService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
