"""console-check CLI — interactive site readiness verification tool."""

import os
import sys

import questionary
from rich.console import Console
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from prompt_toolkit import prompt as pt_prompt

from app.database import engine, Base, SessionLocal
from app.config import LOCODE_CSV_PATH, FUZZY_MATCH_THRESHOLD
from app.models.site import Site
from app.models.device import Device
from app.models.verification import Verification
from app.services.unlocode import UnlocodeService
from app.services.hostname_parser import HostnameParser
from app.services.verifier import check_port, accept_swap
from app.services.importer import SiteImporter
from app.services.exporter import export_readiness
from app.schemas.import_config import ColumnMapping
from app.cli_helpers import (
    console,
    render_banner,
    render_site_table,
    render_site_detail,
    render_port_layout,
    render_verdict,
    render_dashboard,
    render_import_results,
    render_column_preview,
)


# Globals initialized in main()
unlocode_service: UnlocodeService = None
engineer_name: str = ""


def main():
    """Entry point for the console-check CLI."""
    global unlocode_service, engineer_name

    # Init database
    Base.metadata.create_all(bind=engine)

    # Load UNLOCODE data
    try:
        unlocode_service = UnlocodeService(LOCODE_CSV_PATH)
    except Exception as e:
        console.print(f"[red]Failed to load UNLOCODE data: {e}[/red]")
        console.print(f"[dim]Expected at: {LOCODE_CSV_PATH}[/dim]")
        sys.exit(1)

    # Ask for engineer name
    engineer_name = questionary.text(
        "Your name or initials:",
        default=os.environ.get("CONSOLE_CHECK_ENGINEER", ""),
    ).ask()

    if not engineer_name:
        engineer_name = "unknown"

    # Main loop
    while True:
        db = SessionLocal()
        try:
            site_count = db.query(func.count(Site.id)).scalar() or 0
            console.print()
            render_banner(engineer_name, site_count)

            choice = questionary.select(
                "What do you want to do?",
                choices=[
                    questionary.Choice("Search for a site (customer name, city, site ID)", value="search"),
                    questionary.Choice("Verify ports for a site", value="verify"),
                    questionary.Choice("Import master site list", value="import"),
                    questionary.Choice("View readiness dashboard", value="dashboard"),
                    questionary.Choice("Export CSV report", value="export"),
                    questionary.Choice("Resolve unresolved UNLOCODEs", value="resolve"),
                    questionary.Choice("Quit", value="quit"),
                ],
            ).ask()

            if choice is None or choice == "quit":
                console.print("[dim]Goodbye![/dim]")
                break

            if choice == "search":
                search_flow(db)
            elif choice == "verify":
                verify_menu_flow(db)
            elif choice == "import":
                import_flow(db)
            elif choice == "dashboard":
                dashboard_flow(db)
            elif choice == "export":
                export_flow(db)
            elif choice == "resolve":
                resolve_flow(db)

        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted. Returning to menu...[/dim]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")
        finally:
            db.close()


# ─── Search Flow ──────────────────────────────────────────────────────────────

def search_flow(db: Session):
    """Search for sites by customer name, city, site ID, or UNLOCODE."""
    query = questionary.text("Search (customer name, city, site ID, UNLOCODE):").ask()
    if not query or not query.strip():
        return

    sites = search_sites(db, query.strip())
    if not sites:
        console.print("[dim]No matching sites found.[/dim]")
        return

    render_site_table(sites)

    # Let user pick a site
    choices = [
        questionary.Choice(f"{s.id} — {s.bank_name} ({s.city}, {s.state})", value=s.id)
        for s in sites
    ]
    choices.append(questionary.Choice("← Back to menu", value=None))

    selected = questionary.select("Select a site:", choices=choices).ask()
    if not selected:
        return

    show_site_detail(db, selected)


def search_sites(db: Session, query: str) -> list:
    """Search sites across multiple fields."""
    search = f"%{query}%"
    sites = db.query(Site).filter(or_(
        Site.id.ilike(search),
        Site.city.ilike(search),
        Site.bank_name.ilike(search),
        Site.unlocode.ilike(search),
        Site.cp_search_key.ilike(search),
    )).limit(20).all()

    # Also check device hostnames
    existing_ids = {s.id for s in sites}
    device_matches = db.query(Device).filter(
        Device.expected_hostname.ilike(search)
    ).limit(10).all()
    device_site_ids = {d.site_id for d in device_matches} - existing_ids
    if device_site_ids:
        extra = db.query(Site).filter(Site.id.in_(device_site_ids)).all()
        sites.extend(extra)

    return sites[:20]


def show_site_detail(db: Session, site_id: str):
    """Show site detail and offer to verify."""
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        console.print("[red]Site not found.[/red]")
        return

    devices = db.query(Device).filter(Device.site_id == site_id).order_by(Device.port).all()
    verifications = _get_latest_verifications(db, site_id)

    render_site_detail(site, devices, verifications)

    # Offer to verify
    if devices:
        action = questionary.select("What next?", choices=[
            questionary.Choice("Verify ports", value="verify"),
            questionary.Choice("← Back", value=None),
        ]).ask()

        if action == "verify":
            verify_flow(db, site_id)


# ─── Verify Flow ─────────────────────────────────────────────────────────────

def verify_menu_flow(db: Session):
    """Verify ports — first search for a site, then verify."""
    query = questionary.text("Search for site to verify:").ask()
    if not query or not query.strip():
        return

    sites = search_sites(db, query.strip())
    if not sites:
        console.print("[dim]No matching sites found.[/dim]")
        return

    render_site_table(sites)

    choices = [
        questionary.Choice(f"{s.id} — {s.bank_name} ({s.city}, {s.state})", value=s.id)
        for s in sites
    ]
    choices.append(questionary.Choice("← Back", value=None))

    selected = questionary.select("Select site to verify:", choices=choices).ask()
    if not selected:
        return

    verify_flow(db, selected)


def verify_flow(db: Session, site_id: str):
    """Verify ports for a specific site."""
    while True:
        site = db.query(Site).filter(Site.id == site_id).first()
        devices = db.query(Device).filter(Device.site_id == site_id).order_by(Device.port).all()
        verifications = _get_latest_verifications(db, site_id)

        console.print()
        cp_key = site.cp_search_key or "NO UNLOCODE"
        console.print(f"[bold blue]CP Search Key: {cp_key}[/bold blue]   [dim]({site.bank_name})[/dim]")
        console.print()
        render_port_layout(devices, verifications)

        # Pick a port
        port_choices = [
            questionary.Choice(
                f"Port {d.port} — {d.expected_hostname} ({d.role})",
                value=d.port,
            )
            for d in devices
        ]
        port_choices.append(questionary.Choice("← Done with this site", value=None))

        port = questionary.select("Select port to verify:", choices=port_choices).ask()
        if port is None:
            break

        # Get console output
        console.print(f"\n[bold]Paste console output for Port {port}.[/bold]")
        console.print("[dim]Press Esc+Enter (or Alt+Enter) when done:[/dim]\n")

        try:
            raw_output = pt_prompt("", multiline=True)
        except (EOFError, KeyboardInterrupt):
            continue

        if not raw_output or not raw_output.strip():
            console.print("[dim]No input. Skipping.[/dim]")
            continue

        # Run verification
        result = check_port(db, site_id, port, raw_output.strip(), engineer_name)
        render_verdict(result)

        # Handle swap
        if result.verdict == "swapped" and result.swap_details:
            # Extract the other port from swap_details like "Found hostname matches Port 1 (old_primary)"
            import re
            match = re.search(r"Port (\d)", result.swap_details)
            if match:
                other_port = int(match.group(1))
                if questionary.confirm(
                    f"Accept swap? (Port {port} ↔ Port {other_port})",
                    default=False,
                ).ask():
                    accept_swap(db, site_id, port, other_port)
                    console.print("[green]Swap accepted.[/green]")


# ─── Import Flow ─────────────────────────────────────────────────────────────

def import_flow(db: Session):
    """Import a master site list spreadsheet."""
    file_path = questionary.path(
        "Path to spreadsheet (xlsx/csv):",
        only_directories=False,
    ).ask()

    if not file_path:
        return

    file_path = os.path.expanduser(file_path.strip())
    if not os.path.exists(file_path):
        console.print(f"[red]File not found: {file_path}[/red]")
        return

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ('.xlsx', '.xls', '.csv'):
        console.print("[red]Only .xlsx, .xls, and .csv files are supported.[/red]")
        return

    importer = SiteImporter(unlocode_service, db)

    # Preview
    console.print("\n[bold]Reading file...[/bold]")
    preview = importer.preview(file_path)
    console.print(f"Found {preview.total_rows} data rows.\n")
    render_column_preview(preview.headers, preview.sample_rows)

    # Column mapping — interactive selection
    console.print("\n[bold]Map spreadsheet columns to required fields:[/bold]\n")

    header_choices = [
        questionary.Choice(f"{i}: {h}", value=i)
        for i, h in enumerate(preview.headers)
    ]
    skip_choice = questionary.Choice("— Skip (not applicable) —", value=None)

    def ask_column(label: str, required: bool = True) -> int | None:
        choices = list(header_choices)
        if not required:
            choices.append(skip_choice)
        result = questionary.select(f"{label}:", choices=choices).ask()
        return result

    site_id = ask_column("Site ID column")
    city = ask_column("City column")
    state = ask_column("State column")
    bank_name = ask_column("Bank/Customer name column")
    site_type = ask_column("Site type (single/HA) column")
    old_primary = ask_column("Old primary hostname column")
    old_secondary = ask_column("Old secondary hostname column (HA only)", required=False)
    new_primary = ask_column("New primary hostname column")
    new_secondary = ask_column("New secondary hostname column (HA only)", required=False)
    full_address = ask_column("Full address column", required=False)

    if any(v is None for v in [site_id, city, state, bank_name, site_type, old_primary, new_primary]):
        console.print("[red]Required columns not mapped. Aborting import.[/red]")
        return

    column_map = ColumnMapping(
        site_id=site_id,
        city=city,
        state=state,
        bank_name=bank_name,
        site_type=site_type,
        old_primary_hostname=old_primary,
        old_secondary_hostname=old_secondary,
        new_primary_hostname=new_primary,
        new_secondary_hostname=new_secondary,
        full_address=full_address,
    )

    # Confirm
    if not questionary.confirm("Execute import?", default=True).ask():
        return

    # Execute
    console.print("\n[bold]Importing...[/bold]")
    result = importer.execute(file_path, column_map)
    render_import_results(result)


# ─── Dashboard Flow ──────────────────────────────────────────────────────────

def dashboard_flow(db: Session):
    """View readiness dashboard."""
    summary = get_summary(db)
    sites = db.query(Site).order_by(Site.status, Site.city).all()
    render_dashboard(summary, sites)


def get_summary(db: Session) -> dict:
    """Get aggregate site status counts."""
    total = db.query(func.count(Site.id)).scalar() or 0
    counts = {"total": total}
    for status in ("not_checked", "ready", "issues", "partial"):
        counts[status] = db.query(func.count(Site.id)).filter(
            Site.status == status
        ).scalar() or 0
    counts["percent_ready"] = round(counts["ready"] / total * 100, 1) if total > 0 else 0
    return counts


# ─── Export Flow ─────────────────────────────────────────────────────────────

def export_flow(db: Session):
    """Export CSV readiness report."""
    output_path = questionary.text(
        "Output file path:",
        default="readiness_report.csv",
    ).ask()

    if not output_path:
        return

    output_path = os.path.expanduser(output_path.strip())

    # Optional status filter
    status_filter = questionary.select("Filter by status?", choices=[
        questionary.Choice("All statuses", value=None),
        questionary.Choice("Ready only", value="ready"),
        questionary.Choice("Issues only", value="issues"),
        questionary.Choice("Partial only", value="partial"),
        questionary.Choice("Not checked only", value="not_checked"),
    ]).ask()

    csv_content = export_readiness(db, status=status_filter or "")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        f.write(csv_content)

    console.print(f"\n[green]Report exported to: {os.path.abspath(output_path)}[/green]")


# ─── Resolve UNLOCODEs Flow ─────────────────────────────────────────────────

def resolve_flow(db: Session):
    """Manually resolve unresolved UNLOCODEs."""
    unresolved = db.query(Site).filter(Site.unlocode_resolved == False).all()  # noqa: E712

    if not unresolved:
        console.print("[green]All sites have resolved UNLOCODEs![/green]")
        return

    console.print(f"\n[yellow]{len(unresolved)} sites need UNLOCODE resolution:[/yellow]\n")

    for site in unresolved:
        console.print(f"[bold]{site.id}[/bold] — {site.bank_name} ({site.city}, {site.state})")

        # Get suggestions
        result = unlocode_service.resolve(site.city, site.state)
        suggestions = result.suggestions if result.suggestions else []

        choices = [
            questionary.Choice(f"{s['code']} — {s['name']} (score: {s['score']})", value=s['code'])
            for s in suggestions
        ]
        choices.append(questionary.Choice("Enter manually", value="__manual__"))
        choices.append(questionary.Choice("Skip for now", value=None))

        selected = questionary.select("Select UNLOCODE:", choices=choices).ask()

        if selected is None:
            continue
        elif selected == "__manual__":
            code = questionary.text("Enter 3-character UNLOCODE:").ask()
            if not code or len(code.strip()) != 3:
                console.print("[dim]Invalid code. Skipping.[/dim]")
                continue
            selected = code.strip().upper()

        site.unlocode = selected
        site.unlocode_resolved = True
        site.cp_search_key = f"US{selected}"
        db.commit()
        console.print(f"  [green]Set to {selected} → CP Key: US{selected}[/green]")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_latest_verifications(db: Session, site_id: str) -> dict:
    """Get the latest verification for each port of a site."""
    devices = db.query(Device).filter(Device.site_id == site_id).all()
    verifications = {}
    for device in devices:
        latest = db.query(Verification).filter(
            Verification.site_id == site_id,
            Verification.port == device.port,
        ).order_by(Verification.timestamp.desc()).first()
        if latest:
            verifications[device.port] = latest
    return verifications


if __name__ == "__main__":
    main()
