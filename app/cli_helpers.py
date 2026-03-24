"""Rich rendering helpers for the console-check CLI."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()

# Verdict → color mapping
VERDICT_STYLES = {
    "match": "bold green",
    "mismatch": "bold red",
    "swapped": "bold yellow",
    "no_response": "dim",
    "unknown": "dim",
}

STATUS_STYLES = {
    "ready": "bold green",
    "issues": "bold red",
    "partial": "bold yellow",
    "not_checked": "dim",
}

ROLE_LABELS = {
    "old_primary": "Old Primary",
    "old_secondary": "Old Secondary",
    "new_primary": "New Primary",
    "new_secondary": "New Secondary",
}


def render_banner(engineer: str, site_count: int):
    """Render the app banner."""
    text = Text()
    text.append("Site Readiness Verification Tool\n", style="bold")
    text.append(f"Engineer: {engineer}  │  DB: {site_count} sites", style="dim")
    console.print(Panel(text, title="console-check", border_style="blue"))


def render_site_table(sites: list) -> None:
    """Render a table of site search results."""
    if not sites:
        console.print("[dim]No sites found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Site ID", style="cyan")
    table.add_column("Customer")
    table.add_column("City, State")
    table.add_column("CP Key", style="bold white")
    table.add_column("Type")
    table.add_column("Status")

    for i, site in enumerate(sites, 1):
        status_style = STATUS_STYLES.get(site.status, "dim")
        table.add_row(
            str(i),
            site.id,
            site.bank_name,
            f"{site.city}, {site.state}",
            site.cp_search_key or "—",
            site.site_type.upper(),
            Text(site.status.replace("_", " ").upper(), style=status_style),
        )

    console.print(table)


def render_site_detail(site, devices: list, verifications: dict) -> None:
    """Render detailed site info with CP search key prominently displayed."""
    # Site info panel
    info = Text()
    info.append(f"{site.id}", style="bold cyan")
    info.append(f" — {site.bank_name}\n")
    info.append(f"{site.city}, {site.state}  │  ", style="dim")
    info.append(f"{site.site_type.upper()} site  │  ", style="dim")
    status_style = STATUS_STYLES.get(site.status, "dim")
    info.append(site.status.replace("_", " ").upper(), style=status_style)
    console.print(Panel(info, title="Site Detail"))

    # CP Search Key — big and prominent
    cp_key = site.cp_search_key or "NO UNLOCODE"
    console.print()
    console.print(Panel(
        Text(cp_key, style="bold white on blue", justify="center"),
        title="CP Search Key (paste into NCM)",
        border_style="blue",
        padding=(1, 4),
    ))
    console.print()

    # Port layout table
    render_port_layout(devices, verifications)


def render_port_layout(devices: list, verifications: dict) -> None:
    """Render port table with expected hostnames and verification status."""
    table = Table(title="Port Layout", show_header=True, header_style="bold")
    table.add_column("Port", style="bold", width=6)
    table.add_column("Role")
    table.add_column("Expected Hostname", style="cyan")
    table.add_column("Found Hostname")
    table.add_column("Verdict")
    table.add_column("Checked")

    for device in sorted(devices, key=lambda d: d.port):
        v = verifications.get(device.port)
        if v:
            verdict_style = VERDICT_STYLES.get(v.verdict, "dim")
            found = v.found_hostname or "—"
            verdict_text = Text(v.verdict.replace("_", " ").upper(), style=verdict_style)
            if v.swap_details:
                verdict_text.append(f"\n  {v.swap_details}", style="dim italic")
            checked = str(v.timestamp)[:16] if v.timestamp else "—"
        else:
            found = "—"
            verdict_text = Text("NOT CHECKED", style="dim")
            checked = "—"

        table.add_row(
            str(device.port),
            ROLE_LABELS.get(device.role, device.role),
            device.expected_hostname,
            found,
            verdict_text,
            checked,
        )

    console.print(table)


def render_verdict(result) -> None:
    """Render a single verification result."""
    verdict_style = VERDICT_STYLES.get(result.verdict, "dim")

    console.print()
    panel_text = Text()
    panel_text.append("Verdict: ", style="bold")
    panel_text.append(result.verdict.replace("_", " ").upper(), style=verdict_style)
    panel_text.append(f"\nExpected: ", style="dim")
    panel_text.append(result.expected_hostname, style="cyan")
    panel_text.append(f"\nFound:    ", style="dim")
    panel_text.append(result.found_hostname or "nothing", style="bold" if result.found_hostname else "dim")
    panel_text.append(f"\nConfidence: {result.confidence}", style="dim")

    if result.swap_details:
        panel_text.append(f"\n\n⚠ {result.swap_details}", style="yellow")

    border = "green" if result.verdict == "match" else "red" if result.verdict in ("mismatch", "no_response") else "yellow"
    console.print(Panel(panel_text, title=f"Port {result.port} Result", border_style=border))


def render_dashboard(summary: dict, sites: list) -> None:
    """Render the readiness dashboard."""
    # Summary panel
    text = Text()
    text.append(f"Total: {summary['total']}    ", style="bold")
    text.append(f"Ready: {summary['ready']}", style="green bold")
    pct = summary.get('percent_ready', 0)
    text.append(f" ({pct}%)  ", style="green")
    text.append(f"Issues: {summary['issues']}  ", style="red bold")
    text.append(f"Partial: {summary['partial']}  ", style="yellow bold")
    text.append(f"Not Checked: {summary['not_checked']}", style="dim bold")

    console.print(Panel(text, title="Readiness Summary", border_style="blue"))
    console.print()

    # Site table
    if sites:
        render_site_table(sites)


def render_import_results(result) -> None:
    """Render import outcome."""
    console.print()
    text = Text()
    text.append(f"Total rows: {result.total}\n", style="bold")
    text.append(f"Imported:   {result.imported}\n", style="green")
    text.append(f"Resolved:   {result.resolved}  (UNLOCODE auto-matched)\n", style="green")
    text.append(f"Unresolved: {result.unresolved}", style="yellow" if result.unresolved > 0 else "green")

    console.print(Panel(text, title="Import Results", border_style="green" if not result.errors else "yellow"))

    if result.errors:
        console.print("\n[bold red]Errors:[/bold red]")
        for err in result.errors[:10]:
            console.print(f"  [red]• {err}[/red]")
        if len(result.errors) > 10:
            console.print(f"  [dim]... and {len(result.errors) - 10} more[/dim]")

    if result.unresolved_sites:
        console.print(f"\n[yellow]{result.unresolved} sites need manual UNLOCODE resolution.[/yellow]")
        console.print("[dim]Use 'Resolve unresolved UNLOCODEs' from the main menu.[/dim]")


def render_column_preview(headers: list, sample_rows: list) -> None:
    """Render column headers with sample data for mapping."""
    table = Table(title="Spreadsheet Columns", show_header=True, header_style="bold")
    table.add_column("Index", style="bold cyan", width=6)
    table.add_column("Header")
    table.add_column("Sample Values", style="dim")

    for i, header in enumerate(headers):
        samples = []
        for row in sample_rows[:3]:
            if i < len(row) and row[i]:
                samples.append(str(row[i])[:30])
        table.add_row(str(i), header, " │ ".join(samples) if samples else "—")

    console.print(table)
