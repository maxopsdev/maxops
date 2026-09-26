"""Shared real-AWS action recording helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError


class RecordingClient:
    def __init__(self, service: str, client: Any, calls: List[Dict[str, Any]]):
        self._service = service
        self._client = client
        self._calls = calls

    def __getattr__(self, operation: str):
        target = getattr(self._client, operation)
        if operation == "exceptions":
            return target

        def invoke(**kwargs: Any):
            try:
                response = target(**kwargs)
            except ClientError as exc:
                self._calls.append(
                    {
                        "service": self._service,
                        "operation": operation,
                        "kwargs": deepcopy(kwargs),
                        "error": deepcopy(exc.response),
                    }
                )
                raise
            self._calls.append(
                {
                    "service": self._service,
                    "operation": operation,
                    "kwargs": deepcopy(kwargs),
                    "response": deepcopy(response),
                }
            )
            return response

        return invoke

    def get_waiter(self, waiter_name: str):
        return self._client.get_waiter(waiter_name)

    def get_paginator(self, operation_name: str):
        return RecordingPaginator(
            self._service,
            operation_name,
            self._client.get_paginator(operation_name),
            self._calls,
        )


class RecordingPaginator:
    def __init__(
        self,
        service: str,
        operation: str,
        paginator: Any,
        calls: List[Dict[str, Any]],
    ):
        self._service = service
        self._operation = operation
        self._paginator = paginator
        self._calls = calls

    def paginate(self, **kwargs: Any):
        try:
            pages = list(self._paginator.paginate(**kwargs))
        except ClientError as exc:
            self._calls.append(
                {
                    "service": self._service,
                    "operation": self._operation,
                    "kwargs": deepcopy(kwargs),
                    "error": deepcopy(exc.response),
                }
            )
            raise
        self._calls.append(
            {
                "service": self._service,
                "operation": self._operation,
                "kwargs": deepcopy(kwargs),
                "response": {"Pages": deepcopy(pages)},
            }
        )
        return iter(pages)


class RecordingSession:
    def __init__(self, session: Any, calls: List[Dict[str, Any]]):
        self._session = session
        self._calls = calls
        self._clients: Dict[Tuple[str, Optional[str]], RecordingClient] = {}

    def client(self, service_name: str, region_name: Optional[str] = None, endpoint_url: Optional[str] = None):
        key = (service_name, region_name)
        if key not in self._clients:
            client = self._session.client(service_name, region_name=region_name, endpoint_url=endpoint_url)
            self._clients[key] = RecordingClient(service_name, client, self._calls)
        return self._clients[key]


class NullDB:
    def commit(self) -> None:
        return None


def model_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError(f"Unsupported action response type: {type(value)!r}")


def action_payload_complete(
    payload_root: Path,
    action_key: str,
    action_config: Dict[str, Any],
) -> bool:
    scenario_dir = payload_root / "actions" / action_key / action_config["scenario"]
    metadata_path = scenario_dir / "capture_metadata.json"
    if not metadata_path.is_file():
        return False
    try:
        import json

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    calls = metadata.get("calls", [])
    expected = action_config.get("expected_calls", [])
    if [
        (call.get("service"), call.get("operation"))
        for call in calls
    ] != [
        (call.get("service"), call.get("operation"))
        for call in expected
    ]:
        return False
    return bool(calls) and all(
        (scenario_dir / call.get("response_file", "")).is_file()
        for call in calls
    )


def execute_recorded_action(
    session: Any,
    action_key: str,
    action_config: Dict[str, Any],
    account_id: str,
    region: str,
    resource_id: str,
    check: Any,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    from app.actions import action_registry
    from app.actions.base import ActionExecutionContext
    from app.schemas.check import CheckActionRequest

    calls: List[Dict[str, Any]] = []
    payload = CheckActionRequest(
        action=action_key,
        account_id=account_id,
        region=region,
        resource_id=resource_id,
        parameters=deepcopy(action_config.get("parameters", {})),
    )
    action_execution = SimpleNamespace(
        status="running",
        message=None,
        details_json=None,
        error_message=None,
    )
    context = ActionExecutionContext(
        check_id=action_config["check_id"],
        action_key=action_key,
        payload=payload,
        check=check,
        aws_adapter=SimpleNamespace(session=RecordingSession(session, calls)),
        db=NullDB(),
        action_execution=action_execution,
    )
    response = action_registry.execute(context)
    if response is None:
        raise RuntimeError(f"Action {action_key} is not registered")
    response_dict = model_dict(response)
    if response_dict.get("status") != action_config["expected_status"]:
        raise RuntimeError(
            f"Action {action_key} returned status {response_dict.get('status')!r}; "
            f"expected {action_config['expected_status']!r}"
        )
    expected_message = action_config.get("expected_message")
    expected_prefix = action_config.get("expected_message_prefix")
    if expected_message and response_dict.get("message") != expected_message:
        raise RuntimeError(f"Action {action_key} returned an unexpected message")
    if expected_prefix and not response_dict.get("message", "").startswith(expected_prefix):
        raise RuntimeError(f"Action {action_key} returned an unexpected message")

    observed_calls = [
        (call["service"], call["operation"])
        for call in calls
    ]
    expected_calls = [
        (call["service"], call["operation"])
        for call in action_config.get("expected_calls", [])
    ]
    if observed_calls != expected_calls:
        raise RuntimeError(
            f"Action {action_key} AWS calls did not match the manifest: "
            f"expected {expected_calls!r}, observed {observed_calls!r}"
        )
    return response_dict, calls


def write_action_fixture(
    payload_root: Path,
    action_key: str,
    action_config: Dict[str, Any],
    response: Dict[str, Any],
    calls: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    sanitize: Callable[[Dict[str, Any]], Dict[str, Any]],
    write_json: Callable[[Path, Dict[str, Any]], None],
) -> None:
    scenario_dir = payload_root / "actions" / action_key / action_config["scenario"]
    metadata_calls = []
    for index, call in enumerate(calls, start=1):
        suffix = "error" if "error" in call else "response"
        response_file = (
            f"{index:02d}_{call['service'].replace('-', '_')}__"
            f"{call['operation']}__{suffix}.json"
        )
        payload = call["error"] if "error" in call else call["response"]
        write_json(scenario_dir / response_file, sanitize(payload))
        metadata_calls.append(
            {
                "service": call["service"],
                "operation": call["operation"],
                "kwargs": sanitize(call["kwargs"]),
                "response_file": response_file,
                "raises_client_error": "error" in call,
            }
        )
    write_json(
        scenario_dir / "capture_metadata.json",
        {
            **metadata,
            "action_key": action_key,
            "scenario": action_config["scenario"],
            "capture_source": "aws",
            "expected_status": action_config["expected_status"],
            "expected_message": response["message"],
            "handler_response": sanitize(response),
            "calls": metadata_calls,
        },
    )
