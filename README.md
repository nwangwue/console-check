# console-check

CLI tool for verifying Cisco device readiness on Cradlepoint E100 console ports before SDWAN migration cutovers.

Engineers use this tool to import a master site list, look up customers by name to find their Cradlepoint search key, paste console output from NCM, and track port verification status across 300+ sites.

## Quick Start

```powershell
# One-time setup: create venv OUTSIDE OneDrive (OneDrive corrupts .venv symlinks)
python -m uv venv C:\Users\%USERNAME%\.venvs\console-check --python 3.14

# Set this env var so uv finds the venv (add to your PowerShell profile for permanence)
$env:UV_PROJECT_ENVIRONMENT = "C:\Users\$env:USERNAME\.venvs\console-check"

# Install dependencies
python -m uv sync

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

1. **Import** a master site list spreadsheet (xlsx/csv) with interactive column mapping
2. **Search** by customer/bank name, city, site ID, or UNLOCODE to find sites and their CP search key
3. **Verify** console ports — paste Cisco IOS-XE output, tool extracts the hostname and compares against expected devices
4. **Detect swaps** — if a hostname appears on the wrong port, the tool identifies the swap and lets you accept it
5. **Track readiness** — dashboard shows ready/issues/partial/not checked counts across all sites
6. **Export** CSV readiness reports for stakeholders

## Workflow

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌───────────┐
│ Import xlsx  │────>│ Search site  │────>│ Verify ports │────>│ Dashboard │
│ (one-time)   │     │ by customer  │     │ paste output │     │ + Export  │
└─────────────┘     └──────────────┘     └──────────────┘     └───────────┘
```

### Typical Use During a Cutover

1. Search for the customer name (e.g., "First National Bank")
2. Copy the **CP Search Key** (e.g., `USDAL`) displayed in the tool
3. Paste it into NCM to find the Cradlepoint
4. For each console port, copy the output from NCM and paste it into the tool
5. Tool extracts the hostname and tells you: match, mismatch, or swapped
6. Move to the next site

## Project Structure

```
console-check/
├── app/
│   ├── cli.py              # Main interactive CLI (entry point)
│   ├── cli_helpers.py       # Rich terminal rendering (tables, panels)
│   ├── config.py            # Configuration (DB path, CSV path)
│   ├── database.py          # SQLAlchemy engine + session
│   ├── models/              # SQLAlchemy models
│   │   ├── site.py          #   Site (id, city, state, bank_name, unlocode, status)
│   │   ├── device.py        #   Device (expected hostname per port)
│   │   └── verification.py  #   Verification (audit trail of checks)
│   ├── schemas/             # Pydantic data models
│   │   ├── import_config.py #   Column mapping + import results
│   │   └── verification.py  #   Verify result model
│   └── services/            # Business logic
│       ├── unlocode.py      #   City+State → UNLOCODE resolution
│       ├── hostname_parser.py#  Extract hostname from Cisco console output
│       ├── verifier.py      #   Port verification + swap detection
│       ├── importer.py      #   Spreadsheet import + column mapping
│       └── exporter.py      #   CSV readiness report export
├── data/
│   └── us_locode.csv        # ~21K US UN/LOCODE entries
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
- Falls back to manual resolution with suggestions if no match found

### Hostname Parser
Extracts Cisco IOS-XE hostnames from raw console output including:
- Enable mode prompts (`HOSTNAME#`)
- User mode prompts (`HOSTNAME>`)
- Config mode (`HOSTNAME(config)#`)
- `show run` output (`hostname HOSTNAME`)
- Post-TACACS-rejection prompts
- Strips ANSI escape codes automatically

### Swap Detection
If you paste console output for Port 3 and the hostname matches what's expected on Port 1, the tool detects this as a cable swap and offers to accept it — updating the expected port assignments without requiring re-cabling.

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_PATH` | `console_check.db` | SQLite database file path |
| `LOCODE_CSV_PATH` | `data/us_locode.csv` | Path to US UNLOCODE dataset |
| `FUZZY_MATCH_THRESHOLD` | `85` | Minimum fuzzy match score (0-100) |
| `CONSOLE_CHECK_ENGINEER` | _(empty)_ | Pre-fill engineer name on startup |

## Development

```bash
# Install with dev dependencies
python -m uv sync --extra dev

# Run tests
python -m uv run pytest -v

# Run the CLI
python -m uv run console-check
```

## Tech Stack

- **Python 3.11+**
- **SQLAlchemy** + SQLite for persistence
- **Rich** for terminal UI (tables, panels, colors)
- **Questionary** for interactive prompts
- **rapidfuzz** for fuzzy city name matching
- **openpyxl** for Excel file reading
- **Pydantic** for data validation
