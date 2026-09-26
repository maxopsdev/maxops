"""Preflight, cost estimates, job bookkeeping, and the pricing toggle."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.cur import CurSetupJob
from app.models.settings import AccountSettings
from app.services import cur_cost_service, cur_jobs_service as jobs

pytestmark = [pytest.mark.unit]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    CurSetupJob.__table__.create(bind=engine)
    AccountSettings.__table__.create(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def clear_pricing_cache():
    cur_cost_service.reset_cur_pricing_cache()
    yield
    cur_cost_service.reset_cur_pricing_cache()


class _Denied(Exception):
    def __init__(self):
        super().__init__("AccessDenied")
        self.response = {"Error": {"Code": "AccessDenied"}}


# ------------------------------------------------------------- principal arn


@pytest.mark.parametrize(
    "caller_arn,expected",
    [
        # The case that actually bit us: MaxOps runs under an assumed role, and
        # get_caller_identity returns the session ARN, which SimulatePrincipalPolicy
        # rejects with InvalidInput.
        (
            "arn:aws:sts::123456789012:assumed-role/MaxOpsReadOnlyRole/botocore-session-1788705880",
            "arn:aws:iam::123456789012:role/MaxOpsReadOnlyRole",
        ),
        (
            "arn:aws:sts::123456789012:assumed-role/AdminRole/my-session",
            "arn:aws:iam::123456789012:role/AdminRole",
        ),
        # Already an IAM principal: leave it alone.
        ("arn:aws:iam::123456789012:user/alice", "arn:aws:iam::123456789012:user/alice"),
        ("arn:aws:iam::123456789012:role/SomeRole", "arn:aws:iam::123456789012:role/SomeRole"),
        # Anything unrecognised passes through rather than being mangled.
        ("not-an-arn", "not-an-arn"),
    ],
)
def test_iam_principal_arn_resolves_assumed_role_sessions(caller_arn, expected):
    assert jobs.iam_principal_arn(caller_arn) == expected


# -------------------------------------------------------------- simulation


class _SimSession:
    """Records what was asked of IAM so the request itself can be asserted."""

    def __init__(self):
        self.calls = []

    def client(self, name, **_kwargs):
        session = self

        class _Client:
            def get_caller_identity(self):
                return {
                    "Account": "123456789012",
                    "Arn": "arn:aws:sts::123456789012:assumed-role/MaxOpsCostDataRole/s",
                }

            def simulate_principal_policy(self, **kwargs):
                session.calls.append(kwargs)
                return {
                    "EvaluationResults": [
                        {"EvalActionName": a, "EvalDecision": "allowed"}
                        for a in kwargs["ActionNames"]
                    ]
                }

        return _Client()


def test_s3_actions_are_simulated_against_the_report_bucket():
    """Simulating a bucket-scoped grant against "*" reports a false denial,
    which blocked a repair that would actually have succeeded."""
    session = _SimSession()

    jobs._simulate(session, ["s3:CreateBucket", "athena:StartQueryExecution"], "us-east-1")

    by_action = {
        action: call["ResourceArns"]
        for call in session.calls
        for action in call["ActionNames"]
    }
    assert by_action["s3:CreateBucket"] == [
        "arn:aws:s3:::maxops-cur-report-123456789012",
        "arn:aws:s3:::maxops-cur-report-123456789012/*",
    ]
    assert by_action["athena:StartQueryExecution"] == ["*"]


def test_simulation_uses_the_iam_role_arn_not_the_session_arn():
    session = _SimSession()

    jobs._simulate(session, ["athena:StartQueryExecution"], "us-east-1")

    assert session.calls[0]["PolicySourceArn"] == (
        "arn:aws:iam::123456789012:role/MaxOpsCostDataRole"
    )


# ------------------------------------------------------------------ preflight


def test_preflight_reports_missing_permissions(monkeypatch):
    monkeypatch.setattr(jobs, "create_setup_boto3_session", lambda **_: object())
    monkeypatch.setattr(
        jobs,
        "_simulate",
        lambda *_args, **_kwargs: {
            "s3:CreateBucket": "allowed",
            "bcm-data-exports:CreateExport": "implicitDeny",
            "glue:CreateTable": "explicitDeny",
        },
    )

    result = jobs.preflight(jobs.JOB_EXPORT)

    assert result["simulated"] is True
    assert result["can_proceed"] is False
    assert result["denied_actions"] == ["bcm-data-exports:CreateExport", "glue:CreateTable"]
    assert "2 required permission" in result["message"]


def test_preflight_passes_when_everything_is_allowed(monkeypatch):
    monkeypatch.setattr(jobs, "create_setup_boto3_session", lambda **_: object())
    monkeypatch.setattr(
        jobs,
        "_simulate",
        lambda *_args, **_kwargs: {action: "allowed" for action in jobs.REQUIRED_ACTIONS[jobs.JOB_REFRESH]},
    )

    result = jobs.preflight(jobs.JOB_REFRESH)

    assert result["can_proceed"] is True
    assert result["denied_actions"] == []


def test_preflight_allows_proceeding_when_simulation_is_unavailable(monkeypatch):
    """Most profiles cannot run iam:SimulatePrincipalPolicy. That must not block setup."""
    monkeypatch.setattr(jobs, "create_setup_boto3_session", lambda **_: object())

    def denied(*_args, **_kwargs):
        raise _Denied()

    monkeypatch.setattr(jobs, "_simulate", denied)

    result = jobs.preflight(jobs.JOB_EXPORT)

    assert result["simulated"] is False
    assert result["can_proceed"] is True
    assert result["required_actions"] == jobs.REQUIRED_ACTIONS[jobs.JOB_EXPORT]


def test_preflight_blocks_without_credentials(monkeypatch):
    def no_session(**_kwargs):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(jobs, "create_setup_boto3_session", no_session)

    result = jobs.preflight(jobs.JOB_EXPORT)

    assert result["can_proceed"] is False
    assert "No AWS credentials" in result["message"]


def test_preflight_rejects_an_unknown_job_type():
    with pytest.raises(ValueError):
        jobs.preflight("not-a-job")


# ------------------------------------------------------------------ estimate


def test_estimate_counts_one_query_per_planned_month(tmp_path, monkeypatch):
    def no_session(**_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(jobs, "create_setup_boto3_session", no_session)

    estimate = jobs.estimate_refresh(
        cache_root=tmp_path,
        datasets=["resource_monthly"],
        start_month="2026-01",
        end_month="2026-03",
    )

    assert estimate["query_count"] == 3
    assert estimate["months"] == ["2026-01", "2026-02", "2026-03"]
    assert estimate["datasets"] == ["resource_monthly"]


def test_estimate_survives_being_unable_to_size_the_export(tmp_path, monkeypatch):
    def no_session(**_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(jobs, "create_setup_boto3_session", no_session)

    estimate = jobs.estimate_refresh(cache_root=tmp_path, datasets=["resource_monthly"])

    assert estimate["estimated_usd"] is None
    assert "error" in estimate


# ------------------------------------------------------------- job lifecycle


def test_job_moves_from_running_to_completed(db):
    job = jobs.create_job(db, jobs.JOB_REFRESH)
    assert jobs.get_job(db, job.id)["status"] == "running"

    jobs.update_progress(db, job.id, phase="querying", message="Month 1 of 2", current=1, total=2)
    state = jobs.get_job(db, job.id)
    assert state["progress"]["current"] == 1
    assert state["progress"]["total"] == 2

    jobs.complete_job(db, job.id, {"planned": 2})
    state = jobs.get_job(db, job.id)
    assert state["status"] == "completed"
    assert state["result"] == {"planned": 2}
    assert state["completed_at"] is not None


def test_failed_job_keeps_the_error_for_the_page(db):
    job = jobs.create_job(db, jobs.JOB_EXPORT)
    jobs.fail_job(db, job.id, "AccessDenied on bcm-data-exports:CreateExport")

    state = jobs.get_job(db, job.id)
    assert state["status"] == "failed"
    assert "AccessDenied" in state["error"]
    assert state["progress"]["phase"] == "failed"


def test_running_job_of_type_only_matches_running_jobs(db):
    job = jobs.create_job(db, jobs.JOB_EXPORT)
    assert jobs.running_job_of_type(db, jobs.JOB_EXPORT) is not None
    assert jobs.running_job_of_type(db, jobs.JOB_REFRESH) is None

    jobs.complete_job(db, job.id, {})
    assert jobs.running_job_of_type(db, jobs.JOB_EXPORT) is None


def test_missing_job_raises(db):
    with pytest.raises(ValueError):
        jobs.get_job(db, 999)


# ------------------------------------------------------------ pricing toggle


def test_toggle_persists_and_takes_effect_without_a_restart(db, monkeypatch):
    db.add(AccountSettings(environment="prod", account="123456789012", environment_options=[], regions=[]))
    db.commit()

    # Environment default is off.
    monkeypatch.setattr(cur_cost_service.settings, "cur_pricing_enabled", False)
    assert cur_cost_service.is_cur_pricing_enabled() is False

    cur_cost_service.set_cur_pricing_enabled(db, True)

    # No reload, no restart: the cached value changed with the write.
    assert cur_cost_service.is_cur_pricing_enabled() is True
    assert db.query(AccountSettings).first().cur_pricing_enabled is True


def test_stored_preference_beats_the_environment_default(db, monkeypatch):
    db.add(AccountSettings(environment="prod", account="123456789012", environment_options=[], regions=[]))
    db.commit()
    cur_cost_service.set_cur_pricing_enabled(db, False)
    cur_cost_service.reset_cur_pricing_cache()

    monkeypatch.setattr(cur_cost_service.settings, "cur_pricing_enabled", True)
    cur_cost_service.load_cur_pricing_enabled(db)

    assert cur_cost_service.is_cur_pricing_enabled() is False


def test_unset_preference_falls_back_to_the_environment(db, monkeypatch):
    db.add(AccountSettings(environment="prod", account="123456789012", environment_options=[], regions=[]))
    db.commit()

    monkeypatch.setattr(cur_cost_service.settings, "cur_pricing_enabled", True)
    cur_cost_service.load_cur_pricing_enabled(db)

    assert cur_cost_service.is_cur_pricing_enabled() is True


def test_toggle_without_account_settings_is_a_clear_error(db):
    with pytest.raises(ValueError, match="onboarding"):
        cur_cost_service.set_cur_pricing_enabled(db, True)
