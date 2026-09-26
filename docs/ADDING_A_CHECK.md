# Adding a check

A "check" is a function that inspects one AWS resource type for a specific waste/optimization pattern and returns the resources that match. With 112 checks already registered across 32 resource types, adding one more is the most approachable first contribution to MaxOps — no real AWS account is required to write and test one.

This should take about 20 minutes. Worked reference: [`app/checks/asg/idle_capacity_high.py`](../maxops-backend/app/checks/asg/idle_capacity_high.py) and its tests in [`tests/test_ec2_payload_checks.py`](../maxops-backend/tests/test_ec2_payload_checks.py).

## 1. Write the check function

Create a module under `app/checks/<service>/` (reuse an existing service directory, or create a new one if the service doesn't have checks yet):

```python
# app/checks/<service>/my_new_check.py
"""One-line description of what this check flags."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.checks.base import create_check_reason
from app.checks.registry import CheckMetadata, check_registry


def check_<service>_my_new_check(
    aws_adapter,
    threshold: float = 10.0,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    client = aws_adapter.session.client("<boto3-service-name>", region_name=region)

    flagged: List[Dict[str, Any]] = []
    for resource in ...:  # however you enumerate resources via `client`
        if not matches_your_condition(resource, threshold):
            continue
        flagged.append(
            {
                "resource_id": resource["Id"],
                "resource_type": "<service>",
                "resource_name": resource.get("Name"),
                "region": region,
                "state": resource.get("State"),
                "metadata": {
                    "threshold": threshold,
                    "recommended_action": "<action_key>",
                    "check_reason": create_check_reason("underutilized", {"threshold": threshold}),
                },
            }
        )
    return flagged


check_registry.register(
    CheckMetadata(
        check_id="<service>_my_new_check",
        name="Human-readable name",
        description="One sentence describing what gets flagged and why it costs money.",
        resource_type="<service>",
        check_function=check_<service>_my_new_check,
        default_action="<action_key>",
        parameters={"threshold": 10.0, "region": None},
    )
)
```

Key conventions:

- `check_id` is `<resource_type>_<condition>`, must be globally unique — `check_registry.register()` raises if it collides.
- Every returned dict needs `resource_id`, `resource_type`, `region`; put everything else under `metadata`.
- `default_action` should match a key already defined in `app/actions/action_mapping.py` if you want a "Fix it" button to work immediately — otherwise the check will still show up as a finding without a one-click action.
- All AWS calls go through `aws_adapter.session.client(...)` — never `boto3` directly — so the check works with both the real adapter and the test fake.

## 2. Register the module

Add an import in `app/checks/__init__.py` (grouped by service, alongside the existing imports) — this is what triggers `check_registry.register()` at startup:

```python
from app.checks.<service> import my_new_check
```

## 3. Add a payload fixture and test

MaxOps checks are tested against **recorded boto3-shaped payloads**, not live AWS calls or ad-hoc mocks. Look at `tests/payload_helpers.py` and an existing adapter builder such as `_build_adapter_for_idle_scenario` in `tests/test_ec2_payload_adapter.py` for the pattern: a small fake adapter is built from a named scenario's fixture data, then handed straight to your check function.

For your new check:

1. Add a fixture (or reuse an existing payload file) covering at least: one resource that **should** be flagged, and one that **should not** (a clear negative case).
2. Write a test module (e.g. `tests/test_<service>_payload_checks.py`) that imports your check function directly and asserts on the returned `resource_id`s and `metadata`.
3. Run just your new test file — **never** a broad or keyword-based `pytest` invocation across `tests/`, see [CONTRIBUTING.md](../CONTRIBUTING.md#running-tests-safely):

   ```bash
   cd maxops-backend
   pytest tests/test_<service>_payload_checks.py
   ```

## 4. Sanity-check registration

```bash
cd maxops-backend
python -c "import app.checks; from app.checks.registry import check_registry; print(check_registry.get_check('<service>_my_new_check'))"
```

If this prints your `CheckMetadata`, the check is live — it'll appear in Settings, in scan results, and in the Checks Manager UI without any frontend changes.

## Optional: a cost calculation

If the check has a well-defined dollar savings estimate, look at `app/pricing/` for the per-service pricing helpers and pass a `cost_calculation` callable into `CheckMetadata` — see existing checks in the same resource-type directory for the expected signature.
