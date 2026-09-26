"""Status probes behind the cost-data setup page.

The page renders whatever these return, so the important behaviour is that a
missing permission or a broken account degrades one step rather than the page.
"""

import pytest

from app.services import cur_setup_service as svc
from pricing.cur.cache import parquet_path
from pricing.cur.months import BillingMonth

pytestmark = [pytest.mark.unit]


class _ClientError(Exception):
    """Mimics botocore's ClientError shape closely enough for the probes."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeSession:
    def __init__(self, **clients):
        self._clients = clients

    def client(self, name, **_kwargs):
        client = self._clients.get(name)
        if client is None:
            raise AssertionError(f"probe asked for an unexpected client: {name}")
        if isinstance(client, Exception):
            raise client
        return client


class _Exports:
    def __init__(self, pages):
        self._pages = pages
        self.calls = 0

    def list_exports(self, **kwargs):
        page = self._pages[self.calls]
        self.calls += 1
        return page


class _S3:
    def __init__(self, key_count=0, error=None):
        self._key_count = key_count
        self._error = error

    def list_objects_v2(self, **_kwargs):
        if self._error:
            raise self._error
        return {"KeyCount": self._key_count}


# ------------------------------------------------------------------- export


def test_export_step_is_done_when_the_export_exists():
    exports = _Exports([{"Exports": [{"ExportName": svc.DEFAULT_EXPORT_NAME, "ExportArn": "arn:x"}]}])
    step = svc.probe_export(_FakeSession(**{"bcm-data-exports": exports}), "us-east-1")

    assert step["state"] == svc.DONE
    assert step["export_arn"] == "arn:x"


def test_export_step_pages_through_results():
    exports = _Exports(
        [
            {"Exports": [{"ExportName": "something-else"}], "NextToken": "t1"},
            {"Exports": [{"ExportName": svc.DEFAULT_EXPORT_NAME}]},
        ]
    )
    step = svc.probe_export(_FakeSession(**{"bcm-data-exports": exports}), "us-east-1")

    assert step["state"] == svc.DONE
    assert exports.calls == 2


def test_export_step_asks_for_action_when_absent():
    exports = _Exports([{"Exports": [{"ExportName": "unrelated-export"}]}])
    step = svc.probe_export(_FakeSession(**{"bcm-data-exports": exports}), "us-east-1")

    assert step["state"] == svc.ACTION_REQUIRED


def test_denied_permission_reports_unknown_not_failure():
    session = _FakeSession(**{"bcm-data-exports": _ClientError("AccessDeniedException")})
    step = svc.probe_export(session, "us-east-1")

    assert step["state"] == svc.UNKNOWN


@pytest.mark.parametrize(
    "message,expected",
    [
        (
            "An error occurred (AccessDeniedException) when calling the StartQueryExecution "
            "operation: You are not authorized to perform: athena:StartQueryExecution on the resource.",
            "athena:StartQueryExecution",
        ),
        (
            "User: arn:aws:sts::1:assumed-role/R/s is not authorized to perform: "
            "bcm-data-exports:ListExports because no identity-based policy allows it",
            "bcm-data-exports:ListExports",
        ),
        ("some unrelated failure", None),
    ],
)
def test_denied_action_is_extracted_from_the_aws_message(message, expected):
    assert svc.denied_action(Exception(message)) == expected


def test_denial_says_what_to_do_about_it(monkeypatch):
    """A refusal with no remedy leaves the reader stuck, which is what happened."""
    monkeypatch.setattr(svc, "get_setup_aws_profile_name", lambda: None)
    error = _ClientError("AccessDeniedException")
    error.args = (
        "User: arn:aws:sts::1:assumed-role/MaxOpsReadOnlyRole/s is not authorized to "
        "perform: bcm-data-exports:ListExports",
    )
    step = svc.probe_export(_FakeSession(**{"bcm-data-exports": error}), "us-east-1")

    assert step["denied_action"] == "bcm-data-exports:ListExports"
    assert "bcm-data-exports:ListExports" in step["detail"]
    assert "Setup credentials" in step["remedy"]
    assert "read-only scan role" in step["remedy"]


def test_denial_names_the_configured_profile_when_one_is_set(monkeypatch):
    monkeypatch.setattr(svc, "get_setup_aws_profile_name", lambda: "maxops")
    step = svc.probe_export(
        _FakeSession(**{"bcm-data-exports": _ClientError("AccessDeniedException")}), "us-east-1"
    )

    assert "'maxops' profile" in step["remedy"]
    assert step["setup_profile"] == "maxops"


def test_unexpected_failure_degrades_to_unknown():
    session = _FakeSession(**{"bcm-data-exports": RuntimeError("network on fire")})
    step = svc.probe_export(session, "us-east-1")

    assert step["state"] == svc.UNKNOWN
    assert "network on fire" in step["error"]


# ----------------------------------------------------------------- delivery


def test_delivery_is_done_once_a_key_exists():
    step = svc.probe_delivery(_FakeSession(s3=_S3(key_count=1)), "us-east-1", "bucket", "curv2")

    assert step["state"] == svc.DONE
    assert step["prefix"] == "curv2/maxops-curv2-daily"


def test_delivery_waits_rather_than_erroring_before_the_first_file():
    step = svc.probe_delivery(_FakeSession(s3=_S3(key_count=0)), "us-east-1", "bucket", "curv2")

    assert step["state"] == svc.WAITING
    assert "24 hours" in step["detail"]


def test_delivery_cannot_be_checked_without_a_bucket():
    step = svc.probe_delivery(_FakeSession(s3=_S3()), "us-east-1", None, "curv2")

    assert step["state"] == svc.UNKNOWN


# -------------------------------------------------------------- athena table


class _Glue:
    def __init__(self, location=None, error=None):
        self._location = location
        self._error = error

    def get_table(self, **_kwargs):
        if self._error:
            raise self._error
        return {"Table": {"StorageDescriptor": {"Location": self._location}}}


EXPECTED_LOCATION = "s3://maxops-cur-report-123456789012/curv2/maxops-curv2-daily/data/"


def test_table_pointing_at_the_managed_export_is_healthy():
    session = _FakeSession(glue=_Glue(location=EXPECTED_LOCATION))
    step = svc.probe_athena_table(session, "us-east-1", "maxops-cur-report-123456789012")

    assert step["state"] == svc.DONE


def test_trailing_slash_difference_is_not_treated_as_a_mismatch():
    session = _FakeSession(glue=_Glue(location=EXPECTED_LOCATION.rstrip("/")))
    step = svc.probe_athena_table(session, "us-east-1", "maxops-cur-report-123456789012")

    assert step["state"] == svc.DONE


def test_table_left_over_from_another_export_is_an_error_not_a_pass():
    """The real failure: the table existed, so this reported healthy, while
    Athena queried a different bucket and died on a permission error."""
    session = _FakeSession(
        glue=_Glue(location="s3://maxops-cur-bucket/cur/maxops-cur-daily-all-columns/data")
    )
    step = svc.probe_athena_table(session, "us-east-1", "maxops-cur-report-123456789012")

    assert step["state"] == svc.ERROR
    assert "maxops-cur-bucket" in step["detail"]
    # The remedy must name the button that actually exists, not a different one.
    assert "Repair export" in step["remedy"]


def test_table_location_is_not_checked_without_a_known_bucket():
    session = _FakeSession(glue=_Glue(location="s3://anything/at/all"))
    step = svc.probe_athena_table(session, "us-east-1", None)

    assert step["state"] == svc.DONE


# -------------------------------------------------------------------- cache


def test_cache_step_reports_months_present(tmp_path):
    import pandas as pd

    path = parquet_path(tmp_path, "resource_monthly", BillingMonth(2026, 5))
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"line_item_resource_id": "i-1", "net_amortized_cost": 1.0}]).to_parquet(path, index=False)

    step = svc.probe_cache(tmp_path)

    assert step["state"] == svc.DONE
    assert step["months"] == ["2026-05"]


def test_cache_step_asks_for_action_when_empty(tmp_path):
    step = svc.probe_cache(tmp_path)

    assert step["state"] == svc.ACTION_REQUIRED


# ------------------------------------------------------------------ pricing


def test_pricing_step_asks_for_action_when_disabled(monkeypatch):
    monkeypatch.setattr(
        svc.cur_cost_lookup,
        "status",
        lambda: {"enabled": False, "billing_month": None, "resource_count": 0, "cache_root": "x"},
    )
    step = svc.probe_pricing()

    assert step["state"] == svc.ACTION_REQUIRED
    assert "list prices" in step["detail"]


def test_pricing_step_waits_when_enabled_but_empty(monkeypatch):
    monkeypatch.setattr(
        svc.cur_cost_lookup,
        "status",
        lambda: {"enabled": True, "billing_month": None, "resource_count": 0, "cache_root": "x"},
    )

    assert svc.probe_pricing()["state"] == svc.WAITING


def test_pricing_step_is_done_when_resources_are_priced(monkeypatch):
    monkeypatch.setattr(
        svc.cur_cost_lookup,
        "status",
        lambda: {"enabled": True, "billing_month": "2026-05", "resource_count": 42, "cache_root": "x"},
    )
    step = svc.probe_pricing()

    assert step["state"] == svc.DONE
    assert "42 resources" in step["detail"]
    assert "2026-05" in step["detail"]


# ---------------------------------------------------------------- aggregate


def _status_with(monkeypatch, tmp_path, *, export_step, table_step):
    monkeypatch.setattr(svc, "create_setup_boto3_session", lambda **_: object())
    monkeypatch.setattr(svc, "_resolve_identity", lambda *_a, **_k: ("123456789012", None))
    monkeypatch.setattr(svc, "probe_export", lambda *_a, **_k: export_step)
    monkeypatch.setattr(svc, "probe_delivery", lambda *_a, **_k: _fake_step("delivery", svc.DONE))
    monkeypatch.setattr(svc, "probe_athena_table", lambda *_a, **_k: table_step)
    return svc.get_setup_status(cache_root=tmp_path)


def _fake_step(step_id, state, **extra):
    return {"id": step_id, "label": step_id, "state": state, "detail": "d", **extra}


def test_a_stale_table_makes_the_export_step_repairable(monkeypatch, tmp_path):
    """Otherwise the page shows a green export with no way to fix the table."""
    status = _status_with(
        monkeypatch,
        tmp_path,
        export_step=_fake_step(svc.STEP_EXPORT, svc.DONE),
        table_step=_fake_step(
            "athena_table",
            svc.ERROR,
            remedy="Use 'Repair export' on the first step to point it at the current one.",
            location="s3://old/place",
            expected_location="s3://new/place/data/",
        ),
    )

    export_step = status["steps"][0]
    assert export_step["state"] == svc.ERROR
    assert export_step["repairable"] is True
    assert "Repair export" in export_step["remedy"]
    assert export_step["expected_location"] == "s3://new/place/data/"


def test_a_healthy_table_leaves_the_export_step_alone(monkeypatch, tmp_path):
    status = _status_with(
        monkeypatch,
        tmp_path,
        export_step=_fake_step(svc.STEP_EXPORT, svc.DONE),
        table_step=_fake_step("athena_table", svc.DONE),
    )

    assert status["steps"][0]["state"] == svc.DONE
    assert "repairable" not in status["steps"][0]


def test_a_missing_export_is_not_relabelled_as_a_repair(monkeypatch, tmp_path):
    """Nothing exists yet, so this is a create, not a repair."""
    status = _status_with(
        monkeypatch,
        tmp_path,
        export_step=_fake_step(svc.STEP_EXPORT, svc.ACTION_REQUIRED),
        table_step=_fake_step("athena_table", svc.ERROR, remedy="x"),
    )

    assert status["steps"][0]["state"] == svc.ACTION_REQUIRED


def test_missing_credentials_still_returns_a_renderable_page(monkeypatch, tmp_path):
    def no_session(**_kwargs):
        raise RuntimeError("no credentials configured")

    monkeypatch.setattr(svc, "create_setup_boto3_session", no_session)

    status = svc.get_setup_status(cache_root=tmp_path)

    assert status["aws_available"] is False
    # Every step is still present so the page can render all four rows.
    assert [step["id"] for step in status["steps"]] == [
        svc.STEP_EXPORT,
        svc.STEP_DELIVERY,
        svc.STEP_CACHE,
        svc.STEP_PRICING,
    ]
    # The local half is still genuinely evaluated rather than blanked out.
    assert status["steps"][2]["state"] == svc.ACTION_REQUIRED
