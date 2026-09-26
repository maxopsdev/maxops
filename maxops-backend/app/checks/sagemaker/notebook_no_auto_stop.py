"""SageMaker notebook lifecycle auto-stop configuration check."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.checks.registry import CheckMetadata, check_registry
from app.checks.sagemaker.common import lifecycle_auto_stop_match, resource_metadata


DEFAULT_AUTO_STOP_PATTERNS = (
    "autostop",
    "auto-stop",
    "idle",
    "stop-notebook-instance",
)


def _lifecycle_content(aws_adapter: Any, notebook: Dict[str, Any], region: Optional[str]) -> Iterable[Any]:
    """Fetch lifecycle OnStart entries, or raise so Describe failures are skipped."""
    metadata = resource_metadata(notebook)
    lifecycle_name = metadata.get("lifecycle_config_name")
    if not lifecycle_name:
        return []
    if metadata.get("lifecycle_on_start") is not None:
        return metadata["lifecycle_on_start"]
    if hasattr(aws_adapter, "get_sagemaker_lifecycle_config"):
        response = aws_adapter.get_sagemaker_lifecycle_config(lifecycle_name, region)
        return response.get("OnStart") or []
    raise RuntimeError("lifecycle configuration contents unavailable")


def check_sagemaker_notebook_no_auto_stop(
    aws_adapter: Any,
    auto_stop_patterns: Optional[List[str]] = None,
    region: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Find InService notebooks without an attached matching auto-stop script."""
    resources = aws_adapter.get_resources("sagemaker_notebook", {"status": "InService"}, region)
    patterns = auto_stop_patterns or list(DEFAULT_AUTO_STOP_PATTERNS)
    flagged: List[Dict[str, Any]] = []
    for notebook in resources:
        metadata = resource_metadata(notebook)
        status = str(metadata.get("notebook_status") or notebook.get("status") or notebook.get("state") or "")
        if status.casefold() != "inservice":
            continue
        try:
            entries = _lifecycle_content(aws_adapter, notebook, notebook.get("region") or region)
        except Exception as exc:
            print(f"[SAGEMAKER_NOTEBOOK_NO_AUTO_STOP] Skipping {notebook.get('resource_id')}: discovery_unavailable ({exc})")
            continue
        matched = None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            matched = lifecycle_auto_stop_match(entry.get("Content"), patterns)
            if matched:
                break
        metadata["lifecycle_config_has_auto_stop"] = matched is not None
        metadata["lifecycle_config_auto_stop_pattern"] = matched
        if matched:
            continue
        metadata.update(
            {
                "recommended_action": "attach_auto_stop_lifecycle_config",
                "recommended_actions": ["attach_auto_stop_lifecycle_config"],
                "lifecycle_config_name": metadata.get("lifecycle_config_name"),
                "potential_savings_monthly": None,
                "savings_disclosure": "Behavioural fix; notebook volume charges continue and no instance price savings are estimated.",
                "check_reason": "Notebook has no lifecycle auto-stop configuration.",
            }
        )
        notebook["metadata"] = metadata
        flagged.append(notebook)
    return flagged


check_registry.register(CheckMetadata(
    check_id="sagemaker_notebook_no_auto_stop",
    name="SageMaker Notebook Without Auto-Stop",
    description="Finds InService notebooks without an auto-stop lifecycle script",
    resource_type="sagemaker",
    check_function=check_sagemaker_notebook_no_auto_stop,
    default_action="attach_auto_stop_lifecycle_config",
    parameters={"auto_stop_patterns": list(DEFAULT_AUTO_STOP_PATTERNS), "region": None},
))

