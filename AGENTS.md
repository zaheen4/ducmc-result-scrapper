# AGENTS.md

## What This Is

Single-file Python scraper (`result_scrapper.py`) that pulls student exam results from a DUCMC university results portal (Selenium + BeautifulSoup) and writes them into a Google Sheet (gspread).

## Dual Environment

The script auto-detects Colab vs local via `google.colab` in `sys.modules`:

- **Colab**: installs Chromium, uses Chrome WebDriver, mounts Google Drive for credentials at `/content/drive/MyDrive/ResultScraperData/credentials.json`
- **Local**: expects Firefox + geckodriver, reads `credentials.json` from script directory

The `IN_COLAB` flag gates all environment differences.

## Run

```bash
# Local — requires Firefox + geckodriver
python result_scrapper.py

# Colab — paste into a cell and run; dependencies auto-install
```

Run tests: `.venv/bin/python -m pytest tests/ -v`

## Dependencies

Pinned in `requirements.txt` (source of truth):

```
selenium==4.33.0 gspread==6.2.1 beautifulsoup4==4.13.4 webdriver-manager==4.0.2
```

Plus `pytest` for tests. No InquirerPy — all prompts use plain `input()`.

(Installed at runtime in Colab; must be pre-installed locally, e.g. in `.venv`.)

## Config

All runtime data lives in `data/` directory (gitignored). Config is stored in `data/config.json`, created automatically on first run.

```json
{
  "google_sheet_url": "https://docs.google.com/spreadsheets/d/.../edit",
  "worksheet_name": "Copy of PerCourse_L3T1",
  "program": "B.Sc. in Computer Science and Engineering",
  "session": "2020-2021",
  "exam": "B.Sc. in Computer Science and Engineering 3rd year 1st Semester Examination of 2023 (New Curriculum)",
  "start_regi": 710,
  "end_regi": 813
}
```

- First run triggers interactive setup (Sheet URL, worksheet, program, session, regi range)
- Persistent values also saved to `.env` (gitignored) so they survive config.json deletion
- CLI flags (`--sheet-url`, `--worksheet`, `--program`, `--session`, `--start-regi`, `--end-regi`) override config
- `exam` can be empty string to skip exam selection (will fail at runtime)

## Resume (`data/progress.json`)

Scraped reg numbers saved to `data/progress.json`, scoped by `(sheet_url, worksheet, exam)` hash. On restart, those students are skipped. Delete `data/progress.json` to re-scrape from scratch.

## Gotchas

- `credentials.json` is in `.gitignore` but currently **is committed** in the repo root. It contains a GCP service account private key. Do not leak or commit fresh copies.
- `config.json`, `progress.json`, and `.env` are in `.gitignore` via `data/` — they won't be committed.
- The Google Sheet only needs these fixed headers in row 1: `Sl.`, `Student's Name`, `Student's ID`, `Reg. No.`, `GPA`, `CGPA`. Course columns and `Retake Courses` are auto-created from the first scraped result.
- Exam selection uses text normalization (HTML entity decoding, whitespace collapsing) to handle portal encoding quirks.
- The script retries failed requests up to 3 times with exponential backoff (2s, 4s, 8s).
- The script only writes to **empty cells**; it never overwrites existing data.
- After scraping, `format_gpa_cgpa_columns` re-formats columns E/F as numbers from row 3 onward.
- Progress file is cleared after a successful run (0 failures); kept on failure for retry.
- All output has `[HH:MM:SS]` timestamps. A summary is printed at the end (scraped/skipped/failed counts + total time).
- Run `--list-exams` to interactively select an exam: `.venv/bin/python result_scrapper.py --list-exams`
