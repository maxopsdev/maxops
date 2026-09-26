"""Safety gate shared by every real-AWS payload capture entry point."""

from __future__ import annotations

import os


def require_capture_gate(apply: bool, *, resource_plan: str | None = None) -> None:
    """Refuse billable capture unless the environment and CLI gates are set.

    ``resource_plan`` is optional so callers with a known cost or resource
    summary can show it before refusing an unsafe invocation.  The function
    returns ``None`` on an approved invocation and raises ``SystemExit`` on
    the empty/unsafe case.
    """
    if os.environ.get("MAXOPS_RUN_AWS_INTEGRATION_TESTS") == "1" and apply:
        return
    if resource_plan:
        print(f"Capture resource plan: {resource_plan}")
    label = resource_plan or "Payload capture"
    raise SystemExit(
        f"{label} requires MAXOPS_RUN_AWS_INTEGRATION_TESTS=1 and --apply"
    )
