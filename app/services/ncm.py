"""Playwright-based browser automation for Cradlepoint NCM console port access.

Playwright's sync API creates its own asyncio event loop, which conflicts with
questionary (which also uses asyncio.run). To avoid this, all Playwright work
runs in a dedicated background thread. The main thread calls methods that proxy
to the background thread via a simple request/response queue.
"""

import re
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
    # NOTE: NCM uses Ember.js which renders a hidden input (type="hidden") alongside
    # a visible text input. We must exclude hidden inputs to avoid clicking invisible elements.
    "mfa_input": 'input[type="text"][name="token"], input[type="number"][name="token"], input.ember-text-field:not([type="hidden"]), input[name="otp"]:not([type="hidden"]), input[name="mfa"]:not([type="hidden"]), input[name="code"]:not([type="hidden"]), input[name="token"]:not([type="hidden"]), input[name="totp"]:not([type="hidden"]), input[placeholder*="oken"]:not([type="hidden"]), input[placeholder*="MFA"]:not([type="hidden"])',
    "mfa_submit": 'button[type="submit"], input[type="submit"], button:has-text("Verify"), button:has-text("Submit"), button:has-text("Continue"), button:has-text("Confirm"), button:has-text("Log In"), button:has-text("Sign In")',
    "mfa_page": 'text=/MFA token|erification|two.factor|2fa|authenticator|one.time|MFA|security code/i, [class*="mfa"], [class*="otp"], [class*="two-factor"], [class*="verification"]',

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

    # Terminal — NCM console is an Ember.js app, NOT xterm.js.
    # The console area is typically a div/pre with black background containing the bash prompt.
    # The "Close Console" button appearing means the console IS open.
    "close_console_btn": 'button:has-text("Close Console"), a:has-text("Close Console"), :has-text("Close Console")',
    "terminal": '.xterm, .terminal, [class*="terminal"], .xterm-screen, [class*="console-output"], [class*="console-terminal"], [class*="ember-view"][style*="background"], pre[style*="background"]',
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

    # Find a VISIBLE MFA input — try selector-based first, then JS fallback
    filled = False
    try:
        mfa_input = page.locator(SELECTORS["mfa_input"])
        # Iterate to find one that's actually visible
        for i in range(mfa_input.count()):
            el = mfa_input.nth(i)
            if el.is_visible(timeout=1000):
                el.click()
                el.fill(code)
                filled = True
                break
    except Exception:
        pass

    if not filled:
        # JS fallback: find any visible text/number input on the page that looks like MFA
        page.evaluate(f"""(code) => {{
            const inputs = document.querySelectorAll('input[type="text"], input[type="number"], input[type="tel"], input:not([type])');
            for (const inp of inputs) {{
                if (inp.offsetParent !== null && inp.type !== 'hidden') {{
                    inp.focus();
                    inp.value = code;
                    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    break;
                }}
            }}
        }}""", code)
        filled = True
        time.sleep(0.5)

    # Submit
    page.locator(SELECTORS["mfa_submit"]).first.click()

    # Wait longer — NCM can be slow to process MFA and redirect
    time.sleep(5)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    # Give the page a moment to settle after navigation
    time.sleep(2)

    # Check URL first — if we've navigated away from login/mfa, we're in
    url = page.url.lower()
    on_login_page = any(kw in url for kw in ("login", "signin", "auth", "mfa", "verify", "2fa", "challenge"))

    if not on_login_page:
        # We navigated away from auth pages — success
        return True

    # Check for explicit error messages
    try:
        error = page.locator(SELECTORS["login_error"]).first
        if error.is_visible(timeout=1000):
            return False
    except Exception:
        pass

    # If URL still looks like auth but no error — give it more time
    # (some sites do a client-side redirect after MFA)
    time.sleep(3)
    url = page.url.lower()
    on_login_page = any(kw in url for kw in ("login", "signin", "auth", "mfa", "verify", "2fa", "challenge"))

    if not on_login_page:
        return True

    # Still on auth page — likely failed
    return False


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
        page.keyboard.press("Enter")
        time.sleep(3)
        page.wait_for_load_state("domcontentloaded")

        return _scrape_device_list(page)
    except PwTimeout:
        return []


def _scrape_device_list(page) -> list[DeviceInfo]:
    """Scrape all devices, scrolling to load any that are below the fold."""
    devices = []
    try:
        # Scroll down repeatedly to load all devices (NCM may lazy-load or paginate)
        prev_count = 0
        for _ in range(20):  # max 20 scroll attempts
            rows = page.locator(SELECTORS["device_rows"]).all()
            current_count = len(rows)
            if current_count == prev_count:
                break  # no new rows loaded, we have them all
            prev_count = current_count
            # Scroll the last row into view to trigger loading more
            if rows:
                rows[-1].scroll_into_view_if_needed()
                time.sleep(1)

        # Now scrape all rows
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


def _wait_for_console_ready(page, timeout_seconds: int) -> bool:
    """Poll for up to `timeout_seconds` seconds for the console to be ready.

    Returns True the moment any of these signals fire:
      - "Close Console" button visible (strongest signal — console is open)
      - Shell prompt pattern found in body text
      - Any terminal-like DOM element is visible
    Returns False if timeout is hit without detecting readiness.
    """
    iterations = max(1, timeout_seconds // 5)
    for _ in range(iterations):
        time.sleep(5)

        # Close Console button → console is open
        try:
            if page.locator(SELECTORS["close_console_btn"]).first.is_visible(timeout=1000):
                time.sleep(2)  # brief settle
                return True
        except Exception:
            pass

        # Shell prompt in body
        try:
            has_prompt = page.evaluate("""() => {
                const body = document.body.innerText || '';
                return /[@].*[$#]\\s*$|\\]\\$\\s*$/m.test(body);
            }""")
            if has_prompt:
                time.sleep(2)
                return True
        except Exception:
            pass

        # Terminal DOM
        for selector in [
            SELECTORS["terminal"],
            "iframe", "canvas",
            "[class*='console-output']", "[class*='console-terminal']",
        ]:
            try:
                if page.locator(selector).first.is_visible(timeout=500):
                    time.sleep(2)
                    return True
            except Exception:
                continue

    return False


def _cmd_open_console(page, parser):
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

    if _wait_for_console_ready(page, timeout_seconds=360):
        return "auto_detected"

    # Timeout — could not detect console
    debug_path = str(Path.home() / ".console-check" / "ncm_console_debug.png")
    try:
        Path(debug_path).parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=debug_path)
    except Exception:
        debug_path = None

    return {"status": "manual_confirm_needed", "screenshot": debug_path}


def _close_and_reopen_console(page, timeout_seconds: int = 180) -> bool:
    """Click "Close Console", wait 2s, click "Open Console", wait up to
    `timeout_seconds` for the console to come back up.  Returns True on
    success.  Used between ports — Cradlepoint doesn't cleanly switch
    sessions with ~. or --force, so a full close/reopen cycle is the
    reliable way to move to the next port.
    """
    try:
        page.locator(SELECTORS["close_console_btn"]).first.click()
    except Exception:
        # Can't find Close Console — maybe the console is already closed.
        # Try to push on and reopen anyway.
        pass
    time.sleep(2)

    try:
        page.locator(SELECTORS["open_console_btn"]).first.click()
    except Exception as e:
        raise RuntimeError(f"Could not click Open Console after close: {e}")

    return _wait_for_console_ready(page, timeout_seconds=timeout_seconds)


def _cmd_wait_for_user(page, parser):
    """Called after user confirms the console is ready. Just a small pause."""
    time.sleep(1)
    return True


def _type_into_terminal(page, text, press_enter=True):
    """Type text (and optionally Enter) into the NCM terminal.

    The terminal lives inside a cross-origin iframe
    (https://aoobm-haproxy.cradlepointecm.com).  We iterate iframes first,
    then fall back to the main frame.  For each frame we try textarea
    selectors in order of specificity — the first one that has a matching
    element in the DOM gets focused and typed into.  Then we return — no
    verification, no multi-strategy retries.  Those caused 5× duplicate
    typing when echo timing made verification fail.
    """
    # Collect frames: iframes first (where the terminal is), main frame last.
    frames: list = []
    try:
        for frame in page.frames:
            if frame != page.main_frame:
                frames.append(frame)
    except Exception:
        pass
    frames.append(page.main_frame)

    textarea_sels = [
        "textarea.xterm-helper-textarea",    # xterm.js canonical
        ".xterm textarea",
        ".terminal textarea",
        "[class*='terminal'] textarea",
        "[class*='console'] textarea",
        "[class*='shell'] textarea",
        "[class*='remote'] textarea",
        "textarea",                           # last-resort: any textarea
    ]

    for frame in frames:
        for sel in textarea_sels:
            try:
                loc = frame.locator(sel).first
                if loc.count() == 0:
                    continue
                loc.focus(timeout=1500)
                time.sleep(0.1)
                if text:
                    loc.type(text, delay=30)
                if press_enter:
                    loc.press("Enter")
                time.sleep(0.3)
                return True
            except Exception:
                continue

    # Last-ditch fallback: raw page.keyboard (relies on whatever currently has focus)
    try:
        if text:
            page.keyboard.type(text, delay=30)
        if press_enter:
            page.keyboard.press("Enter")
        time.sleep(0.3)
        return True
    except Exception:
        return False


def _save_debug_screenshot(page, label: str) -> str | None:
    """Save a debug screenshot and return the path."""
    try:
        debug_dir = Path.home() / ".console-check"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = str(debug_dir / f"debug_{label}.png")
        page.screenshot(path=path)
        return path
    except Exception:
        return None


_PROMPT_RE = re.compile(r"[\$#>]\s*$")


def _last_nonempty_line(text: str) -> str:
    """Return the last non-empty trimmed line of `text`, or '' if none."""
    if not text:
        return ""
    for line in reversed(text.rstrip().splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _wait_for_prompt(page, timeout: float = 10.0) -> bool:
    """Poll the terminal for a shell prompt at end of last line.

    Returns True if we see a line ending with $, #, or > within the timeout.
    This is how we verify the terminal is actually responsive before firing
    off the next command — instead of blindly sleeping and hoping.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = _read_terminal(page)
        last = _last_nonempty_line(output)
        if last and _PROMPT_RE.search(last):
            return True
        time.sleep(0.5)
    return False


def _ping_terminal(page, timeout: float = 6.0) -> bool:
    """Nudge the terminal with a bare Enter and confirm it responded.

    Two independent signals count as "responsive":
      1. Output grew (new bytes appeared after pressing Enter) — most reliable
      2. Last non-empty line ends with $, #, or > (prompt regex)

    Growth is the primary signal because it doesn't depend on any assumption
    about prompt format.  A bash prompt echo, a cursor move, or any terminal
    update will grow the output.
    """
    before = _read_terminal(page) or ""
    before_len = len(before)
    _type_into_terminal(page, "", press_enter=True)

    deadline = time.time() + timeout
    while time.time() < deadline:
        after = _read_terminal(page) or ""
        if len(after) > before_len:
            return True
        last = _last_nonempty_line(after)
        if last and _PROMPT_RE.search(last):
            return True
        time.sleep(0.3)
    return False


def _cmd_check_port(page, parser, port_number):
    """Run `serial --force N` and capture what the attached device says.

    Flow:
      1. Ping the terminal (bare Enter) — non-fatal responsiveness check
      2. Send `serial --force N` + Enter
      3. Wait 5s for the Cradlepoint serial session to attach (banner appears)
      4. Snapshot output — this is our "empty port" baseline
      5. Press Enter 3 times (2s apart) to elicit a response from the device
      6. Read output again; anything beyond the baseline is the device's reply
      7. Parse hostname from the device reply

    Does NOT close the console — the caller (`_cmd_check_all_ports`) is
    responsible for close/reopen cycles between ports.
    """
    if port_number not in (1, 2, 3, 4):
        return PortResult(port=port_number, error="Invalid port number (must be 1-4)")

    try:
        # 1. Responsiveness check — informational only, not blocking.
        if not _ping_terminal(page, timeout=6):
            _save_debug_screenshot(page, f"port{port_number}_ping_inconclusive")

        # 2. Send serial --force N.
        cmd = f"serial --force {port_number}"
        sent = _type_into_terminal(page, cmd, press_enter=True)
        if not sent:
            shot = _save_debug_screenshot(page, f"port{port_number}_no_input")
            msg = "Could not send command to terminal"
            if shot:
                msg += f" — screenshot: {shot}"
            return PortResult(port=port_number, error=msg)

        # 3. Wait for the Cradlepoint serial-session banner.
        time.sleep(5)

        # 4. Baseline: whatever's in the terminal RIGHT after the banner.
        #    Anything that appears after this point came from the attached
        #    device in response to our Enter presses.
        baseline = _read_terminal(page) or ""
        baseline_len = len(baseline)

        # 5. Three Enters, 2s apart, to wake the device.
        for _ in range(3):
            _type_into_terminal(page, "", press_enter=True)
            time.sleep(2)

        # 6. Read final output and extract the device's reply.
        final_output = _read_terminal(page) or ""
        if len(final_output) > baseline_len and final_output.startswith(baseline):
            device_reply = final_output[baseline_len:]
        else:
            # Output diverged from baseline (scrolled / re-rendered) — use it whole.
            device_reply = final_output

        # 7. Parse hostname from whatever the device gave back.
        result = parser.parse(device_reply) if device_reply else parser.parse("")

        # Classify: hostname found > got some reply > port empty.
        if result.hostname:
            error = None
        elif device_reply.strip():
            error = "Got response but no hostname detected"
        else:
            error = "No response from port — likely no device attached"

        return PortResult(
            port=port_number,
            raw_output=device_reply,
            hostname=result.hostname,
            confidence=result.confidence,
            error=error,
        )
    except Exception as e:
        return PortResult(port=port_number, error=str(e))


def _cmd_check_all_ports(page, parser):
    """Check ports 1–4, closing and reopening the console between ports.

    Cradlepoint's serial session doesn't cleanly hand off to the next port
    with `serial --force` or `~.` — the only reliable reset is to close the
    console entirely and reopen it.  First reopen can take a while, but
    subsequent ones usually come back within ~180s.
    """
    results = []
    for port in range(1, 5):
        if port > 1:
            try:
                ready = _close_and_reopen_console(page, timeout_seconds=180)
                if not ready:
                    shot = _save_debug_screenshot(page, f"port{port}_reopen_timeout")
                    msg = "Console did not come back within 180s after reopen"
                    if shot:
                        msg += f" — screenshot: {shot}"
                    results.append(PortResult(port=port, error=msg))
                    continue
            except Exception as e:
                shot = _save_debug_screenshot(page, f"port{port}_reopen_failed")
                msg = f"Close/reopen failed: {e}"
                if shot:
                    msg += f" — screenshot: {shot}"
                results.append(PortResult(port=port, error=msg))
                continue

        result = _cmd_check_port(page, parser, port)
        results.append(result)
    return results


_TERMINAL_IFRAME_HINTS = ("aoobm", "haproxy", "console", "terminal", "remote")


def _read_terminal(page) -> str:
    """Scrape text content from the NCM console terminal.

    The terminal lives inside a cross-origin iframe served from
    aoobm-haproxy.cradlepointecm.com.  We check iframes FIRST (prioritizing
    URLs that look terminal-related), then fall back to the main frame.
    Cross-origin iframes can't be read via the parent's innerText, so
    iframe-first is the only reliable path.
    """
    # ── Approach 1: iframes (terminal lives here) ──────────────────────────
    try:
        frames = list(page.frames)
        prioritized = []
        others = []
        for frame in frames:
            if frame == page.main_frame:
                continue
            url = (frame.url or "").lower()
            if any(h in url for h in _TERMINAL_IFRAME_HINTS):
                prioritized.append(frame)
            else:
                others.append(frame)

        for frame in prioritized + others:
            try:
                text = frame.evaluate("""() => {
                    // Prefer xterm.js rows if present
                    const rows = document.querySelectorAll('.xterm-rows > div');
                    if (rows.length > 0) {
                        return Array.from(rows).map(r => r.textContent).join('\\n');
                    }
                    return document.body
                        ? (document.body.innerText || document.body.textContent || '')
                        : '';
                }""")
                if text and text.strip():
                    return text
            except Exception:
                continue
    except Exception:
        pass

    # ── Approach 2: main frame (fallback — rarely hits) ────────────────────
    try:
        text = page.evaluate("""() => {
            const xtermRows = document.querySelectorAll('.xterm-rows > div');
            if (xtermRows.length > 0) {
                return Array.from(xtermRows).map(r => r.textContent).join('\\n');
            }
            // Look for dark-bg terminal container
            const allEls = document.querySelectorAll('div, pre, textarea, span');
            let bestTerminal = null;
            let bestArea = 0;
            for (const el of allEls) {
                const bg = window.getComputedStyle(el).backgroundColor;
                const isBlack = bg === 'rgb(0, 0, 0)' || bg === '#000' || bg === '#000000'
                    || bg === 'rgb(30, 30, 30)' || bg === 'rgb(33, 33, 33)';
                if (isBlack && el.offsetHeight > 30 && el.offsetWidth > 100) {
                    const area = el.offsetHeight * el.offsetWidth;
                    if (area > bestArea) { bestArea = area; bestTerminal = el; }
                }
            }
            if (bestTerminal) {
                return bestTerminal.innerText || bestTerminal.textContent || '';
            }
            return '';
        }""")
        if text and text.strip():
            return text
    except Exception:
        pass

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
    "wait_for_user": _cmd_wait_for_user,
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

    def _call(self, method: str, *args, timeout: int = 600, **kwargs):
        """Send a command to the worker thread and wait for the result.

        Default timeout is 600s — long commands like open_console (up to 360s)
        and check_all_ports (multiple ports × retries) need the extra headroom.
        """
        if not self._started:
            raise RuntimeError("Browser not started. Call start() first.")
        self._cmd_q.put((method, args, kwargs))
        status, result = self._result_q.get(timeout=timeout)
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
        """Returns "auto_detected" or {"status": "manual_confirm_needed", "screenshot": path}."""
        return self._call("open_console")

    def wait_for_user(self):
        """Call after user confirms console is ready."""
        return self._call("wait_for_user")

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
