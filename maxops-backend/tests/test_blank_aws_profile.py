"""A blank AWS_PROFILE must mean no profile, not a profile named "".
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]


def _run_with_env(profile_value: str | None, snippet: str) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter so the import-time fix is exercised."""
    env = dict(os.environ)
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_DEFAULT_PROFILE", None)
    if profile_value is not None:
        env["AWS_PROFILE"] = profile_value
    return subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )


LIST_PROFILES = (
    "import app.config\n"
    "from app.services.iam_onboarding_service import list_available_aws_profiles\n"
    "list_available_aws_profiles()\n"
    "print('OK')\n"
)


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_profile_does_not_break_aws_calls(blank):
    result = _run_with_env(blank, LIST_PROFILES)

    assert "ProfileNotFound" not in result.stderr, result.stderr[-600:]
    assert "OK" in result.stdout, result.stderr[-600:]


def test_blank_profile_is_removed_from_the_environment():
    result = _run_with_env(
        "",
        "import app.config, os\n"
        "print('PRESENT' if 'AWS_PROFILE' in os.environ else 'CLEARED')\n",
    )

    assert "CLEARED" in result.stdout, result.stderr[-600:]


def test_a_real_profile_name_is_left_alone():
    result = _run_with_env(
        "some-profile",
        "import app.config, os\nprint(os.environ.get('AWS_PROFILE'))\n",
    )

    assert "some-profile" in result.stdout, result.stderr[-600:]


def test_env_example_never_assigns_a_blank_aws_profile():
    for path in (REPO_ROOT / ".env.example", BACKEND / "env.example"):
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            name, _, value = stripped.partition("=")
            assert not (name.strip() in {"AWS_PROFILE", "AWS_DEFAULT_PROFILE"} and not value.strip()), (
                f"{path.name}:{lineno} assigns a blank {name.strip()}. Comment the line "
                f"out instead -- a blank value is read as a profile named empty."
            )


MISSING_PROFILE_SNIPPET = """
import app.config
from app.services.iam_onboarding_service import list_available_aws_profiles
rows = list_available_aws_profiles()
print('ROWS', len(rows))
print('ERROR', rows[0]['error'])
"""


def test_a_missing_profile_reports_instead_of_crashing():
    result = _run_with_env("no-such-profile-xyz", MISSING_PROFILE_SNIPPET)

    assert "ROWS 1" in result.stdout, result.stderr[-600:]
    assert "no-such-profile-xyz" in result.stdout
    assert "Traceback" not in result.stderr


def test_example_files_never_name_a_profile_that_may_not_exist():
    for path in (REPO_ROOT / ".env.example", BACKEND / "env.example"):
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            name, _, value = stripped.partition("=")
            assert name.strip() not in {"AWS_PROFILE", "AWS_DEFAULT_PROFILE"}, (
                f"{path.name}:{lineno} sets {name.strip()}={value.strip()!r}. Comment it out: "
                f"a copied example naming a profile the user does not have fails every AWS call."
            )
