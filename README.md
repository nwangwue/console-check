# console-check

CLI tool that automates Cradlepoint console port verification for SDWAN migration cutovers. Enter a city and state, and the tool resolves the UNLOCODE, opens NCM in a browser, finds the matching Cradlepoints, and checks all 4 console ports automatically.

## Quick Start

```powershell
# One-time setup: create venv OUTSIDE OneDrive (OneDrive corrupts .venv symlinks)
python -m uv venv C:\Users\%USERNAME%\.venvs\console-check --python 3.14

# Set this env var so uv finds the venv (add to your PowerShell profile for permanence)
$env:UV_PROJECT_ENVIRONMENT = "C:\Users\$env:USERNAME\.venvs\console-check"

# Install dependencies
python -m uv sync

# Install browser for automation (one-time)
python -m uv run playwright install chromium

# Run the tool
python -m uv run console-check
```

### Why the external venv?

WWT syncs the project folder via OneDrive, which corrupts Python virtual environments (deletes symlinked executables, locks `.exe` files). Placing `.venv` outside OneDrive avoids this entirely.

To make it permanent, add this to your PowerShell profile (`$PROFILE`):

```powershell
$env:UV_PROJECT_ENVIRONMENT = "C:\Users\$env:USERNAME\.venvs\console-check"
```

## What It Does

1. **Enter city + state** (e.g., "Dallas", "TX")
2. **Resolve UNLOCODE** — tool finds the 3-character location code (e.g., `DAL`)
3. **Open NCM** — browser launches, logs in, searches for Cradlepoints matching `USDAL`
4. **Pick a device** — tool shows matching devices, engineer selects one
5. **Check all 4 ports** — tool runs `serial --force 1` through `serial --force 4`
6. **Display results** — hostname found on each port displayed in a table

## Workflow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Enter city   │────>│ Resolve      │────>│ Search NCM   │────>│ Check all 4  │
│ + state      │     │ UNLOCODE     │     │ pick device  │     │ console ports│
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

### Typical Use During a Cutover

1. Run `console-check`
2. Enter the site's city and state
3. Tool resolves the UNLOCODE and shows the CP search key
4. Browser opens NCM automatically — log in if prompted
5. Tool searches for matching Cradlepoints and lists them
6. Pick the right device from the list
7. Tool connects to console and checks all 4 serial ports
8. See which hostname is on each port — done!

## Project Structure

```
console-check/
├── app/
│   ├── cli.py               # Main interactive CLI (entry point)
│   ├── cli_helpers.py        # Rich terminal rendering (tables, panels)
│   ├── config.py             # Configuration (NCM URL, Playwright settings)
│   └── services/
│       ├── unlocode.py       #   City+State → UNLOCODE resolution
│       ├── hostname_parser.py#   Extract hostname from Cisco console output
│       └── ncm.py            #   Playwright browser automation for NCM
├── data/
│   └── us_locode.csv         # ~21K US UN/LOCODE entries
├── tests/
│   ├── test_hostname_parser.py
│   └── test_unlocode.py
└── pyproject.toml
```

## Key Features

### UNLOCODE Resolution
Automatically resolves city + state to a 3-character UN/LOCODE using a 3-tier strategy:
- **Exact match** — "Dallas" + "TX" → `DAL`
- **Normalized match** — "St. Louis" → "Saint Louis" → `STL`
- **Fuzzy match** — "Springfild" → "Springfield" (≥85% similarity)
- Falls back to suggestions or manual entry if no match found

### Hostname Parser
Extracts Cisco IOS-XE hostnames from raw console output including:
- Enable mode prompts (`HOSTNAME#`)
- User mode prompts (`HOSTNAME>`)
- Config mode (`HOSTNAME(config)#`)
- `show run` output (`hostname HOSTNAME`)
- Post-TACACS-rejection prompts
- Strips ANSI escape codes automatically

### NCM Browser Automation
- Uses Playwright to automate the Cradlepoint NCM web portal
- Persistent browser context — session cookies survive between runs, so you don't re-login every time
- Navigates: Devices → filter → select device → Troubleshooting → Remote Connect → Console
- Runs `serial --force [1-4]` to check each port
- Extracts terminal output via DOM scraping (xterm.js)

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `NCM_URL` | `https://www.cradlepointecm.com` | NCM instance URL |
| `LOCODE_CSV_PATH` | `data/us_locode.csv` | Path to US UNLOCODE dataset |
| `FUZZY_MATCH_THRESHOLD` | `85` | Minimum fuzzy match score (0-100) |
| `CONSOLE_CHECK_HEADLESS` | `false` | Run browser headless (no visible window) |
| `CONSOLE_CHECK_TIMEOUT` | `30000` | Browser operation timeout in milliseconds |

**Note:** NCM credentials are always prompted interactively and never stored in environment variables or files.

## Development

```powershell
# Install with dev dependencies
python -m uv sync --extra dev

# Run tests
python -m uv run pytest -v

# Run the CLI
python -m uv run console-check
```

## Troubleshooting

### NCM selectors not working
If NCM updates their UI, the CSS selectors in `app/services/ncm.py` may need updating. The tool saves a debug screenshot (`ncm_debug.png`) when navigation fails. Open NCM in Chrome DevTools to find the new selectors and update the `SELECTORS` dict at the top of `ncm.py`.

### Browser won't launch
Run `python -m uv run playwright install chromium` to re-install the browser binaries. They're stored in `%USERPROFILE%\AppData\Local\ms-playwright`.

### OneDrive .venv issues
See the Quick Start section — the venv must be outside the OneDrive-synced folder.

## Tech Stack

- **Python 3.11+**
- **Playwright** for NCM browser automation
- **Rich** for terminal UI (tables, panels, colors)
- **Questionary** for interactive prompts
- **rapidfuzz** for fuzzy city name matching
- **Pydantic** for data validation
