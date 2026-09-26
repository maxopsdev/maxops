"""Base context and interfaces for action handlers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.action_execution import ActionExecution
from app.schemas.check import CheckActionRequest


@dataclass
class ActionExecutionContext:
    """Runtime context provided to action handlers."""

    check_id: str
    action_key: str
    payload: CheckActionRequest
    check: Any
    aws_adapter: Any
    db: Session
    action_execution: ActionExecution

