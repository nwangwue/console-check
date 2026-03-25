"""console-check CLI — stateless Cradlepoint console port verification tool."""

import sys

import questionary
from rich.console import Console

from app.config import LOCODE_CSV_PATH
from app.services.unlocode import UnlocodeService
from app.services.ncm import NCMAutomation
from app.cli_helpers import (
    console,
    render_banner,
    render_unlocode_result,
    render_device_list,
    render_port_results,
    render_status,
)


# Globals initialized in main()
unlocode_service: UnlocodeService = None
ncm: NCMAutomation = None


def main():
    """Entry point for the console-check CLI."""
    global unlocode_service, ncm

    # Load UNLOCODE data
    try:
        unlocode_service = UnlocodeService(LOCODE_CSV_PATH)
    except Exception as e:
        console.print(f"[red]Failed to load UNLOCODE data: {e}[/red]")
        console.print(f"[dim]Expected at: {LOCODE_CSV_PATH}[/dim]")
        sys.exit(1)

    # Create NCM automation (browser launched on first use)
    ncm = NCMAutomation()

    try:
        _main_loop()
    except KeyboardInterrupt:
        console.print("\n[dim]Goodbye![/dim]")
    finally:
        if ncm:
            ncm.close()


def _main_loop():
    """Main interactive loop."""
    render_banner()

    while True:
        console.print()
        choice = questionary.select(
            "What do you want to do?",
            choices=[
                questionary.Choice("Look up a site (city + state → check ports)", value="lookup"),
                questionary.Choice("Quit", value="quit"),
            ],
        ).ask()

        if choice is None or choice == "quit":
            console.print("[dim]Goodbye![/dim]")
            break

        if choice == "lookup":
            try:
                lookup_flow()
            except KeyboardInterrupt:
                console.print("\n[dim]Interrupted. Returning to menu...[/dim]")
            except Exception as e:
                console.print(f"\n[red]Error: {e}[/red]")


# ─── Lookup Flow ────────────────────────────────────────────────────────────

def lookup_flow():
    """Full flow: city/state → UNLOCODE → NCM → select device → check ports."""

    # 1. Get city + state
    city = questionary.text("Enter city:").ask()
    if not city or not city.strip():
        return

    state = questionary.text("Enter state (2-letter code):").ask()
    if not state or not state.strip():
        return

    city = city.strip()
    state = state.strip().upper()

    if len(state) != 2:
        console.print("[red]State must be a 2-letter code (e.g., TX, CA, NY)[/red]")
        return

    # 2. Resolve UNLOCODE
    result = unlocode_service.resolve(city, state)
    render_unlocode_result(city, state, result.code, result.confidence)

    if not result.code:
        # Offer manual entry or suggestions
        if result.suggestions:
            console.print("\n[yellow]Suggestions:[/yellow]")
            choices = [
                questionary.Choice(f"{s['code']} — {s['name']} (score: {s['score']}%)", value=s['code'])
                for s in result.suggestions
            ]
            choices.append(questionary.Choice("Enter UNLOCODE manually", value="__manual__"))
            choices.append(questionary.Choice("← Cancel", value=None))

            selected = questionary.select("Select a UNLOCODE:", choices=choices).ask()
            if not selected:
                return
            if selected == "__manual__":
                code = questionary.text("Enter 3-character UNLOCODE:").ask()
                if not code or len(code.strip()) != 3:
                    console.print("[dim]Invalid code.[/dim]")
                    return
                search_key = f"US{code.strip().upper()}"
            else:
                search_key = f"US{selected}"
        else:
            code = questionary.text("Enter 3-character UNLOCODE manually:").ask()
            if not code or len(code.strip()) != 3:
                console.print("[dim]Invalid code.[/dim]")
                return
            search_key = f"US{code.strip().upper()}"
    else:
        search_key = f"US{result.code}"

    # 3. Connect to NCM
    _ensure_ncm_login()

    # 4. Search for devices
    render_status(f"Searching NCM for devices matching '{search_key}'...")
    devices = ncm.search_devices(search_key)

    if not devices:
        console.print(f"[yellow]No devices found matching '{search_key}' in NCM.[/yellow]")
        return

    render_device_list(devices)

    # 5. Select a device — loop to allow checking multiple devices at this location
    while True:
        device_choices = [
            questionary.Choice(
                f"{d.name} ({d.status})",
                value=i,
            )
            for i, d in enumerate(devices)
        ]
        device_choices.append(questionary.Choice("← Done with this location", value=None))

        selected_idx = questionary.select("Select a device:", choices=device_choices).ask()
        if selected_idx is None:
            break

        device = devices[selected_idx]

        if device.status == "offline":
            if not questionary.confirm(
                f"{device.name} appears OFFLINE. Try anyway?", default=False
            ).ask():
                continue

        # 6. Open console and check all ports
        try:
            render_status(f"Opening {device.name}...")
            ncm.select_device(device)

            render_status("Navigating to console...")
            ncm.open_console()

            render_status("Checking all 4 console ports...")
            results = ncm.check_all_ports()

            render_port_results(results)

        except RuntimeError as e:
            console.print(f"[red]Error: {e}[/red]")
            console.print("[dim]The NCM UI may have changed or the device may be unresponsive.[/dim]")
            # Save debug screenshot
            ncm.take_screenshot()
            console.print("[dim]Debug screenshot saved to ncm_debug.png[/dim]")

        # Ask what to do next
        next_action = questionary.select("What next?", choices=[
            questionary.Choice("Check another device at this location", value="another"),
            questionary.Choice("← Done with this location", value="done"),
        ]).ask()

        if next_action != "another":
            break

        # Go back to device list
        try:
            ncm.go_back()
        except Exception:
            # Re-search if navigation fails
            render_status(f"Re-searching for '{search_key}'...")
            devices = ncm.search_devices(search_key)
            if not devices:
                console.print("[yellow]Could not re-load device list.[/yellow]")
                break
            render_device_list(devices)


# ─── NCM Login Helper ──────────────────────────────────────────────────────

def _ensure_ncm_login():
    """Ensure NCM browser is started and logged in."""
    global ncm

    if not ncm._started:
        render_status("Launching browser...")
        ncm.start()

    render_status("Checking NCM session...")

    if ncm.is_logged_in():
        render_status("Already logged into NCM ✓", style="green")
        return

    # Prompt for credentials
    console.print()
    console.print("[bold]NCM login required[/bold]")
    username = questionary.text("NCM Username:").ask()
    if not username:
        raise RuntimeError("Username required")

    password = questionary.password("NCM Password:").ask()
    if not password:
        raise RuntimeError("Password required")

    render_status("Logging in...")
    success = ncm.login(username, password)

    if not success:
        raise RuntimeError("NCM login failed. Check your credentials and try again.")

    render_status("Logged into NCM ✓", style="green")


if __name__ == "__main__":
    main()
