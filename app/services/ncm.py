"""Playwright-based browser automation for Cradlepoint NCM console port access."""

import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PwTimeout

from app.services.hostname_parser import HostnameParser
from app.config import NCM_URL, HEADLESS, BROWSER_TIMEOUT


@dataclass
class DeviceInfo:
    name: str
    status: str           # "online", "offline", etc.
    row_index: int = 0    # position in the list for clicking


@dataclass
class PortResult:
    port: int             # 1-4
    raw_output: str = ""
    hostname: str | None = None
    confidence: str = "none"
    error: str | None = None


# ── NCM UI Selectors ────────────────────────────────────────────────────────
# Update these if NCM changes its DOM. Use browser DevTools to inspect.
# Multiple selectors separated by commas act as fallbacks.
SELECTORS = {
    # Login page
    "login_form": 'form, [data-testid="login-form"], .login-form',
    "login_username": 'input[name="username"], input[type="email"], #username, input[name="email"]',
    "login_password": 'input[name="password"], input[type="password"], #password',
    "login_submit": 'button[type="submit"], input[type="submit"], button:has-text("Log In"), button:has-text("Sign In")',
    "login_error": '.error, .alert-danger, [role="alert"], .login-error',

    # Dashboard / logged-in indicator
    "dashboard": '.dashboard, #app, [data-testid="main-content"], nav, .main-content',

    # Devices tab & list
    "devices_nav": 'a:has-text("Devices"), [href*="device"], [data-nav="devices"]',
    "device_search": 'input[placeholder*="earch"], input[placeholder*="ilter"], input[type="search"], .search-input',
    "device_table": 'table, .device-list, [data-testid="device-list"]',
    "device_rows": 'table tbody tr, .device-row, [data-testid="device-row"]',

    # Device detail → console
    "troubleshooting_tab": 'a:has-text("Troubleshoot"), [data-tab*="troubleshoot"], a[href*="troubleshoot"]',
    "remote_connect": 'a:has-text("Remote Connect"), button:has-text("Remote Connect"), [data-action="remote-connect"]',
    "console_option": 'a:has-text("Console"), button:has-text("Console"), [data-type="console"]',
    "open_console_btn": 'button:has-text("Open Console"), button:has-text("Connect"), a:has-text("Open Console")',

    # Terminal
    "terminal": '.xterm, .terminal, [class*="terminal"], .xterm-screen',
    "terminal_rows": '.xterm-rows > div, .xterm-rows .xterm-row',
}


class NCMAutomation:
    """Automates Cradlepoint NCM web portal via Playwright."""

    def __init__(self, headless: bool = HEADLESS, timeout: int = BROWSER_TIMEOUT):
        self.headless = headless
        self.timeout = timeout
        self._pw = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._parser = HostnameParser()
        self._logged_in = False

    def start(self):
        """Launch browser with persistent context (preserves session cookies)."""
        user_data_dir = str(Path.home() / ".console-check" / "browser-data")
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context.set_default_timeout(self.timeout)

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = self._context.new_page()

    def close(self):
        """Clean up browser resources."""
        try:
            if self._context:
                self._context.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    # ── Login ───────────────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Check if we're already logged into NCM (session cookie valid)."""
        try:
            self.page.goto(NCM_URL, wait_until="domcontentloaded")
            time.sleep(2)

            # Check if we landed on a login page or the dashboard
            url = self.page.url.lower()
            if "login" in url or "signin" in url or "auth" in url:
                return False

            # Try to find dashboard content
            try:
                self.page.wait_for_selector(SELECTORS["dashboard"], timeout=5000)
                return True
            except PwTimeout:
                return False
        except Exception:
            return False

    def login(self, username: str, password: str) -> bool:
        """Log into NCM with username and password.

        Returns True if login succeeded, False otherwise.
        """
        try:
            self.page.goto(NCM_URL, wait_until="domcontentloaded")
            time.sleep(2)

            # Check if already logged in
            url = self.page.url.lower()
            if "login" not in url and "signin" not in url and "auth" not in url:
                try:
                    self.page.wait_for_selector(SELECTORS["dashboard"], timeout=5000)
                    self._logged_in = True
                    return True
                except PwTimeout:
                    pass

            # Find and fill login form
            username_input = self.page.locator(SELECTORS["login_username"]).first
            username_input.click()
            username_input.fill(username)

            password_input = self.page.locator(SELECTORS["login_password"]).first
            password_input.click()
            password_input.fill(password)

            # Submit
            self.page.locator(SELECTORS["login_submit"]).first.click()

            # Wait for navigation
            time.sleep(3)
            self.page.wait_for_load_state("domcontentloaded")

            # Check for login error
            try:
                error = self.page.locator(SELECTORS["login_error"]).first
                if error.is_visible(timeout=2000):
                    return False
            except Exception:
                pass

            # Verify we're logged in
            url = self.page.url.lower()
            if "login" in url or "signin" in url:
                return False

            self._logged_in = True
            return True

        except PwTimeout:
            return False
        except Exception as e:
            raise RuntimeError(f"Login failed: {e}") from e

    # ── Device Search ───────────────────────────────────────────────────────

    def search_devices(self, search_key: str) -> list[DeviceInfo]:
        """Navigate to Devices tab and search for devices matching the key.

        Args:
            search_key: The UNLOCODE-based search key (e.g., "USDAL")

        Returns:
            List of DeviceInfo found in the device table.
        """
        try:
            # Navigate to devices
            devices_link = self.page.locator(SELECTORS["devices_nav"]).first
            devices_link.click()
            time.sleep(2)
            self.page.wait_for_load_state("domcontentloaded")

            # Find search/filter input
            search_input = self.page.locator(SELECTORS["device_search"]).first
            search_input.click()
            search_input.fill("")
            search_input.fill(search_key)
            time.sleep(2)  # Wait for filter to apply

            # Scrape device rows
            return self._scrape_device_list()

        except PwTimeout:
            return []
        except Exception as e:
            raise RuntimeError(f"Device search failed: {e}") from e

    def _scrape_device_list(self) -> list[DeviceInfo]:
        """Extract device info from the current device list/table."""
        devices = []
        try:
            rows = self.page.locator(SELECTORS["device_rows"]).all()
            for i, row in enumerate(rows):
                text = row.inner_text().strip()
                if not text:
                    continue

                # Parse device name and status from row text
                # The exact parsing depends on NCM's table structure
                parts = text.split("\t")  # tab-separated in table cells
                if not parts:
                    parts = text.split("\n")

                name = parts[0].strip() if parts else text[:50]

                # Try to find status — look for common keywords
                status = "unknown"
                text_lower = text.lower()
                if "online" in text_lower:
                    status = "online"
                elif "offline" in text_lower:
                    status = "offline"

                devices.append(DeviceInfo(name=name, status=status, row_index=i))

        except Exception:
            pass

        return devices

    # ── Device Selection & Console ──────────────────────────────────────────

    def select_device(self, device: DeviceInfo):
        """Click into a specific device from the device list."""
        try:
            rows = self.page.locator(SELECTORS["device_rows"]).all()
            if device.row_index < len(rows):
                rows[device.row_index].click()
                time.sleep(2)
                self.page.wait_for_load_state("domcontentloaded")
            else:
                raise RuntimeError(f"Device row {device.row_index} not found")
        except PwTimeout:
            raise RuntimeError("Timed out clicking on device")

    def open_console(self):
        """Navigate from device detail to the console terminal.

        Flow: Troubleshooting tab → Remote Connect → Console → Open Console
        """
        try:
            # Click Troubleshooting tab
            self.page.locator(SELECTORS["troubleshooting_tab"]).first.click()
            time.sleep(2)

            # Click Remote Connect
            self.page.locator(SELECTORS["remote_connect"]).first.click()
            time.sleep(2)

            # Click Console option
            try:
                self.page.locator(SELECTORS["console_option"]).first.click()
                time.sleep(1)
            except Exception:
                pass  # Console might already be selected

            # Click Open Console button
            self.page.locator(SELECTORS["open_console_btn"]).first.click()
            time.sleep(3)  # Give the terminal time to initialize

            # Wait for terminal widget to appear
            self.page.wait_for_selector(SELECTORS["terminal"], timeout=15000)
            time.sleep(2)

        except PwTimeout:
            raise RuntimeError(
                "Timed out opening console. The device may be offline or "
                "the NCM UI structure may have changed."
            )

    # ── Port Checking ───────────────────────────────────────────────────────

    def check_port(self, port_number: int) -> PortResult:
        """Check a single console port by typing serial command and reading output.

        Args:
            port_number: Port number 1-4

        Returns:
            PortResult with extracted hostname or error.
        """
        if port_number not in (1, 2, 3, 4):
            return PortResult(port=port_number, error="Invalid port number (must be 1-4)")

        try:
            # Click on terminal to ensure focus
            terminal = self.page.locator(SELECTORS["terminal"]).first
            terminal.click()
            time.sleep(0.5)

            # Type the serial command
            self.page.keyboard.type(f"serial --force {port_number}")
            self.page.keyboard.press("Enter")
            time.sleep(3)  # Wait for serial connection

            # Send a couple of Enters to wake the device
            self.page.keyboard.press("Enter")
            time.sleep(1)
            self.page.keyboard.press("Enter")
            time.sleep(2)

            # Read terminal output
            output = self._read_terminal()

            if not output or not output.strip():
                # Retry with longer wait
                self.page.keyboard.press("Enter")
                time.sleep(5)
                output = self._read_terminal()

            # Parse hostname
            result = self._parser.parse(output)

            return PortResult(
                port=port_number,
                raw_output=output,
                hostname=result.hostname,
                confidence=result.confidence,
                error=None if output.strip() else "No response from port",
            )

        except PwTimeout:
            return PortResult(port=port_number, error="Timed out reading port")
        except Exception as e:
            return PortResult(port=port_number, error=str(e))

    def check_all_ports(self) -> list[PortResult]:
        """Check all 4 console ports sequentially."""
        results = []
        for port in range(1, 5):
            result = self.check_port(port)
            results.append(result)

            # Brief pause between ports to let the terminal settle
            if port < 4:
                time.sleep(1)

        return results

    def _read_terminal(self) -> str:
        """Scrape text content from the terminal widget.

        Tries xterm.js DOM scraping first, falls back to broader approaches.
        """
        # Approach 1: xterm.js rows (most common terminal library)
        try:
            text = self.page.evaluate("""
                () => {
                    // Try xterm.js structure
                    const rows = document.querySelectorAll('.xterm-rows > div');
                    if (rows.length > 0) {
                        return Array.from(rows).map(r => r.textContent).join('\\n');
                    }

                    // Try generic terminal container
                    const terminal = document.querySelector('.xterm, .terminal, [class*="terminal"]');
                    if (terminal) {
                        return terminal.innerText || terminal.textContent || '';
                    }

                    return '';
                }
            """)
            return text or ""
        except Exception:
            return ""

    # ── Utility ─────────────────────────────────────────────────────────────

    def take_screenshot(self, path: str = "ncm_debug.png"):
        """Save a screenshot for debugging selector issues."""
        try:
            self.page.screenshot(path=path)
        except Exception:
            pass
