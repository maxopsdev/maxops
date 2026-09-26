"""No real AWS account IDs anywhere in the tree.

This repository is the source of truth for a public mirror, so anything
committed here can end up published. Account IDs are not credentials, but they
are reconnaissance material: they tie a real account to the exact resource
inventory sitting in the payload fixtures.

An earlier manual sanitization pass missed nine synthetic_data config files and
the copy of the id inside a scrub guard, which is why this is a test rather
than a checklist item.

Two conventions this enforces, both load-bearing:
  * 123456789012 is the *capture-side* placeholder -- real captures are
    rewritten to it.
  * 999999999999 is the *synthetic-side* account. The synthetic generators
    substitute the former into the latter, and their own leak guards assert
    the capture placeholder never survives into generated output, so these two
    must stay distinct.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every 12-digit id that is allowed to appear. Add to this only after
# confirming the value is not a real account.
PLACEHOLDERS = {
    "123456789012",   # capture-side placeholder
    "999999999999",   # synthetic dataset account
    "111111111111",
    "222222222222",
    "210987654321",
    "000000000000",   # explicit fallback in the SageMaker generator
}

# A 12-digit run that is not part of a longer hex string. The negative
# lookarounds keep UUID segments out: an x-amzn-requestid such as
# 09dde570-6cd6-4f20-8044-985086358988 ends in twelve digits and is not an
# account id. A bare \b pattern flags those and sends you chasing ghosts.
ACCOUNT_ID = re.compile(r"(?<![0-9A-Fa-f-])[0-9]{12}(?![0-9A-Fa-f-])")

BINARY_SUFFIXES = {
    ".db", ".xz", ".sha256", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".lock", ".svg",
}

# Raw AWS Pricing API dumps: tier boundaries there are 12-digit quantities,
# not identifiers, and no account id appears in them. (Naming one here would
# trip this very test -- the same trap as a scrub guard that spells out the id
# it is guarding against.)
SKIP_PREFIXES = ("maxops-backend/tests/payloads/s3_optimizer/pricing_api/",)


def _tracked_files():
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.split()


def test_no_unrecognised_twelve_digit_account_ids():
    offenders: dict[str, list[str]] = {}

    for rel in _tracked_files():
        if rel.startswith(SKIP_PREFIXES):
            continue
        path = REPO_ROOT / rel
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for candidate in ACCOUNT_ID.findall(text):
            if candidate not in PLACEHOLDERS:
                offenders.setdefault(candidate, []).append(rel)

    assert offenders == {}, (
        "Unrecognised 12-digit ids found. If these are real account numbers, "
        "replace them with 123456789012; if they are placeholders or "
        "quantities, add them to PLACEHOLDERS with a note:\n"
        + "\n".join(f"  {k}: {v[:3]}" for k, v in sorted(offenders.items()))
    )


def test_capture_and_synthetic_placeholders_stay_distinct():
    # The synthetic generators' own leak guards depend on this.
    assert "123456789012" != "999999999999"
    assert {"123456789012", "999999999999"} <= PLACEHOLDERS
