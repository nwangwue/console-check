"""Playwright-based browser automation for Cradlepoint NCM console port access.

Playwright's sync API creates its own asyncio event loop, which conflicts with
questionary (which also uses asyncio.run). To avoid this, all Playwright work
runs in a dedicated background thread. The main thread calls methods that proxy
to the background thread via a simple request/response queue.
"""

import time
import threading
import queue
from dataclasses import dataclass
from pathlib import Path

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
SELECTORS = {
    # Login page
    "login_form": 'form, [data-testid="login-form"], .login-form',
    "login_username": 'input[name="username"], input[type="email"], #username, input[name="email"]',
    "login_password": 'input[name="password"], input[type="password"], #password',
    "login_submit": 'button[type="submit"], input[type="submit"], button:has-text("Log In"), button:has-text("Sign In")',
    "login_error": '.error, .alert-danger, [role="alert"], .login-error',

    # MFA / Two-factor authentication
    "mfa_input": 'input[name="otp"], input[name="mfa"], input[name="code"], input[name="token"], input[name="totp"], input[type="tel"][autocomplete="one-time-code"], input[placeholder*="erification"], input[placeholder*="ode"], input[placeholder*="otp"], input[aria-label*="erification"], input[aria-label*="ode"]',
    "mfa_submit": 'button[type="submit"], input[type="submit"], button:has-text("Verify"), button:has-text("Submit"), button:has-text("Continue"), button:has-text("Confirm")',
    "mfa_page": 'text=/erification|two.factor|2fa|authenticator|one.time|MFA|security code/i, [class*="mfa"], [class*="otp"], [class*="two-factor"], [class*="verification"]',

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


# ── Background Thread Worker ────────────────────────────────────────────────

_SENTINEL = object()


def _playwright_worker(
    headless: bool,
    timeout: int,
    cmd_queue: queue.Queue,
    result_queue: queue.Queue,
):
    """Runs in a background thread. Owns the Playwright browser and event loop."""
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

    parser = HostnameParser()
    pw = None
    context = None
    page = None

    try:
        user_data_dir = str(Path.home() / ".console-check" / "browser-data")
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        pw = sync_playwright().start()
        context = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            viewport={"width": 1280, "height": 800},
            args=["--disable-blink-features=AutomationControlled"],
        )
        context.set_default_timeout(timeout)
        page = context.pages[0] if context.pages else context.new_page()

        # Signal that startup is done
        result_queue.put(("ok", None))

        # Process commands until told to stop
        while True:
            cmd = cmd_queue.get()
            if cmd is _SENTINEL:
                break

            method_name, args, kwargs = cmd
            try:
                fn = _COMMANDS[method_name]
                ret = fn(page, parser, *args, **kwargs)
                result_queue.put(("ok", ret))
            except Exception as e:
                result_queue.put(("error", e))

    except Exception as e:
        # Startup failed
        result_queue.put(("error", e))
    finally:
        try:
            if context:
                context.close()
            if pw:
                pw.stop()
        except Exception:
            pass


# ── Command implementations (run inside the worker thread) ──────────────────

def _cmd_is_logged_in(page, parser):
    from playwright.sync_api import TimeoutError as PwTimeout
    try:
        page.goto(NCM_URL, wait_until="domcontentloaded")
        time.sleep(2)
        url = page.url.lower()
        if "login" in url or "signin" in url or "auth" in url:
            return False
        try:
            page.wait_for_selector(SELECTORS["dashboard"], timeout=5000)
            return True
        except PwTimeout:
            return False
    except Exception:
        return False


def _cmd_login(page, parser, username, password):
    """Returns: True (logged in), False (failed), "mfa_required" (needs MFA token)."""
    from playwright.sync_api import TimeoutError as PwTimeout
    page.goto(NCM_URL, wait_until="domcontentloaded")
    time.sleep(2)

    url = page.url.lower()
    if "login" not in url and "signin" not in url and "auth" not in url:
        try:
            page.wait_for_selector(SELECTORS["dashboard"], timeout=5000)
            return True
        except PwTimeout:
            pass

    username_input = page.locator(SELECTORS["login_username"]).first
    username_input.click()
    username_input.fill(username)

    password_input = page.locator(SELECTORS["login_password"]).first
    password_input.click()
    password_input.fill(password)

    page.locator(SELECTORS["login_submit"]).first.click()
    time.sleep(3)
    page.wait_for_load_state("domcontentloaded")

    # Check for login error
    try:
        error = page.locator(SELECTORS["login_error"]).first
        if error.is_visible(timeout=2000):
            return False
    except Exception:
        pass

    # Check if MFA is required
    if _is_mfa_page(page):
        return "mfa_required"

    # Check if we're still on login
    url = page.url.lower()
    if "login" in url or "signin" in url:
        return False

    return True


def _is_mfa_page(page) -> bool:
    """Detect if the current page is an MFA/verification challenge."""
    from playwright.sync_api import TimeoutError as PwTimeout

    # Check URL for MFA hints
    url = page.url.lower()
    if any(kw in url for kw in ("mfa", "otp", "verify", "2fa", "two-factor", "challenge", "totp")):
        return True

    # Check for MFA input field
    try:
        mfa_input = page.locator(SELECTORS["mfa_input"]).first
        if mfa_input.is_visible(timeout=2000):
            return True
    except (PwTimeout, Exception):
        pass

    # Check for MFA page markers (text content)
    try:
        mfa_marker = page.locator(SELECTORS["mfa_page"]).first
        if mfa_marker.is_visible(timeout=1000):
            return True
    except (PwTimeout, Exception):
        pass

    return False


def _cmd_submit_mfa(page, parser, code):
    """Submit MFA token and check if login completes.

    Returns: True (logged in), False (MFA failed / still on MFA page).
    """
    from playwright.sync_api import TimeoutError as PwTimeout

    # Find and fill the MFA input
    mfa_input = page.locator(SELECTORS["mfa_input"]).first
    mfa_input.click()
    mfa_input.fill(code)

    # Submit
    page.locator(SELECTORS["mfa_submit"]).first.click()
    time.sleep(3)
    page.wait_for_load_state("domcontentloaded")

    # Check for errors
    try:
        error = page.locator(SELECTORS["login_error"]).first
        if error.is_visible(timeout=2000):
            return False
    except Exception:
        pass

    # Still on MFA page? Failed.
    if _is_mfa_page(page):
        return False

    # Still on login page? Failed.
    url = page.url.lower()
    if "login" in url or "signin" in url:
        return False

    return True


def _cmd_search_devices(page, parser, search_key):
    from playwright.sync_api import TimeoutError as PwTimeout
    try:
        devices_link = page.locator(SELECTORS["devices_nav"]).first
        devices_link.click()
        time.sleep(2)
        page.wait_for_load_state("domcontentloaded")

        search_input = page.locator(SELECTORS["device_search"]).first
        search_input.click()
        search_input.fill("")
        search_input.fill(search_key)
        time.sleep(2)

        return _scrape_device_list(page)
    except PwTimeout:
        return []


def _scrape_device_list(page) -> list[DeviceInfo]:
    devices = []
    try:
        rows = page.locator(SELECTORS["device_rows"]).all()
        for i, row in enumerate(rows):
            text = row.inner_text().strip()
            if not text:
                continue
            parts = text.split("\t")
            if not parts:
                parts = text.split("\n")
            name = parts[0].strip() if parts else text[:50]
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


def _cmd_select_device(page, parser, row_index):
    from playwright.sync_api import TimeoutError as PwTimeout
    rows = page.locator(SELECTORS["device_rows"]).all()
    if row_index < len(rows):
        rows[row_index].click()
        time.sleep(2)
        page.wait_for_load_state("domcontentloaded")
    else:
        raise RuntimeError(f"Device row {row_index} not found")


def _cmd_open_console(page, parser):
    from playwright.sync_api import TimeoutError as PwTimeout
    page.locator(SELECTORS["troubleshooting_tab"]).first.click()
    time.sleep(2)

    page.locator(SELECTORS["remote_connect"]).first.click()
    time.sleep(2)

    try:
        page.locator(SELECTORS["console_option"]).first.click()
        time.sleep(1)
    except Exception:
        pass

    page.locator(SELECTORS["open_console_btn"]).first.click()
    time.sleep(3)

    page.wait_for_selector(SELECTORS["terminal"], timeout=15000)
    time.sleep(2)


def _cmd_check_port(page, parser, port_number):
    if port_number not in (1, 2, 3, 4):
        return PortResult(port=port_number, error="Invalid port number (must be 1-4)")

    try:
        terminal = page.locator(SELECTORS["terminal"]).first
        terminal.click()
        time.sleep(0.5)

        page.keyboard.type(f"serial --force {port_number}")
        page.keyboard.press("Enter")
        time.sleep(3)

        page.keyboard.press("Enter")
        time.sleep(1)
        page.keyboard.press("Enter")
        time.sleep(2)

        output = _read_terminal(page)

        if not output or not output.strip():
            page.keyboard.press("Enter")
            time.sleep(5)
            output = _read_terminal(page)

        result = parser.parse(output)

        return PortResult(
            port=port_number,
            raw_output=output,
            hostname=result.hostname,
            confidence=result.confidence,
            error=None if output.strip() else "No response from port",
        )
    except Exception as e:
        return PortResult(port=port_number, error=str(e))


def _cmd_check_all_ports(page, parser):
    results = []
    for port in range(1, 5):
        result = _cmd_check_port(page, parser, port)
        results.append(result)
        if port < 4:
            time.sleep(1)
    return results


def _read_terminal(page) -> str:
    try:
        text = page.evaluate("""
            () => {
                const rows = document.querySelectorAll('.xterm-rows > div');
                if (rows.length > 0) {
                    return Array.from(rows).map(r => r.textContent).join('\\n');
                }
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


def _cmd_take_screenshot(page, parser, path):
    page.screenshot(path=path)


def _cmd_go_back(page, parser):
    page.go_back()
    time.sleep(1)
    page.go_back()
    time.sleep(2)


# Command dispatch table
_COMMANDS = {
    "is_logged_in": _cmd_is_logged_in,
    "login": _cmd_login,
    "submit_mfa": _cmd_submit_mfa,
    "search_devices": _cmd_search_devices,
    "select_device": _cmd_select_device,
    "open_console": _cmd_open_console,
    "check_port": _cmd_check_port,
    "check_all_ports": _cmd_check_all_ports,
    "take_screenshot": _cmd_take_screenshot,
    "go_back": _cmd_go_back,
}


# ── Public API (called from main thread) ────────────────────────────────────

class NCMAutomation:
    """Automates Cradlepoint NCM web portal via Playwright.

    All Playwright operations run in a background thread to avoid
    event loop conflicts with questionary/prompt_toolkit.
    """

    def __init__(self, headless: bool = HEADLESS, timeout: int = BROWSER_TIMEOUT):
        self.headless = headless
        self.timeout = timeout
        self._cmd_q: queue.Queue = queue.Queue()
        self._result_q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self):
        """Launch browser in background thread."""
        if self._started:
            return

        self._thread = threading.Thread(
            target=_playwright_worker,
            args=(self.headless, self.timeout, self._cmd_q, self._result_q),
            daemon=True,
        )
        self._thread.start()

        # Wait for startup to complete
        status, err = self._result_q.get(timeout=60)
        if status == "error":
            raise RuntimeError(f"Failed to launch browser: {err}")
        self._started = True

    def close(self):
        """Stop the background thread and close the browser."""
        if self._started:
            self._cmd_q.put(_SENTINEL)
            if self._thread:
                self._thread.join(timeout=10)
            self._started = False

    def _call(self, method: str, *args, **kwargs):
        """Send a command to the worker thread and wait for the result."""
        if not self._started:
            raise RuntimeError("Browser not started. Call start() first.")
        self._cmd_q.put((method, args, kwargs))
        status, result = self._result_q.get(timeout=120)
        if status == "error":
            raise result
        return result

    # ── Public methods ──────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        return self._call("is_logged_in")

    def login(self, username: str, password: str) -> bool | str:
        """Returns True (success), False (failed), or "mfa_required"."""
        return self._call("login", username, password)

    def submit_mfa(self, code: str) -> bool:
        """Submit MFA/2FA verification code. Returns True if login completes."""
        return self._call("submit_mfa", code)

    def search_devices(self, search_key: str) -> list[DeviceInfo]:
        return self._call("search_devices", search_key)

    def select_device(self, device: DeviceInfo):
        return self._call("select_device", device.row_index)

    def open_console(self):
        return self._call("open_console")

    def check_port(self, port_number: int) -> PortResult:
        return self._call("check_port", port_number)

    def check_all_ports(self) -> list[PortResult]:
        return self._call("check_all_ports")

    def take_screenshot(self, path: str = "ncm_debug.png"):
        try:
            self._call("take_screenshot", path)
        except Exception:
            pass

    def go_back(self):
        """Navigate back (for returning to device list)."""
        self._call("go_back")
