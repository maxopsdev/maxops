"""Shared AWS credential selection helpers."""
from __future__ import annotations

from typing import Any, Optional

import boto3
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import SessionLocal
from app.models.settings import AccountSettings


def _scan_profile_from_settings(account_settings: Any) -> Optional[str]:
    """Read the scan profile off an AccountSettings row, or None if unset."""
    onboarding_data: Any = account_settings.onboarding_data if account_settings else None
    if not isinstance(onboarding_data, dict):
        return None

    iam_role_data = onboarding_data.get("iam_role")
    if not isinstance(iam_role_data, dict):
        return None

    profile_name = iam_role_data.get("scan_profile_name")
    if not isinstance(profile_name, str):
        return None

    return profile_name.strip() or None


def _latest_account_settings(db) -> Any:
    return (
        db.query(AccountSettings)
        .order_by(AccountSettings.updated_at.desc(), AccountSettings.id.desc())
        .first()
    )


def get_scan_aws_profile_name(db) -> Optional[str]:
    """Return the scan profile using a caller-supplied session."""
    return _scan_profile_from_settings(_latest_account_settings(db))


def get_onboarding_scan_profile_name() -> Optional[str]:
    """Return the IAM role profile created during onboarding, when available."""
    db = SessionLocal()
    try:
        return _scan_profile_from_settings(_latest_account_settings(db))
    except SQLAlchemyError:
        return None
    finally:
        db.close()


def get_runtime_aws_profile_name() -> Optional[str]:
    """Resolve the AWS profile MaxOps should use for read-only runtime scans."""
    return get_onboarding_scan_profile_name() or settings.aws_profile


def create_runtime_boto3_session(region_name: Optional[str] = None) -> boto3.Session:
    """Create a boto3 session using the active MaxOps runtime credential source."""
    selected_region = region_name or settings.aws_region
    profile_name = get_runtime_aws_profile_name()
    if profile_name:
        return boto3.Session(profile_name=profile_name, region_name=selected_region)

    session_kwargs: dict[str, Any] = {"region_name": selected_region}
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        session_kwargs.update(
            {
                "aws_access_key_id": settings.aws_access_key_id,
                "aws_secret_access_key": settings.aws_secret_access_key,
            }
        )
    return boto3.Session(**session_kwargs)


# --------------------------------------------------------- setup credentials
#
# Scanning runs as a deliberately read-only role. Some one-time administrative
# work — creating a Cost and Usage Report export, running the Athena queries
# that summarise it — needs permissions that role does not and should not
# have. Rather than widening the scan role, those operations use a separate
# profile configured here.
#
# Only explicitly privileged operations may use this. Scans, checks and
# actions must keep using create_runtime_boto3_session.


def get_setup_aws_profile_name() -> Optional[str]:
    """The AWS profile chosen for privileged setup work, if one is configured."""
    db = SessionLocal()
    try:
        account_settings = (
            db.query(AccountSettings)
            .order_by(AccountSettings.updated_at.desc(), AccountSettings.id.desc())
            .first()
        )
        stored = getattr(account_settings, "setup_aws_profile", None) if account_settings else None
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
    except SQLAlchemyError:
        pass
    finally:
        db.close()

    configured = getattr(settings, "setup_aws_profile", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return None


def create_setup_boto3_session(region_name: Optional[str] = None) -> boto3.Session:
    """Session for privileged setup work.

    Falls back to the runtime credentials when no setup profile is configured,
    so behaviour is unchanged for anyone who has not set one.
    """
    profile_name = get_setup_aws_profile_name()
    if not profile_name:
        return create_runtime_boto3_session(region_name=region_name)
    return boto3.Session(
        profile_name=profile_name,
        region_name=region_name or settings.aws_region,
    )


def set_scan_aws_profile(db, profile_name: Optional[str]) -> Optional[str]:
    """Persist the scan profile. Passing None or "" clears it.

    Clearing falls back to MAXOPS_AWS_PROFILE and then the default credential
    chain, matching a machine that never ran onboarding.
    """
    account_settings = _latest_account_settings(db)
    if account_settings is None:
        raise ValueError("No account settings exist yet; complete onboarding first.")

    normalized = (profile_name or "").strip() or None
    onboarding_data = (
        account_settings.onboarding_data
        if isinstance(account_settings.onboarding_data, dict)
        else {}
    )
    iam_role = (
        onboarding_data.get("iam_role")
        if isinstance(onboarding_data.get("iam_role"), dict)
        else {}
    )

    # Reassign rather than mutate: SQLAlchemy does not track in-place edits to
    # a plain JSON column, so a nested write would be silently dropped.
    account_settings.onboarding_data = {
        **onboarding_data,
        "iam_role": {**iam_role, "scan_profile_name": normalized},
    }
    db.commit()
    return normalized


def set_setup_aws_profile(db, profile_name: Optional[str]) -> Optional[str]:
    """Persist the setup profile. Passing None or "" clears it."""
    account_settings = (
        db.query(AccountSettings)
        .order_by(AccountSettings.updated_at.desc(), AccountSettings.id.desc())
        .first()
    )
    if account_settings is None:
        raise ValueError("No account settings exist yet; complete onboarding first.")

    normalized = (profile_name or "").strip() or None
    account_settings.setup_aws_profile = normalized
    db.commit()
    return normalized
