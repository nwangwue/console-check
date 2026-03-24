import csv
import io

from sqlalchemy.orm import Session

from app.models.site import Site
from app.models.device import Device
from app.models.verification import Verification


def export_readiness(db: Session, status: str = "", site_type: str = "", state: str = "") -> str:
    """Export readiness report as CSV string."""
    query = db.query(Site)
    if status:
        query = query.filter(Site.status == status)
    if site_type:
        query = query.filter(Site.site_type == site_type)
    if state:
        query = query.filter(Site.state == state.upper())

    sites = query.order_by(Site.city, Site.state).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Site ID", "City", "State", "Bank Name", "UNLOCODE", "CP Search Key",
        "Site Type", "Status",
        "Port 1 Expected", "Port 1 Found", "Port 1 Verdict", "Port 1 Checked At",
        "Port 2 Expected", "Port 2 Found", "Port 2 Verdict", "Port 2 Checked At",
        "Port 3 Expected", "Port 3 Found", "Port 3 Verdict", "Port 3 Checked At",
        "Port 4 Expected", "Port 4 Found", "Port 4 Verdict", "Port 4 Checked At",
        "Engineer",
    ])

    for site in sites:
        devices = {d.port: d for d in db.query(Device).filter(Device.site_id == site.id).all()}
        row = [
            site.id, site.city, site.state, site.bank_name,
            site.unlocode or "", site.cp_search_key or "",
            site.site_type, site.status,
        ]

        last_engineer = ""
        for port_num in (1, 2, 3, 4):
            device = devices.get(port_num)
            if device:
                latest = db.query(Verification).filter(
                    Verification.site_id == site.id,
                    Verification.port == port_num,
                ).order_by(Verification.timestamp.desc()).first()

                row.extend([
                    device.expected_hostname,
                    latest.found_hostname if latest else "",
                    latest.verdict if latest else "",
                    str(latest.timestamp) if latest and latest.timestamp else "",
                ])
                if latest and latest.engineer:
                    last_engineer = latest.engineer
            else:
                row.extend(["", "", "", ""])

        row.append(last_engineer)
        writer.writerow(row)

    return output.getvalue()
