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
| `--exam` | Set exam in memory (auto-selects PerCourse sheet, doesn't touch config.json) |
| `--retake` | Enable retake/improvement scraping mode |
| `--retake-exam` | Set retake exam in memory (for multi-terminal retake scraping) |
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
| `worksheet_name` | `""` | Worksheet/tab name (prompted on first run; auto-derived from exam title when using `--exam`) |
| `program` | `"B.Sc. in Computer Science and Engineering"` | Program in portal dropdown |
| `session` | `"2021-2022"` | Academic session in portal dropdown |
| `exam` | `""` | Exam name (auto-selected if empty) |
| `retake_exam` | `""` | Retake/improvement exam name (set via `--retake-exam` or `--list-exams --retake`) |
| `start_regi` | `710` | Starting reg number |
| `end_regi` | `813` | Ending reg number |
| `request_delay` | `4` | Seconds between requests (keep at 4 for concurrent scraping) |

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

## Retake/Improvement Mode

Use `--retake` to scrape retake/improvement exam results (only course grades, no GPA/CGPA on portal).

```bash
# Interactively select a retake exam
.venv/bin/python result_scrapper.py --retake --list-exams --force

# Run retake scraping
.venv/bin/python result_scrapper.py --retake

# Dry run
.venv/bin/python result_scrapper.py --retake --dry-run
```

- Auto-maps exam title to PerCourse sheet (e.g. "1st year 2nd Semester" → `PerCourse_L1T2`)
- Only writes if new grade is **better** than existing, or cell is empty
- GPA recalculated after updates using credit hours from `data/cse_credit_hours.json`
- Sanity check: uses `max(existing_gpa, formula_gpa)` — never lowers GPA
- Course codes with spaces (e.g. "MATH 1204") normalized to hyphens ("MATH-1204")

## Multi-Terminal Concurrent Scraping

Run multiple terminals for different semesters using `--exam` or `--retake-exam`. These set the exam **in memory only** (config untouched) and each terminal gets its own per-exam progress file.

```bash
# Terminal 1
.venv/bin/python result_scrapper.py --exam "B.Sc. in Computer Science and Engineering 1st year 2nd Semester Examination of 2022" --start-regi 710 --end-regi 813

# Terminal 2
.venv/bin/python result_scrapper.py --exam "B.Sc. in Computer Science and Engineering 2nd year 1st Semester Examination of 2023" --start-regi 710 --end-regi 813

# Terminal 3 — retake
.venv/bin/python result_scrapper.py --retake --retake-exam "B.Sc. in Computer Science and Engineering 1st year 2nd Semester Improvement Examination of 2023 (Retake/Improvement)" --start-regi 710 --end-regi 813
```

- Worksheet auto-derived from exam title — no need to set `--worksheet`
- Progress files scoped per-exam: `data/progress_{hash}.json` (normal), `data/progress_retake_{hash}.json` (retake)
- Keep `request_delay: 4` to stay under Google's 60 writes/min quota

## Data Files

| File | Description |
|---|---|
| `data/config.json` | Runtime configuration (gitignored) |
| `data/progress_{hash}.json` | Per-exam scrape progress — auto-resumed on restart (gitignored) |
| `data/cse_credit_hours.json` | Credit hours for all CSE courses — used for GPA recalculation |
| `data/exam_catalog.json` | All CSE exams, sessions, and metadata scraped from portal — used for `--exam`/`--retake-exam` |
| `credentials.json` | GCP service account key — **never committed** (gitignored) |
| `.env` | Persistent config overrides (gitignored) |
