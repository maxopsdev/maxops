"""The gate that stands between a payload-capture run and real AWS spend.

Every payload generator and resource-creation script calls
`require_capture_gate` before touching AWS. It is the only thing preventing an
ordinary invocation from provisioning billable EC2, RDS, S3, EBS, ElastiCache
and DynamoDB resources, so its refusal behaviour is worth pinning explicitly.
"""

import pytest

from tests_generator.capture_gate import require_capture_gate

pytestmark = [pytest.mark.unit]

ENV_VAR = "MAXOPS_RUN_AWS_INTEGRATION_TESTS"


def test_both_gates_open_permits_capture(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1")

    assert require_capture_gate(True) is None


@pytest.mark.parametrize(
    "env_value,apply,why",
    [
        (None, True, "--apply alone, environment variable unset"),
        ("", True, "--apply alone, environment variable empty"),
        ("0", True, "--apply alone, environment variable not 1"),
        ("true", True, "environment variable must be exactly '1'"),
        ("1", False, "environment variable alone, no --apply"),
        (None, False, "neither gate"),
    ],
)
def test_one_gate_alone_is_refused(monkeypatch, env_value, apply, why):
    """Both gates must be cleared deliberately; either alone is not enough."""
    if env_value is None:
        monkeypatch.delenv(ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(ENV_VAR, env_value)

    with pytest.raises(SystemExit) as excinfo:
        require_capture_gate(apply)

    # The refusal has to name both gates, or the reader cannot act on it.
    message = str(excinfo.value)
    assert ENV_VAR in message, why
    assert "--apply" in message, why


def test_refusal_reports_the_resource_plan(monkeypatch, capsys):
    """A caller that knows what it would create should say so before refusing."""
    monkeypatch.delenv(ENV_VAR, raising=False)

    with pytest.raises(SystemExit) as excinfo:
        require_capture_gate(False, resource_plan="2 EC2 instances, 1 RDS instance")

    assert "2 EC2 instances, 1 RDS instance" in capsys.readouterr().out
    assert "2 EC2 instances, 1 RDS instance" in str(excinfo.value)


def test_every_generator_and_creator_is_behind_the_gate():
    """A generator that forgets the gate can provision resources unguarded."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "tests_generator"
    unguarded = []
    for path in sorted(root.rglob("*_payloads_generator.py")) + sorted(
        root.rglob("*_resource_creation.py")
    ):
        if "require_capture_gate" not in path.read_text(encoding="utf-8"):
            unguarded.append(path.relative_to(root).as_posix())

    assert unguarded == [], f"not behind the capture gate: {unguarded}"
