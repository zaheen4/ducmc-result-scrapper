# DUCMC Result Scraper

Selenium scraper that pulls student exam results from the DUCMC results portal into a Google Sheet.

## Architecture

Three files, zero duplication:

| File | Role | Environment |
|------|------|-------------|
| `scraper_common.py` | All shared logic | — |
| `result_scrapper.py` | Entry point (Firefox, InquirerPy) | Local |
| `colab_scrapper.py` | Entry point (Chrome, Drive mount) | Colab |

## Requirements

- Python 3.11+
- Firefox + geckodriver (local) or Google Colab (auto-installs Chrome)
- `credentials.json` — GCP service account key with Sheets API access

## Setup (Local)

1. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. Place `credentials.json` in the project root.

3. Run — on first launch, you'll be prompted for Sheet URL, worksheet name, and reg range:

   ```bash
   .venv/bin/python result_scrapper.py
   ```

## Setup (Colab)

1. Upload `scraper_common.py` and `colab_scrapper.py` to a Colab notebook.

2. Run:

   ```python
   %run colab_scrapper.py
   ```

   Dependencies install automatically. Google Drive mounts for credentials/config storage.

## Usage

```bash
# Basic run (uses config.json settings)
.venv/bin/python result_scrapper.py

# Interactively select an exam (InquirerPy fuzzy search)
.venv/bin/python result_scrapper.py --list-exams

# Override config via CLI (no need to edit JSON)
.venv/bin/python result_scrapper.py --session "2022-2023" --start-regi 900 --end-regi 999

# Scrape a single registration number
.venv/bin/python result_scrapper.py --reg 810

# Dry run — scrape without writing to the sheet
.venv/bin/python result_scrapper.py --dry-run

# Validate config before a long run
.venv/bin/python result_scrapper.py --validate

# Check current config or progress
.venv/bin/python result_scrapper.py --show-config
.venv/bin/python result_scrapper.py --status

# Save output to a log file
.venv/bin/python result_scrapper.py --log

# Ignore progress and start fresh
.venv/bin/python result_scrapper.py --fresh
```

### CLI Flags

| Flag | Description |
|---|---|
| `--list-exams` | Interactively select an exam and save to config |
| `--force` | Override existing exam selection (with `--list-exams`) |
| `--fresh` | Ignore progress.json, start from scratch |
| `--dry-run` | Scrape without writing to the sheet |
| `--validate` | Test browser + sheet connection, then exit |
| `--show-config` | Print active configuration and exit |
| `--status` | Print progress status and exit |
| `--log` | Save output to `data/logs/` |
| `--reg N` | Scrape a single reg number |
| `--sheet-url` | Override Google Sheet URL |
| `--worksheet` | Override worksheet name |
| `--program` | Override program name |
| `--session` | Override session year |
| `--start-regi` | Override starting reg number |
| `--end-regi` | Override ending reg number |

## Config

All settings live in `data/config.json` (local) or `Google Drive/ResultScraperData/config.json` (Colab). Created automatically on first run.

| Field | Default | Description |
|---|---|---|
| `google_sheet_url` | `""` | Google Spreadsheet URL (prompted on first run) |
| `worksheet_name` | `""` | Worksheet/tab name (prompted on first run) |
| `program` | `"B.Sc. in Computer Science and Engineering"` | Program in portal dropdown |
| `session` | `"2021-2022"` | Academic session in portal dropdown |
| `exam` | `""` | Exam name (auto-selected if empty) |
| `start_regi` | `710` | Starting reg number |
| `end_regi` | `813` | Ending reg number |
| `request_delay` | `1` | Seconds between requests |

Progress is scoped by `(sheet_url, worksheet, exam)` — switching configs automatically starts fresh.

## Google Sheet

The sheet needs these fixed headers in row 1:

```
Sl. | Student's Name | Student's ID | Reg. No. | GPA | CGPA
```

Course columns and `Retake Courses` are auto-created from the first scraped result. Column widths are set automatically.

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```
