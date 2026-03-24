from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.site import Site
from app.models.device import Device
from app.models.verification import Verification
from app.services.hostname_parser import HostnameParser
from app.schemas.verification import VerifyResult

parser = HostnameParser()


def check_port(db: Session, site_id: str, port: int, raw_output: str, engineer: str | None = None) -> VerifyResult:
    """Verify a single port's console output against expected device."""
    device = db.query(Device).filter(
        Device.site_id == site_id, Device.port == port
    ).first()

    if not device:
        return VerifyResult(
            port=port, expected_hostname="", found_hostname=None,
            verdict="unknown", swap_details="No device expected on this port",
        )

    expected = device.expected_hostname
    result = parser.parse(raw_output)
    found = result.hostname

    if not found:
        verdict = "no_response"
        swap_details = None
    elif found.lower() == expected.lower():
        verdict = "match"
        swap_details = None
    else:
        # Check for swap: does found hostname match another port?
        other_device = db.query(Device).filter(
            Device.site_id == site_id,
            Device.port != port,
            func.lower(Device.expected_hostname) == found.lower(),
        ).first()

        if other_device:
            verdict = "swapped"
            swap_details = f"Found hostname matches Port {other_device.port} ({other_device.role})"
        else:
            verdict = "mismatch"
            swap_details = None

    # Save verification record
    verification = Verification(
        site_id=site_id,
        port=port,
        expected_hostname=expected,
        found_hostname=found,
        raw_output=raw_output,
        verdict=verdict,
        swap_details=swap_details,
        engineer=engineer,
    )
    db.add(verification)
    db.flush()  # Ensure new verification is visible for status calculation

    # Update site status
    _update_site_status(db, site_id)
    db.commit()

    return VerifyResult(
        port=port,
        expected_hostname=expected,
        found_hostname=found,
        verdict=verdict,
        swap_details=swap_details,
        confidence=result.confidence,
    )


def accept_swap(db: Session, site_id: str, port_a: int, port_b: int):
    """Swap expected hostnames between two ports and re-evaluate."""
    dev_a = db.query(Device).filter(Device.site_id == site_id, Device.port == port_a).first()
    dev_b = db.query(Device).filter(Device.site_id == site_id, Device.port == port_b).first()

    if not dev_a or not dev_b:
        return

    # Swap hostnames and roles
    dev_a.expected_hostname, dev_b.expected_hostname = dev_b.expected_hostname, dev_a.expected_hostname
    dev_a.role, dev_b.role = dev_b.role, dev_a.role

    _update_site_status(db, site_id)
    db.commit()


def _update_site_status(db: Session, site_id: str):
    """Recalculate and update site status based on latest verifications per port."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        return

    devices = db.query(Device).filter(Device.site_id == site_id).all()
    if not devices:
        site.status = "not_checked"
        return

    expected_ports = {d.port for d in devices}
    verdicts = {}  # port -> latest verdict

    for device in devices:
        latest = db.query(Verification).filter(
            Verification.site_id == site_id,
            Verification.port == device.port,
        ).order_by(Verification.timestamp.desc()).first()

        if latest:
            verdicts[device.port] = latest.verdict

    if not verdicts:
        site.status = "not_checked"
    elif set(verdicts.keys()) == expected_ports and all(v == "match" for v in verdicts.values()):
        site.status = "ready"
    elif any(v in ("mismatch", "no_response") for v in verdicts.values()):
        site.status = "issues"
    else:
        site.status = "partial"
