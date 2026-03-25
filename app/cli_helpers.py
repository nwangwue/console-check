"""Rich rendering helpers for the console-check CLI."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from app.services.ncm import DeviceInfo, PortResult

console = Console()


def render_banner():
    """Render the app banner."""
    text = Text()
    text.append("Cradlepoint Console Port Verification Tool\n", style="bold")
    text.append("Enter city + state → resolve UNLOCODE → check console ports via NCM", style="dim")
    console.print(Panel(text, title="console-check", border_style="blue"))


def render_unlocode_result(city: str, state: str, code: str | None, confidence: float = 1.0):
    """Render UNLOCODE resolution result."""
    if code:
        search_key = f"US{code}"
        console.print()
        console.print(f"  [bold green]✓[/bold green] {city}, {state} → [bold cyan]{code}[/bold cyan]")
        console.print(Panel(
            Text(search_key, style="bold white on blue", justify="center"),
            title="CP Search Key",
            border_style="blue",
            padding=(0, 4),
        ))
        if confidence < 1.0:
            console.print(f"  [dim](fuzzy match — confidence: {confidence:.0%})[/dim]")
    else:
        console.print(f"\n  [bold red]✗[/bold red] Could not resolve UNLOCODE for {city}, {state}")


def render_device_list(devices: list[DeviceInfo]) -> None:
    """Render a table of NCM device search results."""
    if not devices:
        console.print("[dim]No devices found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Device Name")
    table.add_column("Status")

    for i, device in enumerate(devices, 1):
        status_style = "green" if device.status == "online" else "red" if device.status == "offline" else "dim"
        table.add_row(
            str(i),
            device.name,
            Text(device.status.upper(), style=status_style),
        )

    console.print(table)


def render_port_results(results: list[PortResult]) -> None:
    """Render results from checking all console ports."""
    table = Table(
        title="Console Port Results",
        show_header=True,
        header_style="bold",
        show_lines=True,
    )
    table.add_column("Port", style="bold", width=6, justify="center")
    table.add_column("Hostname", min_width=20)
    table.add_column("Confidence")
    table.add_column("Status")

    for r in sorted(results, key=lambda x: x.port):
        if r.error:
            hostname_text = Text("—", style="dim")
            confidence_text = Text("—", style="dim")
            status_text = Text(r.error, style="red")
        elif r.hostname:
            hostname_text = Text(r.hostname, style="bold cyan")
            conf_style = "green" if r.confidence == "high" else "yellow" if r.confidence == "medium" else "red"
            confidence_text = Text(r.confidence.upper(), style=conf_style)
            status_text = Text("✓ Found", style="green")
        else:
            hostname_text = Text("—", style="dim")
            confidence_text = Text("—", style="dim")
            status_text = Text("No hostname detected", style="yellow")

        table.add_row(str(r.port), hostname_text, confidence_text, status_text)

    console.print()
    console.print(table)
    console.print()


def render_status(message: str, style: str = "dim"):
    """Render a status/progress message."""
    console.print(f"  [{style}]{message}[/{style}]")
