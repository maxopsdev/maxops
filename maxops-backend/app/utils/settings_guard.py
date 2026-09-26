from typing import Tuple

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.settings import AccountSettings
from app.services.settings_service import ensure_account_settings, get_execution_regions


def _normalize_value(value: object) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def get_settings_scope(settings: AccountSettings) -> Tuple[str | None, str | None]:
    account = _normalize_value(getattr(settings, "account", None))
    region = None
    for candidate in get_execution_regions(settings):
        region = _normalize_value(candidate)
        if region:
            break
    return account, region


def get_settings_regions(settings: AccountSettings) -> list[str]:
    return get_execution_regions(settings)


def require_account_region(db: Session) -> AccountSettings:
    settings = ensure_account_settings(db)
    if not settings:
        raise ValueError("Onboarding not completed. Please complete onboarding first.")

    account, region = get_settings_scope(settings)

    missing: list[str] = []
    if not account:
        missing.append("account")
    if not region:
        missing.append("region")

    if missing:
        raise ValueError(f"Missing required settings: {', '.join(missing)}")

    app_settings.aws_region = region

    return settings
