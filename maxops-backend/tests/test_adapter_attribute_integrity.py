"""Guards against the adapter reading attributes its constructor never sets.

Removing the AWS Simulator dropped the `self._use_simulator = ...` assignments
from __init__ but left one read behind, in _sagemaker_client. Every SageMaker
check failed in production with AttributeError while the suite stayed green,
because the tests build adapters with AWSAdapter.__new__ and set the attribute
by hand -- so the real constructor was never exercised.
"""
from __future__ import annotations

import ast
import io
from pathlib import Path
from typing import Any, Dict

import pytest

from app.adapters.aws import adapter as adapter_module
from app.adapters.aws.adapter import AWSAdapter

ADAPTER_SOURCE = Path(adapter_module.__file__)


def _self_attribute_usage(source: str, class_name: str):
    """Return (assigned, read) attribute names for `self` inside a class."""
    tree = ast.parse(source)
    cls = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )

    declared = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            declared.add(node.name)
        elif isinstance(node, ast.Assign):
            declared.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            declared.add(node.target.id)

    assigned, read = set(declared), {}
    for node in ast.walk(cls):
        if not isinstance(node, ast.Attribute):
            continue
        if not (isinstance(node.value, ast.Name) and node.value.id == "self"):
            continue
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            assigned.add(node.attr)
        else:
            read.setdefault(node.attr, node.lineno)
    return assigned, read


def test_every_self_attribute_read_is_also_assigned():
    assigned, read = _self_attribute_usage(
        io.open(ADAPTER_SOURCE, encoding="utf-8").read(), "AWSAdapter"
    )
    dangling = {name: line for name, line in read.items() if name not in assigned}
    assert not dangling, (
        "AWSAdapter reads attributes nothing assigns, which raises AttributeError "
        f"at runtime: {dangling}"
    )


def test_the_guard_detects_a_dangling_attribute():
    """Mutation check: the guard must fail on the shape of the original bug."""
    broken = (
        "class AWSAdapter:\n"
        "    def __init__(self):\n"
        "        self.session = None\n"
        "    def _client(self):\n"
        "        if self._use_simulator:\n"
        "            return None\n"
        "        return self.session\n"
    )
    assigned, read = _self_attribute_usage(broken, "AWSAdapter")
    assert "_use_simulator" in read
    assert "_use_simulator" not in assigned


class _StubSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.clients: Dict[str, Any] = {}

    def client(self, service_name, **kwargs):
        self.clients[service_name] = kwargs
        return f"{service_name}-client"


@pytest.fixture
def constructed_adapter(monkeypatch):
    """An adapter built through the real __init__, not __new__."""
    monkeypatch.setattr(adapter_module, "get_onboarding_scan_profile_name", lambda: None)
    monkeypatch.setattr(adapter_module.boto3, "Session", _StubSession)
    return AWSAdapter(default_region="us-east-1")


def test_sagemaker_client_works_on_a_normally_constructed_adapter(constructed_adapter):
    """The exact production failure: SageMaker checks could not build a client."""
    assert constructed_adapter._sagemaker_client("us-west-2") == "sagemaker-client"


def test_sagemaker_client_falls_back_to_the_adapter_region(constructed_adapter):
    constructed_adapter._sagemaker_client(None)
    assert constructed_adapter.session.clients["sagemaker"]["region_name"] == "us-east-1"


def test_no_simulator_settings_remain_referenced():
    """The simulator settings were deleted; a stray reference is an AttributeError."""
    removed = (
        "aws_use_simulator",
        "aws_simulator_endpoint",
        "aws_simulator_region",
        "use_simulator_pricing",
        "_use_simulator",
    )
    root = Path(adapter_module.__file__).resolve().parents[3]
    offenders = []
    for path in list((root / "app").rglob("*.py")) + list((root / "tests").rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = io.open(path, encoding="utf-8").read()
        offenders += [f"{path.relative_to(root)}: {name}" for name in removed if name in text]
    assert not offenders, f"References to removed simulator settings: {offenders}"
