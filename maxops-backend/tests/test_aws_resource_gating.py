"""Nothing that can build real AWS resources may run in a default test run.

tests/test_ec2_only.py sat directly in tests/, imported EC2TestResources, and
wrapped it in its own class-scoped fixture. It therefore missed both guards
described in CONTRIBUTING.md: the tests/integration/ path tag, and the
MAXOPS_RUN_AWS_INTEGRATION_TESTS check on the shared `test_resources` fixture.
A plain `pytest tests/` launched two t3.micro instances and reported them as
five passing tests.

conftest now tags by capability rather than path. This holds that line
statically, so a new file in the same shape fails here instead of on someone's
AWS bill.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

TESTS_ROOT = Path(__file__).resolve().parent

# The managers in tests/fixtures/ that call create_* against a real account.
RESOURCE_MANAGER_SUFFIX = "TestResources"


def _imports_a_resource_manager(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tests.fixtures"):
            if any(alias.name.endswith(RESOURCE_MANAGER_SUFFIX) for alias in node.names):
                return True
    return False


def test_aws_building_modules_are_confined_or_marked():
    offenders = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        rel = path.relative_to(TESTS_ROOT).as_posix()
        if rel.startswith("integration/") or path.name == "conftest.py":
            continue
        if not _imports_a_resource_manager(path):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if "pytest.mark.integration" in source:
            continue
        offenders.append(rel)

    assert offenders == [], (
        "These modules can create real, billable AWS resources but sit outside "
        "tests/integration/ and carry no integration marker, so a plain "
        "`pytest tests/` would run them:\n"
        + "\n".join(f"  tests/{name}" for name in offenders)
        + "\nMove them under tests/integration/ or set "
          "pytestmark = [pytest.mark.integration]."
    )
