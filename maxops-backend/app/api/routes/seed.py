"""Database seeding API routes."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.utils.seed_data import seed_policies, seed_executions, seed_cost_savings

router = APIRouter(prefix="/seed", tags=["seed"])


@router.post("/policies")
def seed_default_policies(
    force: bool = False,
    db: Session = Depends(get_db)
):
    """
    Manually trigger seeding of default policies.
    
    Args:
        force: If True, re-seed even if policies exist (will delete existing)
    """
    try:
        count = seed_policies(db, force=force)
        return {
            "message": f"Successfully seeded {count} default policies",
            "count": count,
            "forced": force
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error seeding policies: {str(e)}"
        )


@router.post("/executions")
def seed_dummy_executions(
    force: bool = False,
    db: Session = Depends(get_db)
):
    """
    Manually trigger seeding of dummy execution data for testing.
    
    Args:
        force: If True, re-seed even if executions exist (will delete existing)
    """
    try:
        count = seed_executions(db, force=force)
        return {
            "message": f"Successfully seeded {count} dummy executions",
            "count": count,
            "forced": force
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error seeding executions: {str(e)}"
        )


@router.post("/cost-savings")
def seed_dummy_cost_savings(
    force: bool = False,
    db: Session = Depends(get_db)
):
    """
    Manually trigger seeding of dummy cost savings data for testing.
    
    Args:
        force: If True, re-seed even if cost savings exist (will delete existing)
    """
    try:
        count = seed_cost_savings(db, force=force)
        return {
            "message": f"Successfully seeded {count} cost savings records",
            "count": count,
            "forced": force
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error seeding cost savings: {str(e)}"
        )

