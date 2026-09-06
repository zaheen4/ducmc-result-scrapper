# AGENTS.md

## What This Is

Three-file Python scraper for DUCMC university exam results:
- `scraper_common.py` — shared logic (all functions, config, scraping, sheet ops)
- `result_scrapper.py` — local entry point (Firefox, InquirerPy fuzzy prompts)
- `colab_scrapper.py` — Colab entry point (Chrome, Drive mount, plain `input()`)

Pulls results from the DUCMC portal (Selenium + BeautifulSoup) and writes them into a Google Sheet (gspread).

## Architecture

`scraper_common.py` holds all shared logic. Each entry point sets environment-specific constants and calls `scraper_common.configure()` + `scraper_common.run()`. No code duplication.

```
result_scrapper.py  ─┐
                     ├─► scraper_common.py  ──► Google Sheet + DUCMC Portal
colab_scrapper.py   ─┘
```

## Run

```bash
# Local — requires Firefox + geckodriver. No args opens the interactive menu.
python result_scrapper.py

# Menu tasks: Scrape normal/retake | Update exam data | Settings | Exit
# Breadth: Single semester | Everything pending | multi-terminal commands
# | Settings (edits saved config: sheet, session, range, delay)
# | Update exam data (portal → exam_catalog.json, suggest targets.json adds)
# Menu loops until Exit; ← Back navigates up; arrows/hjkl + l/Enter, h/Esc for motion
# (Esc lags ~0.5s from terminal ambiguity — h is instant); exam picker is fuzzy

# Short codes skip the menu: L1T2 normal, L1T2R retake, L1T2R-2024 one year
python result_scrapper.py --exam L3T2
python result_scrapper.py --retake --retake-exam L1T2R-2024

# Batch: every targets.json exam for the mode, sequentially
python result_scrapper.py --all
python result_scrapper.py --retake --all

# Colab — data files + credentials must live in Google Drive/ResultScraperData/
# (see Result_Scraper_Colab.ipynb), then:
# %run colab_scrapper.py
```

Run tests: `.venv/bin/python -m pytest tests/ -v`

## Dependencies

Pinned in `requirements.txt` (source of truth):

```
selenium==4.33.0 gspread==6.2.1 beautifulsoup4==4.13.4 InquirerPy==0.3.4
```

Plus `pytest==9.1.1` for tests. (`.env` is hand-parsed — no dotenv dependency.)

(Installed at runtime in Colab; must be pre-installed locally, e.g. in `.venv`.)

## Config

All runtime data lives in `data/` directory (gitignored). Config is stored in `data/config.json`, created automatically on first run.

```json
{
  "google_sheet_url": "https://docs.google.com/spreadsheets/d/.../edit",
  "worksheet_name": "PerCourse_L3T1",
  "program": "B.Sc. in Computer Science and Engineering",
  "session": "2021-2022",
  "exam": "B.Sc. in Computer Science and Engineering 3rd year 1st Semester Examination of 2023 (New Curriculum)",
  "retake_exam": "B.Sc. in Computer Science and Engineering 3rd year 1st Semester Improvement Examination of 2023 (Retake/Improvement)",
  "start_regi": 710,
  "end_regi": 813,
  "request_delay": 4
}
```

- First run triggers interactive setup (Sheet URL, worksheet, program, session, regi range)
- Local: InquirerPy fuzzy search for exam selector
- Colab: plain `input()` with numbered list
- Persistent values also saved to `.env` (gitignored) so they survive config.json deletion
- CLI flags (`--sheet-url`, `--worksheet`, `--program`, `--session`, `--start-regi`, `--end-regi`) override config
- `--exam`/`--retake-exam` take short codes (`L1T2`, `L1T2R`, `L1T2R-2024`) or raw titles; `--help` groups basic vs advanced flags
- `exam` can be empty string to skip exam selection (will fail at runtime)
- `worksheet_name` is auto-derived from exam title when `--exam` is used (e.g. "1st year 2nd Semester" → `PerCourse_L1T2`), unless `--worksheet` is explicitly passed

## Resume

Scraped reg numbers saved to per-exam progress files, scoped by `(sheet_url, worksheet, exam)` hash. On restart, those students are skipped.
- Normal mode: `data/progress_{hash}.json`
- Retake mode: `data/progress_retake_{hash}.json`
- Delete the relevant progress file to re-scrape that exam from scratch.
- `--dry-run` never creates, modifies, or clears progress files.

## Exam Codes & Batch (`data/targets.json`)

- `data/targets.json` (tracked) maps slot codes → titles for this batch: 6 normals (`L1T1`–`L3T2`), retake slots hold yearly lists
- `resolve_exam_code()` in `scraper_common.py`: `L1T2` → single title; `L1T2R` → unique or `ExamCodeError` with `L1T2R-YYYY` options; `L1T2R-2024` → one year; missing slots fall back to `exam_catalog.json` search
- `run_batch()` runs every targets exam for the mode sequentially (per-exam progress, resumable); `--all` flag; menu "Everything pending" calls the same path
- Menu's "Show multi-terminal commands" prints short-code one-liners

## Retake/Improvement Mode

Use `--retake` flag to scrape retake/improvement exam results. These publish separately on the portal with only course grades (no GPA/CGPA).

```bash
# Select a retake exam
python result_scrapper.py --retake --list-exams --force

# Run retake scraping
python result_scrapper.py --retake

# Single student retake
python result_scrapper.py --retake --reg 710

# Dry run
python result_scrapper.py --retake --dry-run
```

- Retake exams are filtered by "Improvement"/"Retake" keywords in exam name
- The exam title is parsed to determine which PerCourse sheet to update (e.g., "1st year 2nd Semester" → `PerCourse_L1T2`)
- Only updates cells where the new grade is **better** than the existing grade, or the cell is empty
- Does NOT touch GPA/CGPA columns (they're formulas from Overview sheet)
- Course codes with spaces (e.g., "MATH 1204") are normalized to hyphens ("MATH-1204") for matching
- GPA is recalculated after retake updates using credit hours from `data/cse_credit_hours.json`
- Sanity check: retake mode uses `max(existing_gpa, formula_gpa)` — never lowers GPA

## Multi-Terminal Concurrent Scraping

Use short codes (`--exam L1T2`, `--retake --retake-exam L1T2R-2024`) to run multiple terminals concurrently. These set the exam **in memory only** (no disk write to config.json) and each terminal gets its own per-exam progress file. The menu's "Show multi-terminal commands" prints the one-liners for you.

```bash
# Terminal 1 — normal semester
python result_scrapper.py --exam L1T2 --start-regi 710 --end-regi 813

# Terminal 2 — different semester
python result_scrapper.py --exam L2T1 --start-regi 710 --end-regi 813

# Terminal 3 — retake scraping
python result_scrapper.py --retake --retake-exam L1T2R-2024 --start-regi 710 --end-regi 813
```

- `worksheet_name` is auto-derived from exam title (e.g. "1st year 2nd Semester" → `PerCourse_L1T2`)
- Progress files are per-exam: `data/progress_{hash}.json` (normal), `data/progress_retake_{hash}.json` (retake)
- Keep `request_delay: 4` to stay under Google's 60 writes/min quota across concurrent terminals

## Gotchas

- `credentials.json` is in `.gitignore` and is **not committed** — it exists only on disk. It contains a GCP service account private key. Do not leak or commit copies.
- `config.json`, `progress_*.json`, and `.env` are in `.gitignore` via `data/` — they won't be committed.
- The Google Sheet only needs these fixed headers in row 1: `Sl.`, `Student's Name`, `Student's ID`, `Reg. No.`, `GPA`, `CGPA`. Course columns and `Retake Courses` are auto-created from the first scraped result.
- Exam selection uses text normalization (HTML entity decoding, whitespace collapsing) to handle portal encoding quirks.
- The script retries failed requests up to 3 times with exponential backoff (2s, 4s, 8s).
- The script only writes to **empty cells**; it never overwrites existing data.
- After scraping, `format_gpa_cgpa_columns` re-formats columns E/F as numbers from row 3 onward.
- Progress file is cleared after a successful run (0 failures); kept on failure for retry.
- All output has `[HH:MM:SS]` timestamps. A summary is printed at the end (scraped/skipped/failed counts + total time).
- Run `--list-exams` to interactively select an exam: `.venv/bin/python result_scrapper.py --list-exams`
