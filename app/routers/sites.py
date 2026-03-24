from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.models.site import Site
from app.models.device import Device
from app.schemas.site import SiteListItem, SiteDetail, SiteListResponse

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("", response_model=SiteListResponse)
def list_sites(
    q: str = Query("", description="Search across all fields"),
    status: str = Query("", description="Filter by status"),
    site_type: str = Query("", description="Filter by site type"),
    state: str = Query("", description="Filter by state"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Site)

    if q:
        search = f"%{q}%"
        query = query.filter(or_(
            Site.id.ilike(search),
            Site.city.ilike(search),
            Site.state.ilike(search),
            Site.bank_name.ilike(search),
            Site.unlocode.ilike(search),
            Site.cp_search_key.ilike(search),
        ))

    if status:
        query = query.filter(Site.status == status)
    if site_type:
        query = query.filter(Site.site_type == site_type)
    if state:
        query = query.filter(Site.state == state.upper())

    total = query.count()
    items = query.order_by(Site.city, Site.state).offset((page - 1) * per_page).limit(per_page).all()

    return SiteListResponse(
        items=[SiteListItem.model_validate(s) for s in items],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/search")
def search_sites(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Quick search returning up to 20 results for autocomplete/typeahead."""
    search = f"%{q}%"
    sites = db.query(Site).filter(or_(
        Site.id.ilike(search),
        Site.city.ilike(search),
        Site.bank_name.ilike(search),
        Site.unlocode.ilike(search),
        Site.cp_search_key.ilike(search),
    )).limit(20).all()

    # Also search by expected hostname in devices
    device_matches = db.query(Device).filter(
        Device.expected_hostname.ilike(search)
    ).limit(10).all()
    device_site_ids = {d.site_id for d in device_matches}
    if device_site_ids:
        extra_sites = db.query(Site).filter(Site.id.in_(device_site_ids)).all()
        existing_ids = {s.id for s in sites}
        for s in extra_sites:
            if s.id not in existing_ids:
                sites.append(s)

    return [SiteListItem.model_validate(s) for s in sites[:20]]


@router.get("/{site_id}", response_model=SiteDetail)
def get_site(site_id: str, db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Site not found")
    return SiteDetail.model_validate(site)
