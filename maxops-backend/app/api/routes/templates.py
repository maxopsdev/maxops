"""Policy template API routes."""
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from app.services.policy_templates import get_template, list_templates

router = APIRouter(prefix="/policy-templates", tags=["policy-templates"])


@router.get("")
def list_policy_templates(category: Optional[str] = None):
    """List all available policy templates."""
    templates = list_templates(category=category)
    return {"templates": templates}


@router.get("/{template_id}")
def get_policy_template(template_id: str):
    """Get a policy template by ID."""
    template = get_template(template_id)
    if not template:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template {template_id} not found"
        )
    return template

