# Split result_scrapper.py into Local + Colab Scripts

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the single `result_scrapper.py` (~956 lines) into a shared library (`scraper_common.py`) and two thin entry points (`result_scrapper.py` for local, `colab_scrapper.py` for Colab), eliminating all `IN_COLAB` branching while preserving every feature.

**Architecture:** `scraper_common.py` holds all shared logic (pure functions, config, progress, sheet ops, scraping, CLI). Two entry points set environment-specific constants and call into it. No code duplication. Each entry point is <80 lines.

**Tech Stack:** Python 3.14, Selenium (Firefox local / Chrome Colab), gspread, BeautifulSoup, argparse.

---

## File Structure

| File | Responsibility | Lines (est.) |
|------|---------------|-------------|
| `scraper_common.py` | All shared logic: functions, config, progress, sheet ops, scraping, CLI parsing, `main()` | ~870 |
| `result_scrapper.py` | Local entry point: Firefox, local paths, no drive mount | ~50 |
| `colab_scrapper.py` | Colab entry point: Chrome, Drive mount, Colab paths | ~60 |
| `tests/test_scraper_common.py` | Unit tests (renamed from test_result_scrapper.py) | ~700 |

`result_scrapper.py` stays as the filename for local so existing workflows (`python result_scrapper.py`) are unaffected.

---

## How the Split Works

### Environment-specific values (set by entry points)

| Constant | Local (`result_scrapper.py`) | Colab (`colab_scrapper.py`) |
|----------|-----|-----|
| `DATA_DIR` | `script_dir/data` | `/content/drive/MyDrive/ResultScraperData` |
| `CREDENTIALS_FILE` | `script_dir/credentials.json` | `DATA_DIR/credentials.json` |
| `ENV_FILE` | `script_dir/.env` | `DATA_DIR/.env` |
| `BROWSER` | `"firefox"` | `"chrome"` |
| Drive mount | none | `drive.mount('/content/drive')` |
| pip install | none | Chromium + deps |

### Interface: `scraper_common.configure()`

Each entry point calls `configure()` before anything else:

```python
scraper_common.configure(
    data_dir="/path/to/data",
    credentials_file="/path/to/credentials.json",
    env_file="/path/to/.env",
    browser="firefox",  # or "chrome"
)
```

This sets module-level state (`DATA_DIR`, `CREDENTIALS_FILE`, `ENV_FILE`, `CONFIG`, `GOOGLE_SHEET_URL`, `WORKSHEET_NAME`, `FORM_DATA`, `START_REGI`, `END_REGI`, etc.) so the rest of the shared code works unchanged.

### Interface: `scraper_common.run()`

Entry points call `run(args)` which runs the CLI arg parsing and main logic:

```python
if __name__ == '__main__':
    scraper_common.configure(...)
    scraper_common.run()
```

### `initialize_webdriver()` takes a `browser` parameter

```python
def initialize_webdriver(browser=None):
    """Initializes Selenium WebDriver. browser: 'firefox' or 'chrome'."""
    if browser is None:
        browser = BROWSER  # module-level, set by configure()
    options = Options[browser]()  # imported at top based on browser
    ...
```

Actually cleaner: import both Options at the top of `scraper_common.py`:

```python
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
```

Then `initialize_webdriver()` selects based on `BROWSER`.

---

## Task 1: Create `scraper_common.py` from existing code

**Files:**
- Create: `scraper_common.py`
- Read: `result_scrapper.py` (full file for reference)

**What moves:** Everything except the environment-specific bootstrapping (lines 1–87 of current `result_scrapper.py`).

**What changes:**
1. No `IN_COLAB`, no `setup_environment()`, no Drive mount, no conditional imports
2. Import both `ChromeOptions` and `FirefoxOptions` unconditionally
3. Add `configure(data_dir, credentials_file, env_file, browser)` function at the top
4. Replace module-level `CONFIG = load_config()` with lazy init inside `configure()`
5. `load_config()`, `load_env()`, `save_config()`, `save_env()` take explicit paths (or use module-level `DATA_DIR`/`ENV_FILE` set by `configure()`)
6. `initialize_webdriver()` uses module-level `BROWSER` (set by `configure()`)
7. `get_worksheet()` always checks `os.path.exists(CREDENTIALS_FILE)` (no IN_COLAB guard)
8. CLI argparse + `if __name__ == '__main__'` block moves to `run()` function
9. Remove `if IN_COLAB or __name__ == '__main__'` — just `if __name__ == '__main__'` (via `run()`)

**Steps:**

- [ ] **Step 1: Create `scraper_common.py` with the `configure()` function and imports**

```python
# scraper_common.py
# Shared logic for DUCMC Result Scraper.
# Import this from result_scrapper.py (local) or colab_scrapper.py (Colab).
# Must call configure() before using any other function.

import sys
import os
import time
import json
import html as html_module
import re
import hashlib
import argparse
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions

LOG_FILE = None

# --- Module-level state (set by configure()) ---
DATA_DIR = None
CREDENTIALS_FILE = None
ENV_FILE = None
BROWSER = "firefox"
CONFIG = {}
GOOGLE_SHEET_URL = ""
WORKSHEET_NAME = ""
FORM_DATA = {"program": "", "session": "", "exam": ""}
URL = "https://ducmc.du.ac.bd/result.php"
START_REGI = 710
END_REGI = 813
REQUEST_DELAY = 1
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

DEFAULTS = {
    "google_sheet_url": "",
    "worksheet_name": "",
    "program": "B.Sc. in Computer Science and Engineering",
    "session": "2021-2022",
    "exam": "",
    "start_regi": 710,
    "end_regi": 813,
    "request_delay": 1,
}


def configure(data_dir, credentials_file, env_file, browser="firefox"):
    """Set up module-level state. Call once at startup before anything else."""
    global DATA_DIR, CREDENTIALS_FILE, ENV_FILE, BROWSER
    global CONFIG, GOOGLE_SHEET_URL, WORKSHEET_NAME, FORM_DATA
    global START_REGI, END_REGI, REQUEST_DELAY

    DATA_DIR = data_dir
    CREDENTIALS_FILE = credentials_file
    ENV_FILE = env_file
    BROWSER = browser

    os.makedirs(DATA_DIR, exist_ok=True)

    CONFIG = load_config()
    GOOGLE_SHEET_URL = CONFIG["google_sheet_url"]
    WORKSHEET_NAME = CONFIG["worksheet_name"]
    FORM_DATA = {
        "program": CONFIG["program"],
        "session": CONFIG["session"],
        "exam": CONFIG["exam"],
    }
    START_REGI = CONFIG["start_regi"]
    END_REGI = CONFIG["end_regi"]
    REQUEST_DELAY = CONFIG["request_delay"]


def ts(msg):
    """Print a message with [HH:MM:SS] timestamp prefix, optionally to log file."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    if LOG_FILE:
        LOG_FILE.write(line + "\n")
        LOG_FILE.flush()
```

- [ ] **Step 2: Paste all remaining functions from `result_scrapper.py`**

Move these functions verbatim (they already use module-level globals set by `configure()`):
- `sanitize_text()`, `normalize_text()`, `format_roll_number()`, `parse_result_html()`
- `load_env()`, `save_env()`, `save_config()`, `load_config()`
- `first_run_setup()`
- `_make_progress_key()`, `load_progress()`, `save_progress()`
- `get_worksheet()`, `get_sheet_data()`, `setup_course_columns()`, `set_column_widths()`
- `update_sheet_with_student_data()`, `format_gpa_cgpa_columns()`
- `scrape_student_result()`
- `select_exam()`, `show_config()`, `show_status()`, `validate_config()`
- `main()`

Changes within these functions:
- `get_worksheet()`: remove `if not IN_COLAB and` — always check `os.path.exists(CREDENTIALS_FILE)`
- `initialize_webdriver()`: use `BROWSER` module-level var to pick `ChromeOptions` vs `FirefoxOptions`

- [ ] **Step 3: Add `run()` function with CLI arg parsing**

Move the `if __name__ == '__main__'` block into `run()`:

```python
def run():
    """CLI entry point. Parses args, runs first-run setup if needed, then main()."""
    global LOG_FILE

    parser = argparse.ArgumentParser(description="DUCMC Result Scraper")
    parser.add_argument("--list-exams", action="store_true", help="Interactively select an exam")
    parser.add_argument("--force", action="store_true", help="Force re-selection of exam")
    parser.add_argument("--fresh", action="store_true", help="Clear progress and start fresh")
    parser.add_argument("--dry-run", action="store_true", help="Scrape but don't write to sheet")
    parser.add_argument("--log", action="store_true", help="Log output to file")
    parser.add_argument("--reg", type=int, help="Scrape a single registration number")
    parser.add_argument("--validate", action="store_true", help="Test browser + sheet connection")
    parser.add_argument("--show-config", action="store_true", help="Print active config")
    parser.add_argument("--status", action="store_true", help="Show progress status")
    parser.add_argument("--sheet-url", help="Override Google Sheet URL")
    parser.add_argument("--worksheet", help="Override worksheet name")
    parser.add_argument("--program", help="Override program name")
    parser.add_argument("--session", help="Override session")
    parser.add_argument("--start-regi", type=int, help="Override start registration number")
    parser.add_argument("--end-regi", type=int, help="Override end registration number")
    args = parser.parse_args()

    # Apply CLI overrides
    if args.sheet_url:
        CONFIG["google_sheet_url"] = args.sheet_url
    if args.worksheet:
        CONFIG["worksheet_name"] = args.worksheet
    if args.program:
        CONFIG["program"] = args.program
    if args.session:
        CONFIG["session"] = args.session
    if args.start_regi is not None:
        CONFIG["start_regi"] = args.start_regi
    if args.end_regi is not None:
        CONFIG["end_regi"] = args.end_regi

    # Rebuild module-level constants from potentially-overridden config
    global GOOGLE_SHEET_URL, WORKSHEET_NAME, FORM_DATA, START_REGI, END_REGI
    GOOGLE_SHEET_URL = CONFIG["google_sheet_url"]
    WORKSHEET_NAME = CONFIG["worksheet_name"]
    FORM_DATA["program"] = CONFIG["program"]
    FORM_DATA["session"] = CONFIG["session"]
    FORM_DATA["exam"] = CONFIG["exam"]
    START_REGI = CONFIG["start_regi"]
    END_REGI = CONFIG["end_regi"]

    if args.list_exams:
        select_exam(force=args.force)
    elif args.validate:
        validate_config()
    elif args.show_config:
        show_config()
    elif args.status:
        show_status()
    else:
        # First-run setup
        if not CONFIG.get("google_sheet_url") or not CONFIG.get("worksheet_name"):
            try:
                first_run_setup(CONFIG)
            except (KeyboardInterrupt, Exception):
                ts("\nSetup cancelled.")
                sys.exit(1)

        # Auto-run exam selector if no exam configured
        if not CONFIG.get("exam"):
            select_exam(force=False)
            CONFIG = load_config()
            GOOGLE_SHEET_URL = CONFIG["google_sheet_url"]
            WORKSHEET_NAME = CONFIG["worksheet_name"]
            FORM_DATA["exam"] = CONFIG["exam"]
            START_REGI = CONFIG["start_regi"]
            END_REGI = CONFIG["end_regi"]

        if args.log:
            log_dir = os.path.join(DATA_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_filename = os.path.join(
                log_dir, f"scraper_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
            )
            LOG_FILE = open(log_filename, "w")
            ts(f"Logging to {log_filename}")

        if args.fresh:
            save_progress([])
            ts("[INFO] Progress cleared — starting fresh.")

        try:
            main(dry_run=args.dry_run, reg_num=args.reg)
        finally:
            if LOG_FILE:
                LOG_FILE.close()
```

- [ ] **Step 4: Verify `scraper_common.py` has no IN_COLAB references**

Run: `rg IN_COLAB scraper_common.py`
Expected: no matches

- [ ] **Step 5: Commit**

```bash
git add scraper_common.py
git commit -m "refactor: extract shared logic into scraper_common.py"
```

---

## Task 2: Rewrite `result_scrapper.py` as local entry point

**Files:**
- Modify: `result_scrapper.py` (replace contents entirely)

**Steps:**

- [ ] **Step 1: Replace entire contents of `result_scrapper.py`**

```python
#!/usr/bin/env python3
"""DUCMC Result Scraper — Local (Firefox) entry point."""

import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

import scraper_common

scraper_common.configure(
    data_dir=DATA_DIR,
    credentials_file=CREDENTIALS_FILE,
    env_file=ENV_FILE,
    browser="firefox",
)

if __name__ == "__main__":
    scraper_common.run()
```

- [ ] **Step 2: Verify it runs (syntax check)**

Run: `.venv/bin/python -c "import result_scrapper"`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add result_scrapper.py
git commit -m "refactor: rewrite result_scrapper.py as local entry point"
```

---

## Task 3: Create `colab_scrapper.py` as Colab entry point

**Files:**
- Create: `colab_scrapper.py`

**Steps:**

- [ ] **Step 1: Create `colab_scrapper.py`**

```python
#!/usr/bin/env python3
"""DUCMC Result Scraper — Google Colab entry point.

Upload this file and scraper_common.py to Colab, then run:
  %run colab_scrapper.py
"""

import sys
import os
import subprocess

# --- Install dependencies ---
subprocess.run(["apt-get", "update"], check=True)
subprocess.run(["apt-get", "install", "-y", "chromium-browser"], check=True)
subprocess.run(
    ["pip", "install",
     "selenium==4.33.0", "gspread==6.2.1",
     "beautifulsoup4==4.13.4", "webdriver-manager==4.0.2"],
    check=True,
)

# --- Mount Google Drive ---
from google.colab import drive  # type: ignore

if not os.path.exists("/content/drive/MyDrive"):
    drive.mount("/content/drive")
else:
    print("[INFO] Google Drive already mounted")

DATA_DIR = "/content/drive/MyDrive/ResultScraperData"
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
ENV_FILE = os.path.join(DATA_DIR, ".env")

if not os.path.exists(CREDENTIALS_FILE):
    print(f"[ERROR] credentials.json not found at {CREDENTIALS_FILE}")
    print("Please place it in Google Drive → ResultScraperData/")
    sys.exit(1)

import scraper_common

scraper_common.configure(
    data_dir=DATA_DIR,
    credentials_file=CREDENTIALS_FILE,
    env_file=ENV_FILE,
    browser="chrome",
)

if __name__ == "__main__":
    scraper_common.run()
```

- [ ] **Step 2: Verify it imports cleanly (syntax check only — no Colab runtime)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('colab_scrapper.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add colab_scrapper.py
git commit -m "feat: add colab_scrapper.py entry point"
```

---

## Task 4: Update tests to import from `scraper_common`

**Files:**
- Modify: `tests/test_result_scrapper.py` (rename imports, add configure() calls)

**Steps:**

- [ ] **Step 1: Update imports and add configure() fixture**

Change the import block:

```python
# OLD
from result_scrapper import (
    sanitize_text, normalize_text, ...
)

# NEW
import scraper_common

# Configure with test defaults before importing functions
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scraper_common.configure(
    data_dir=os.path.join(SCRIPT_DIR, "data"),
    credentials_file=os.path.join(SCRIPT_DIR, "credentials.json"),
    env_file=os.path.join(SCRIPT_DIR, ".env"),
    browser="firefox",
)

from scraper_common import (
    sanitize_text, normalize_text, format_roll_number, parse_result_html,
    load_config, save_config, load_progress, save_progress,
    get_sheet_data, setup_course_columns, set_column_widths,
    update_sheet_with_student_data, format_gpa_cgpa_columns,
    scrape_student_result, select_exam, ts,
)
```

Also update all `result_scrapper.X` mock targets in `@patch` decorators to `scraper_common.X`.

- [ ] **Step 2: Run tests to verify they still pass**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all 59 tests pass

- [ ] **Step 3: Rename test file to `tests/test_scraper_common.py`**

```bash
git mv tests/test_result_scrapper.py tests/test_scraper_common.py
```

- [ ] **Step 4: Run tests again after rename**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all 59 tests pass

- [ ] **Step 5: Commit**

```bash
git add tests/test_scraper_common.py
git commit -m "test: update tests to import from scraper_common"
```

---

## Task 5: Update documentation and .gitignore

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `.gitignore`

**Steps:**

- [ ] **Step 1: Update AGENTS.md**

Key changes:
- Document the 3-file architecture
- Remove all `IN_COLAB` references
- Update run instructions for both environments
- Update dependencies (no InquirerPy)
- Note that Colab requires uploading both `.py` files

- [ ] **Step 2: Update README.md**

Key changes:
- Add Colab section with upload instructions
- Update CLI reference
- Note the shared module architecture

- [ ] **Step 3: Update .gitignore**

Add `colab_scrapper.py` note — it IS tracked (unlike credentials). No changes needed unless we want to add `__pycache__` more explicitly.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: update for 3-file architecture"
```

---

## Task 6: Final verification

**Steps:**

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 2: Verify no IN_COLAB in scraper_common.py**

Run: `rg IN_COLAB scraper_common.py`
Expected: no matches

- [ ] **Step 3: Verify entry points are thin (<80 lines each)**

Run: `wc -l result_scrapper.py colab_scrapper.py`
Expected: ~50 and ~60 lines respectively

- [ ] **Step 4: Verify local entry point runs (help flag)**

Run: `.venv/bin/python result_scrapper.py --help`
Expected: argparse help output

- [ ] **Step 5: Verify no duplicate code**

Run: `rg "def sanitize_text" scraper_common.py result_scrapper.py colab_scrapper.py`
Expected: only `scraper_common.py` has the definition

---

## Migration Checklist

After implementation:
- [ ] Local: `python result_scrapper.py --help` works
- [ ] Local: `python result_scrapper.py --show-config` works
- [ ] Local: `python result_scrapper.py --validate` works (with Firefox)
- [ ] Local: `python result_scrapper.py` full scrape works
- [ ] Colab: upload both `.py` files, `%run colab_scrapper.py` works
- [ ] Tests: `.venv/bin/python -m pytest tests/ -v` — all pass
- [ ] No `IN_COLAB` anywhere in `scraper_common.py`
- [ ] No code duplication between the 3 files
