"""The published check catalog must describe the checks that actually register.

docs/maxops_checks_catalog.csv is what README, the docs site and maxops.dev
quote their headline numbers from. It drifted once already: a branch added
ec2_gpu_underutilized and removed ec2_rightsize_candidate, and only the removal
reached the CSV, so the catalog claimed one check fewer than shipped.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import app.checks  # noqa: F401  -- importing registers every check
from app.checks.registry import check_registry

pytestmark = [pytest.mark.unit]

CATALOG = Path(__file__).resolve().parents[1] / "docs" / "maxops_checks_catalog.csv"


def _catalog_rows():
    with CATALOG.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_catalog_lists_every_registered_check_and_nothing_else():
    registered = {check.check_id for check in check_registry.list_checks()}
    catalogued = {row["Check ID"].strip() for row in _catalog_rows()}

    assert registered - catalogued == set(), "checks registered but absent from the catalog"
    assert catalogued - registered == set(), "catalog rows for checks that no longer register"


def test_catalog_has_one_row_per_check():
    ids = [row["Check ID"].strip() for row in _catalog_rows()]

    duplicates = {check_id for check_id in ids if ids.count(check_id) > 1}
    assert duplicates == set(), f"duplicate catalog rows: {sorted(duplicates)}"
    assert len(ids) == len(check_registry.list_checks())


def test_catalog_resource_type_matches_the_registered_one():
    by_id = {check.check_id: check for check in check_registry.list_checks()}

    mismatched = {
        row["Check ID"].strip(): (row["Resource Type"].strip(), by_id[row["Check ID"].strip()].resource_type)
        for row in _catalog_rows()
        if row["Check ID"].strip() in by_id
        and row["Resource Type"].strip() != by_id[row["Check ID"].strip()].resource_type
    }

    assert mismatched == {}, f"catalog resource types disagree with the registry: {mismatched}"
