from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
import io

from app.database import get_db
from app.models.site import Site
from app.services.exporter import export_readiness

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """Aggregate counts: ready, issues, partial, not_checked."""
    total = db.query(func.count(Site.id)).scalar() or 0

    counts = {}
    for status in ("not_checked", "ready", "issues", "partial"):
        counts[status] = db.query(func.count(Site.id)).filter(Site.status == status).scalar() or 0

    return {
        "total": total,
        "not_checked": counts["not_checked"],
        "ready": counts["ready"],
        "issues": counts["issues"],
        "partial": counts["partial"],
        "percent_ready": round(counts["ready"] / total * 100, 1) if total > 0 else 0,
    }


@router.get("/sites")
def get_dashboard_sites(
    status: str = Query(""),
    site_type: str = Query(""),
    state: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Site readiness list with filters for dashboard table."""
    query = db.query(Site)
    if status:
        query = query.filter(Site.status == status)
    if site_type:
        query = query.filter(Site.site_type == site_type)
    if state:
        query = query.filter(Site.state == state.upper())

    total = query.count()
    items = query.order_by(Site.city, Site.state).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [
            {
                "id": s.id,
                "city": s.city,
                "state": s.state,
                "bank_name": s.bank_name,
                "unlocode": s.unlocode,
                "site_type": s.site_type,
                "status": s.status,
                "updated_at": str(s.updated_at) if s.updated_at else None,
            }
            for s in items
        ],
    }


@router.get("/export")
def export_csv(
    status: str = Query(""),
    site_type: str = Query(""),
    state: str = Query(""),
    db: Session = Depends(get_db),
):
    """Download CSV readiness report."""
    csv_content = export_readiness(db, status=status, site_type=site_type, state=state)

    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=readiness_report.csv"},
    )
