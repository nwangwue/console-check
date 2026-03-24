from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.site import Site
from app.models.device import Device
from app.models.verification import Verification
from app.schemas.verification import (
    VerifyRequest, VerifyResult, AcceptSwapRequest,
    PortStatus, SiteVerificationStatus,
)
from app.services.verifier import check_port, accept_swap

router = APIRouter(prefix="/api/verify", tags=["verify"])


@router.post("/{site_id}/port/{port}", response_model=VerifyResult)
def verify_port(
    site_id: str, port: int, req: VerifyRequest,
    db: Session = Depends(get_db),
):
    """Submit console output for a port, return verdict."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    return check_port(db, site_id, port, req.raw_output, req.engineer)


@router.post("/{site_id}/accept-swap")
def accept_swap_route(
    site_id: str, req: AcceptSwapRequest,
    db: Session = Depends(get_db),
):
    """Accept a port swap (swap expected hostnames between two ports)."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    accept_swap(db, site_id, req.port_a, req.port_b)
    return {"status": "ok", "message": f"Swapped ports {req.port_a} and {req.port_b}"}


@router.get("/{site_id}/status", response_model=SiteVerificationStatus)
def get_verification_status(site_id: str, db: Session = Depends(get_db)):
    """Get current verification status for all ports."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    devices = db.query(Device).filter(Device.site_id == site_id).order_by(Device.port).all()

    ports = []
    for device in devices:
        latest = db.query(Verification).filter(
            Verification.site_id == site_id,
            Verification.port == device.port,
        ).order_by(Verification.timestamp.desc()).first()

        ports.append(PortStatus(
            port=device.port,
            role=device.role,
            expected_hostname=device.expected_hostname,
            found_hostname=latest.found_hostname if latest else None,
            verdict=latest.verdict if latest else None,
            swap_details=latest.swap_details if latest else None,
            timestamp=str(latest.timestamp) if latest and latest.timestamp else None,
        ))

    return SiteVerificationStatus(
        site_id=site_id,
        status=site.status,
        ports=ports,
    )
