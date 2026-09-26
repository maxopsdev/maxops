"""API routes for inventory-backed scans."""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.services.scan_service import create_scan_execution, get_scan_status, mark_scan_failed, run_inventory_scan

router = APIRouter()


class ScanRunRequest(BaseModel):
    resource_type: Optional[str] = None


@router.post("/scans")
def run_scan(request: ScanRunRequest = ScanRunRequest(), db: Session = Depends(get_db)):
    try:
        return run_inventory_scan(db, request.resource_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to run scan: {exc}") from exc


def _run_scan_background(execution_id: int, resource_type: Optional[str]) -> None:
    db = SessionLocal()
    try:
        run_inventory_scan(db, resource_type, execution_id=execution_id)
    except Exception as exc:
        mark_scan_failed(db, execution_id, str(exc))
    finally:
        db.close()


@router.post("/scans/start")
def start_scan(
    background_tasks: BackgroundTasks,
    request: ScanRunRequest = ScanRunRequest(),
    db: Session = Depends(get_db),
):
    try:
        response = create_scan_execution(db, request.resource_type)
        background_tasks.add_task(_run_scan_background, response["execution_id"], request.resource_type)
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start scan: {exc}") from exc


@router.get("/scans/{execution_id}")
def scan_status(execution_id: int, db: Session = Depends(get_db)):
    try:
        return get_scan_status(db, execution_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
