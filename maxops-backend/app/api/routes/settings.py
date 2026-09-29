"""API routes for OSS account settings."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.database import get_db
from app.schemas.settings import (
    AccountSettingsSummaryResponse,
    OnboardingDraftRequest,
    OnboardingProgressRequest,
    OnboardingRequest,
    SettingsUpdateRequest,
)
from app.services.settings_service import (
    build_account_settings_summary,
    build_settings_catalog,
    clear_account_inventory_state,
    ensure_account_settings,
    get_account_settings,
    get_or_create_onboarding_settings,
    reseed_default_policies,
    save_action_settings,
    save_onboarding_account_draft,
    save_check_settings,
    update_onboarding_progress,
    upsert_account_settings,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _resolve_regions(region: str | None, regions: list[str]) -> list[str]:
    values = [item.strip() for item in regions if isinstance(item, str) and item.strip()]
    if values:
        return values
    if region and region.strip():
        return [region.strip()]
    return []


@router.get("/onboarding", response_model=AccountSettingsSummaryResponse)
def get_onboarding_status(db: Session = Depends(get_db)):
    """Get onboarding status and top-level account settings."""
    settings = get_or_create_onboarding_settings(db)
    return build_account_settings_summary(settings)


@router.patch("/onboarding/progress", response_model=AccountSettingsSummaryResponse)
def save_onboarding_progress(request: OnboardingProgressRequest, db: Session = Depends(get_db)):
    """Persist the current onboarding step and optional page-specific state."""
    try:
        settings = update_onboarding_progress(
            db=db,
            onboarding_step=request.onboarding_step,
            onboarding_data=request.onboarding_data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return build_account_settings_summary(settings)


@router.patch("/onboarding/draft", response_model=AccountSettingsSummaryResponse)
def save_onboarding_draft(request: OnboardingDraftRequest, db: Session = Depends(get_db)):
    """Persist account-settings draft values before onboarding is completed."""
    regions = _resolve_regions(request.region, request.regions)
    settings = save_onboarding_account_draft(
        db=db,
        environment=request.environment,
        account=request.account,
        regions=regions,
    )
    return build_account_settings_summary(settings)


@router.post("/onboarding", response_model=AccountSettingsSummaryResponse)
def complete_onboarding(request: OnboardingRequest, db: Session = Depends(get_db)):
    """Save initial account settings during onboarding."""
    regions = _resolve_regions(request.region, request.regions)
    try:
        settings = upsert_account_settings(
            db=db,
            environment=request.environment,
            account=request.account,
            environment_options=None,
            regions=regions,
            onboarding_completed=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return build_account_settings_summary(settings)


@router.get("", response_model=AccountSettingsSummaryResponse)
def get_settings_summary(db: Session = Depends(get_db)):
    """Get the current top-level account settings."""
    settings = ensure_account_settings(db)
    if not settings:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settings not found")
    return build_account_settings_summary(settings)


@router.get("/catalog")
def get_settings_catalog(db: Session = Depends(get_db)):
    """Return full account settings with resource-grouped check controls."""
    return build_settings_catalog(db)


@router.put("")
def update_settings(payload: SettingsUpdateRequest, db: Session = Depends(get_db)):
    """Save global account settings and per-check saved presets/overrides."""
    regions = _resolve_regions(payload.global_settings.region, payload.global_settings.regions)
    try:
        settings = upsert_account_settings(
            db=db,
            environment=payload.global_settings.environment,
            account=payload.global_settings.account,
            environment_options=payload.global_settings.environment_options,
            regions=regions,
            onboarding_completed=True,
        )
        save_check_settings(
            db=db,
            settings=settings,
            check_payloads=[item.model_dump() for item in payload.check_settings],
        )
        save_action_settings(
            db=db,
            settings=settings,
            action_payloads=[item.model_dump() for item in payload.action_settings],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return build_settings_catalog(db)


@router.get("/feature-flags")
def get_feature_flags():
    """Get feature flags controlled by environment variables."""
    return {
        "test_action": app_settings.test_action,
        "actions_enabled": app_settings.actions_enabled,
    }


class SetupCredentialsRequest(BaseModel):
    """`profile` of None or "" clears the setting."""

    profile: Optional[str] = None


@router.get("/setup-credentials")
def get_setup_credentials():
    """The AWS profile used for privileged setup work, and what's available.

    Scans always run as the read-only scan role. This is a separate profile
    used only by operations that must create or modify AWS resources, such as
    creating a Cost and Usage Report export.
    """
    from app.services.aws_credentials import (
        get_runtime_aws_profile_name,
        get_setup_aws_profile_name,
    )
    from app.services.iam_onboarding_service import list_available_aws_profiles

    try:
        available = list_available_aws_profiles()
    except Exception:  # noqa: BLE001 - the dropdown is a convenience, not a gate
        available = []

    configured = get_setup_aws_profile_name()
    return {
        "profile": configured,
        "scan_profile": get_runtime_aws_profile_name(),
        "using_scan_profile": configured is None,
        "available_profiles": available,
    }


@router.put("/setup-credentials")
def update_setup_credentials(
    request: SetupCredentialsRequest,
    db: Session = Depends(get_db),
):
    """Choose the AWS profile for privileged setup work."""
    from app.services.aws_credentials import set_setup_aws_profile

    try:
        profile = set_setup_aws_profile(db, request.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"profile": profile, "using_scan_profile": profile is None}


class ScanCredentialsRequest(BaseModel):
    """`profile` of None or "" resets to the default credential chain.

    `confirm_account_change` acknowledges that the chosen profile belongs to a
    different AWS account, which wipes everything scanned so far.
    """

    profile: Optional[str] = None
    confirm_account_change: bool = False


def _scan_credentials_state(db: Session) -> Dict[str, Any]:
    """Current scan profile plus what the picker needs to offer alternatives."""
    from app.services.aws_credentials import get_scan_aws_profile_name
    from app.services.iam_onboarding_service import list_available_aws_profiles

    try:
        available = list_available_aws_profiles()
    except Exception:  # noqa: BLE001 - the dropdown is a convenience, not a gate
        available = []

    configured = get_scan_aws_profile_name(db)
    resolved_account = next(
        (
            entry.get("account_id")
            for entry in available
            if entry.get("profile_name") == configured
        ),
        None,
    )
    return {
        "profile": configured,
        "using_default_chain": configured is None,
        "resolved_account_id": resolved_account,
        "available_profiles": available,
    }


@router.get("/scan-credentials")
def get_scan_credentials(db: Session = Depends(get_db)):
    """The AWS profile every scan, check and action runs as.

    Separate from the setup profile: this one is read-only and does the
    routine work, while the setup profile exists only for privileged
    operations such as creating a Cost and Usage Report export.
    """
    state = _scan_credentials_state(db)
    account_settings = get_account_settings(db)
    state["settings_account_id"] = getattr(account_settings, "account", None)
    state["account_mismatch"] = bool(
        state["resolved_account_id"]
        and state["settings_account_id"]
        and state["resolved_account_id"] != state["settings_account_id"]
    )
    return state


def _account_for_profile(profile: Optional[str]) -> Optional[str]:
    """The AWS account a profile resolves to, or None if it cannot be read."""
    from app.services.iam_onboarding_service import list_available_aws_profiles

    try:
        available = list_available_aws_profiles()
    except Exception:  # noqa: BLE001 - treated as "unknown", never as a mismatch
        return None
    return next(
        (
            entry.get("account_id")
            for entry in available
            if entry.get("profile_name") == profile
        ),
        None,
    )


@router.put("/scan-credentials")
def update_scan_credentials(
    request: ScanCredentialsRequest,
    db: Session = Depends(get_db),
):
    """Change or reset the AWS profile used for scanning.

    Switching between roles in the same account is routine. Switching to a
    different account is not: everything already scanned describes resources
    that the new credentials cannot see, so it is refused with 409 until the
    caller confirms, and confirming stops running scans and clears that data.
    """
    from app.services.aws_credentials import set_scan_aws_profile
    from app.services.scan_service import cancel_running_scans, count_running_scans

    account_settings = get_account_settings(db)
    current_account = getattr(account_settings, "account", None)
    new_account = _account_for_profile(request.profile)
    # An unreadable account is not a mismatch -- refusing on "unknown" would
    # block every profile whose STS call timed out.
    account_changing = bool(current_account and new_account and new_account != current_account)

    if account_changing and not request.confirm_account_change:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "account_change_requires_confirmation",
                "current_account_id": current_account,
                "new_account_id": new_account,
                "running_scans": count_running_scans(db),
                "message": (
                    f"Profile '{request.profile}' belongs to account {new_account}, not "
                    f"{current_account}. Switching stops any running scan and clears every "
                    "finding, inventory row and snooze collected from "
                    f"{current_account}. Confirm to start fresh."
                ),
            },
        )

    try:
        set_scan_aws_profile(db, request.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if account_changing:
        stopped = cancel_running_scans(db)
        cleared = clear_account_inventory_state(db)
        reseed_default_policies(db)
        account_settings.account = new_account
        db.add(account_settings)
        db.commit()
        state = get_scan_credentials(db)
        state["account_changed"] = True
        state["stopped_scans"] = stopped
        state["cleared_rows"] = sum(cleared.values())
        return state

    return get_scan_credentials(db)


class CreateSetupRoleRequest(BaseModel):
    """`profile_name` is the existing profile used to create the role."""

    profile_name: Optional[str] = None


@router.get("/setup-credentials/policy")
def get_setup_role_policy():
    """The IAM policy the cost-data setup role needs.

    Offered for download so someone whose own credentials can't create IAM
    resources can hand it to an administrator instead.
    """
    from app.services.iam_onboarding_service import (
        COST_DATA_SETUP_POLICY,
        MAXOPS_COST_DATA_POLICY_NAME,
        MAXOPS_COST_DATA_ROLE_NAME,
    )

    return {
        "role_name": MAXOPS_COST_DATA_ROLE_NAME,
        "policy_name": MAXOPS_COST_DATA_POLICY_NAME,
        "policy": COST_DATA_SETUP_POLICY,
    }


@router.post("/setup-credentials/role")
def create_setup_role(
    request: CreateSetupRoleRequest,
    db: Session = Depends(get_db),
):
    """Create the cost-data setup role and select it for setup work.

    Creates a role separate from the read-only scan role, writes a local AWS
    profile for it, and records that profile as the setup credential so the
    cost-data actions use it immediately.
    """
    from app.services.aws_credentials import set_setup_aws_profile
    from app.services.iam_onboarding_service import (
        IamRoleCreationError,
        create_or_update_cost_data_role,
    )

    try:
        result = create_or_update_cost_data_role(profile_name=request.profile_name)
    except IamRoleCreationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        set_setup_aws_profile(db, result["setup_profile_name"])
        result["selected_as_setup_profile"] = True
    except ValueError:
        # The role exists either way; only the convenience of auto-selecting failed.
        result["selected_as_setup_profile"] = False
    return result
