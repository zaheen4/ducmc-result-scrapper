# ===================================================================
# Scraper Common Module
# ===================================================================
# Shared logic for DUCMC result scraper. Entry points call configure()
# then use these functions. No environment detection — each entry point
# provides the right settings.

import sys
import os
import time
import json
import html as html_module
import re
import hashlib
import urllib.request
import contextlib
from datetime import datetime
from typing import Any, TextIO

import gspread
from gspread.exceptions import APIError
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


# ===================================================================
# Module-Level State
# ===================================================================
DATA_DIR: str | None = None
CREDENTIALS_FILE: str | None = None
ENV_FILE: str | None = None
BROWSER: str = "firefox"
USE_INQUIRERPY: bool = True
CONFIG: dict[str, Any] = {}
GOOGLE_SHEET_URL: str = ""
WORKSHEET_NAME: str = ""
FORM_DATA: dict[str, str] = {"program": "", "session": "", "exam": ""}
URL = "https://ducmc.du.ac.bd/result.php"
START_REGI: int = 710
END_REGI: int = 813
REQUEST_DELAY: int = 1
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2
LOG_FILE: TextIO | None = None
RETAKE_MODE: bool = False

GRADE_POINTS: dict[str, float] = {
    "A+": 4.00, "A": 3.75, "A-": 3.50,
    "B+": 3.25, "B": 3.00, "B-": 2.75,
    "C+": 2.50, "C": 2.25, "D": 2.00,
    "F": 0.00, "Fail": 0.00,
}
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
    "retake_exam": "",
    "start_regi": 710,
    "end_regi": 813,
    "request_delay": 1,
    "theme": "flexoki-light",
}


# ===================================================================
# Configure
# ===================================================================
def configure(data_dir, credentials_file, env_file, browser="firefox", use_inquirerpy=True):
    """Set module-level state for the given environment.

    Call this once at startup before any other functions.
    """
    global DATA_DIR, CREDENTIALS_FILE, ENV_FILE, BROWSER, USE_INQUIRERPY
    global CONFIG

    DATA_DIR = data_dir  # pyright: ignore[reportConstantRedefinition]
    CREDENTIALS_FILE = credentials_file  # pyright: ignore[reportConstantRedefinition]
    ENV_FILE = env_file  # pyright: ignore[reportConstantRedefinition]
    BROWSER = browser  # pyright: ignore[reportConstantRedefinition]
    USE_INQUIRERPY = use_inquirerpy  # pyright: ignore[reportConstantRedefinition]

    assert DATA_DIR is not None
    os.makedirs(DATA_DIR, exist_ok=True)
    CONFIG = load_config()  # pyright: ignore[reportConstantRedefinition]


# ===================================================================
# Logging
# ===================================================================
def ts(msg):
    """Print a message with [HH:MM:SS] timestamp prefix, optionally to log file."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    if LOG_FILE:
        LOG_FILE.write(line + "\n")
        LOG_FILE.flush()


# ===================================================================
# Helper Functions
# ===================================================================
def sanitize_text(text):
    """Cleans and standardizes text for reliable matching."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def normalize_text(text):
    """Normalize text: decode HTML entities, collapse whitespace, strip."""
    text = html_module.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def format_roll_number(raw_roll):
    """Extracts letters and numbers to format a class roll."""
    if not raw_roll:
        return ''
    letters = ''.join(re.findall(r'[a-zA-Z]+', raw_roll))
    numbers = ''.join(re.findall(r'\d+', raw_roll))
    if letters and numbers:
        return f"{letters.upper()} {numbers}"
    return raw_roll


def parse_result_html(html_content):
    """Parses the HTML content of a student's result page."""
    soup = BeautifulSoup(html_content, 'html.parser')
    student_data = {}

    info_rows = soup.select('div#exam_result > div.row > div.col-12 > table.table-bordered > tbody > tr')
    for row in info_rows:
        headers = row.find_all('th')
        if headers and len(headers) == 1:
            header_text = headers[0].get_text(strip=True)
            if "Student's Name" in header_text:
                student_data['Name'] = row.find('td').get_text(strip=True)
            elif "Class Roll" in header_text:
                student_data['Roll'] = row.find('td').get_text(strip=True)
            elif "Registration" in header_text:
                student_data['Reg'] = row.find('td').get_text(strip=True)

    result_div = soup.find('div', style=lambda v: v and 'text-align: center' in v)

    student_data['GPA'], student_data['CGPA'], student_data['Fail Subs'] = '', '', ''

    if result_div:
        result_text = result_div.get_text(separator=' ', strip=True)
        gpa_match = re.search(r"GPA:\s*([\d.]+)", result_text)
        if gpa_match:
            student_data['GPA'] = gpa_match.group(1)
        cgpa_match = re.search(r"CGPA:\s*([\d.]+)", result_text)
        if cgpa_match:
            student_data['CGPA'] = cgpa_match.group(1)
        all_codes = re.findall(r'[A-Z]{2,}-\d{4}', result_div.get_text(separator=','))
        student_data['Fail Subs'] = ', '.join(all_codes) if all_codes else ''
    student_data['courses'] = []
    course_table = soup.select_one('th table[width="100%"]')
    if course_table:
        for row in course_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) == 5:
                student_data['courses'].append({
                    "name": cols[2].get_text(strip=True),
                    "code": cols[1].get_text(strip=True),
                    "grade": cols[4].get_text(strip=True) or '0.00'
                })
    return student_data


def grade_to_point(grade_str):
    """Convert a letter grade (e.g., 'A+', 'B') to a numeric grade point."""
    if not grade_str:
        return 0.0
    grade_str = grade_str.strip()
    if grade_str in GRADE_POINTS:
        return GRADE_POINTS[grade_str]
    try:
        return float(grade_str)
    except (ValueError, TypeError):
        return 0.0


def parse_retake_result_html(html_content):
    """Parses a retake/improvement result page.

    Same structure as normal results but NO GPA/CGPA — only course grades.
    Also extracts the exam title to determine which semester the retake is for.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    student_data = {}

    info_rows = soup.select('div#exam_result > div.row > div.col-12 > table.table-bordered > tbody > tr')
    for row in info_rows:
        headers = row.find_all('th')
        if headers and len(headers) == 1:
            header_text = headers[0].get_text(strip=True)
            if "Student's Name" in header_text:
                student_data['Name'] = row.find('td').get_text(strip=True)
            elif "Class Roll" in header_text:
                student_data['Roll'] = row.find('td').get_text(strip=True)
            elif "Registration" in header_text:
                student_data['Reg'] = row.find('td').get_text(strip=True)

    h2_tag = soup.select_one('div#exam_result h2')
    student_data['exam_title'] = normalize_text(h2_tag.get_text(strip=True)) if h2_tag else ''

    student_data['GPA'] = ''
    student_data['CGPA'] = ''
    student_data['Fail Subs'] = ''

    student_data['courses'] = []
    course_table = soup.select_one('th table[width="100%"]')
    if course_table:
        for row in course_table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) == 5:
                code = cols[1].get_text(strip=True).replace(' ', '-')
                student_data['courses'].append({
                    "name": cols[2].get_text(strip=True),
                    "code": code,
                    "grade": cols[4].get_text(strip=True) or '0.00'
                })
    return student_data


def map_exam_to_sheet(exam_title):
    """Maps a retake exam title to the corresponding PerCourse worksheet name.

    e.g. 'B.Sc. in CSE 1st year 2nd Semester Improvement...' -> 'PerCourse_L1T2'
    Handles both "Nth year Nth Semester" and "Level-N Term-N" formats.
    Returns None if can't determine from title.
    """
    exam_title = normalize_text(exam_title).lower()

    level_match = re.search(r'(\d+)(?:st|nd|rd|th)\s+year', exam_title)
    if not level_match:
        level_match = re.search(r'level[\s-]*(\d+)', exam_title)

    term_match = re.search(r'(\d+)(?:st|nd|rd|th)\s+semester', exam_title)
    if not term_match:
        term_match = re.search(r'term[\s-]*(\d+)', exam_title)

    if level_match and term_match:
        level = level_match.group(1)
        term = term_match.group(1)
        return f"PerCourse_L{level}T{term}"

    return None


def find_course_column(course_code, course_name_map):
    """Finds the column index for a course code in the sheet.

    Looks up by sanitized course code in the course_name_map.
    Returns the column index or None if not found.
    """
    sanitized = sanitize_text(course_code)
    return course_name_map.get(sanitized)


def resolve_worksheet_from_exam(exam_title, spreadsheet):
    """Auto-selects the correct PerCourse worksheet from an exam title.

    Uses map_exam_to_sheet() to derive the base name (e.g. 'PerCourse_L1T2'),
    then tries 'Copy of {name}' first, then '{name}'.
    Returns (worksheet, resolved_name) or (None, None) if not found.
    """
    base_name = map_exam_to_sheet(exam_title)
    if not base_name:
        return None, None

    for candidate in [f"Copy of {base_name}", base_name]:
        try:
            ws = spreadsheet.worksheet(candidate)
            ts(f"✅ Auto-selected worksheet '{ws.title}' from exam title.")
            return ws, ws.title
        except Exception:
            continue

    ts(f"⚠️ Could not find worksheet '{base_name}' or 'Copy of {base_name}' in spreadsheet.")
    return None, None


EXAM_CODE_RE = re.compile(r'^L([1-4])T([1-2])(R?)(?:-(\d{4}))?$', re.IGNORECASE)


class ExamCodeError(ValueError):
    """Raised when a short exam code (e.g. 'L1T2R') can't resolve to one title."""


def load_targets():
    """Loads the batch targets file (slot codes -> exam titles)."""
    assert DATA_DIR is not None
    targets_path = os.path.join(DATA_DIR, 'targets.json')
    if os.path.exists(targets_path):
        with open(targets_path, 'r') as f:
            return json.load(f)
    return {"session": "", "normal": {}, "retake": {}}


def _catalog_exams():
    """Loads the raw exam list from exam_catalog.json ([] if missing)."""
    assert DATA_DIR is not None
    catalog_path = os.path.join(DATA_DIR, 'exam_catalog.json')
    if os.path.exists(catalog_path):
        with open(catalog_path, 'r') as f:
            return json.load(f).get('exams', [])
    return []


def _exam_year_of(title):
    """Extracts the exam year from a portal exam title ('' if none)."""
    match = re.search(r'Examination\s+(?:of\s+)?(\d{4})', normalize_text(title))
    return match.group(1) if match else ''


def _slot_titles(targets, slot, want_retake):
    """Returns the targets.json title list for a slot (may be empty)."""
    section = (targets or {}).get('retake' if want_retake else 'normal', {})
    entry = section.get(slot)
    if isinstance(entry, str):
        return [entry]
    if isinstance(entry, list):
        return list(entry)
    return []


def _catalog_titles(catalog, year, term, want_retake):
    """Finds catalog exam titles for a level/term/type combination."""
    return [
        e['name'] for e in (catalog or [])
        if str(e.get('year')) == year and str(e.get('term')) == term
        and (e.get('type') == 'retake') == want_retake
    ]


def resolve_exam_code(code, retake=False, targets=None, catalog=None):
    """Resolves a short exam code to a full portal exam title.

    Codes: 'L1T2' (normal slot), 'L1T2R' (retake slot), 'L1T2R-2024'
    (one retake year). Slot codes come from targets.json; anything missing
    there falls back to an exam_catalog.json search. Raises ExamCodeError
    with concrete candidates when ambiguous or unknown.
    """
    match = EXAM_CODE_RE.match((code or '').strip())
    if not match:
        raise ExamCodeError(f"'{code}' is not an exam code (e.g. L1T2, L2T1R, L1T2R-2024).")
    year, term, r_flag, exam_year = match.groups()
    slot = f"L{year}T{term}"
    want_retake = retake or bool(r_flag)

    if targets is None:
        targets = load_targets()
    candidates = _slot_titles(targets, slot, want_retake)
    if not candidates:
        if catalog is None:
            catalog = _catalog_exams()
        candidates = _catalog_titles(catalog, year, term, want_retake)
    if exam_year:
        candidates = [t for t in candidates if _exam_year_of(t) == exam_year]

    if len(candidates) == 1:
        return candidates[0]
    kind = "retake" if want_retake else "exam"
    if not candidates:
        raise ExamCodeError(f"No {kind} found for '{code.strip()}'.")
    options = ", ".join(f"{slot}{'R' if want_retake else ''}-{_exam_year_of(t) or '?'}" for t in candidates)
    raise ExamCodeError(f"'{code.strip()}' matches {len(candidates)} exams; be specific: {options}.")


def maybe_resolve_exam_arg(value, retake=False):
    """Resolves a CLI exam argument: short code -> title, else raw title."""
    if value and EXAM_CODE_RE.match(value.strip()):
        return resolve_exam_code(value, retake=retake)
    return value


def _auto_resolve_worksheet(exam_title):
    """Best-effort PerCourse worksheet name from an exam title (None if unknown)."""
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(GOOGLE_SHEET_URL)
        _, resolved_name = resolve_worksheet_from_exam(exam_title, spreadsheet)
        return resolved_name
    except Exception:
        return None


def _setup_log_file():
    """Opens a timestamped log file (sets LOG_FILE)."""
    global LOG_FILE
    assert DATA_DIR is not None
    log_dir = os.path.join(DATA_DIR, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_filename = os.path.join(log_dir, f"scraper_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")
    LOG_FILE = open(log_filename, 'w')  # pyright: ignore[reportConstantRedefinition]
    ts(f"Logging to {log_filename}")


def _set_target_context(title, retake):
    """Points CONFIG/FORM_DATA at one exam title (in memory) + resolves worksheet."""
    global WORKSHEET_NAME
    if retake:
        CONFIG["retake_exam"] = title
        FORM_DATA["retake_exam"] = title  # pyright: ignore[reportConstantRedefinition]
    else:
        CONFIG["exam"] = title
        FORM_DATA["exam"] = title
        resolved = _auto_resolve_worksheet(title)
        if resolved:
            WORKSHEET_NAME = resolved  # pyright: ignore[reportConstantRedefinition]


def run_batch(retake=False, dry_run=False, reg_num=None, regs=None, fresh=False, log=False):
    """Runs every targets.json exam for the mode, sequentially."""
    global CONFIG
    targets = load_targets()
    section = targets.get('retake' if retake else 'normal', {})
    items = [(slot, t) for slot, v in section.items() for t in (v if isinstance(v, list) else [v])]
    if not items:
        ts(f"No {'retake' if retake else 'normal'} targets configured in targets.json.")
        return

    if not CONFIG.get("google_sheet_url"):
        try:
            first_run_setup(CONFIG)
        except (KeyboardInterrupt, Exception):
            ts("\nSetup cancelled.")
            sys.exit(1)

    if log:
        _setup_log_file()

    ts(f"--- Batch mode: {len(items)} exam(s), {'retake' if retake else 'normal'} ---")
    totals = {"ok": 0, "skipped": 0, "failed": 0}
    try:
        for slot, title in items:
            ts(f"\n===== [{slot}] {title} =====")
            _set_target_context(title, retake)
            if fresh and not dry_run:
                if retake:
                    _save_retake_progress([])
                else:
                    save_progress([])
                ts("[INFO] Progress cleared — starting fresh.")
            if retake:
                stats = scrape_retake_results(dry_run=dry_run, reg_num=reg_num, regs=regs)
                totals["ok"] += stats["updated"] + stats["unchanged"]
            else:
                stats = main(dry_run=dry_run, reg_num=reg_num, regs=regs)
                totals["ok"] += stats["scraped"]
            totals["skipped"] += stats["skipped"]
            totals["failed"] += stats["failed"]
    finally:
        if LOG_FILE:
            LOG_FILE.close()
    ts(f"\n--- Batch complete: ok={totals['ok']} skipped={totals['skipped']} failed={totals['failed']} ---")
    return totals


CSE_PROGRAM_ID = 14
PORTAL_EXAM_LIST_URL = (
    "https://ducmc.du.ac.bd/ajax/get_program_by_exam.php?program_id=14&pedata=99"
)
RETAKE_KEYWORDS = ('improvement', 'retake')


def fetch_portal_exams(url=PORTAL_EXAM_LIST_URL, timeout=20):
    """Fetches the live (id, name) exam list from the portal.

    Plain HTTPS GET of the portal's own exam-list endpoint — no browser needed.
    Returns None on network failure.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            html_content = response.read().decode('utf-8', errors='replace')
    except Exception as e:
        ts(f"[ERROR] Could not reach portal exam list: {e}")
        return None
    soup = BeautifulSoup(html_content, 'html.parser')
    exams = []
    for opt in soup.find_all('option'):
        try:
            exam_id = int((opt.get('value') or '').strip())
        except ValueError:
            continue
        name = normalize_text(opt.get_text())
        if exam_id and name:
            exams.append((exam_id, name))
    return exams


def _derive_catalog_entry(exam_id, name):
    """Builds a catalog entry for a new exam (metadata derived from the title)."""
    lowered = name.lower()
    is_retake = any(kw in lowered for kw in RETAKE_KEYWORDS)
    entry = {"id": exam_id, "name": name,
             "type": "retake" if is_retake else "normal",
             "year": 0, "term": 0}
    sheet = map_exam_to_sheet(name)
    if sheet:
        match = re.fullmatch(r'PerCourse_L(\d)T(\d)', sheet)
        if match:
            entry["year"], entry["term"] = int(match.group(1)), int(match.group(2))
    exam_year = _exam_year_of(name)
    if exam_year:
        entry["exam_year"] = int(exam_year)
    if 'special' in lowered:
        entry["special"] = True
    if 'old syllabus' in lowered or 'old curriculum' in lowered:
        entry["curriculum"] = "old"
    elif 'new curriculum' in lowered:
        entry["curriculum"] = "new"
    return entry


def refresh_exam_catalog():
    """Merges the live portal exam list into exam_catalog.json.

    New exams are prepended (newest first) with derived metadata; vanished
    exams are kept locally and reported. Returns (new_entries, removed_ids).
    """
    assert DATA_DIR is not None
    live = fetch_portal_exams()
    if live is None:
        return None
    catalog_path = os.path.join(DATA_DIR, 'exam_catalog.json')
    with open(catalog_path, 'r') as f:
        catalog = json.load(f)
    known_ids = {e['id'] for e in catalog.get('exams', [])}
    live_ids = {exam_id for exam_id, _ in live}
    new_entries = [_derive_catalog_entry(i, n) for i, n in live if i not in known_ids]
    removed_ids = sorted(known_ids - live_ids)
    for entry in sorted(new_entries, key=lambda e: e['id'], reverse=True):
        catalog['exams'].insert(0, entry)
    with open(catalog_path, 'w') as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
        f.write('\n')
    ts(f"Catalog: {len(new_entries)} new exam(s).")
    for entry in sorted(new_entries, key=lambda e: e['id'], reverse=True):
        ts(f"  + [{entry['id']}] {entry['name']}")
    for exam_id in removed_ids:
        ts(f"  - [{exam_id}] kept locally, missing from portal.")
    return new_entries, removed_ids


def save_targets(targets):
    """Writes the targets dict back to targets.json."""
    assert DATA_DIR is not None
    with open(os.path.join(DATA_DIR, 'targets.json'), 'w') as f:
        json.dump(targets, f, indent=2, ensure_ascii=False)
        f.write('\n')


def _suggestable(entry, targets_session):
    """Whether a new catalog entry is worth offering for this batch's targets."""
    lowered = entry['name'].lower()
    if 'special' in lowered:
        return False
    if 'old syllabus' in lowered or 'old curriculum' in lowered:
        return False
    session_mark = re.search(r'\((\d{4}-\d{4})\)', entry['name'])
    if session_mark and targets_session and session_mark.group(1) != targets_session:
        return False
    return True


def _batch_update_with_retry(worksheet, requests, value_input_option='RAW', max_retries=5):
    """Calls worksheet.batch_update() with retry + backoff for 429 rate limits."""
    for attempt in range(1, max_retries + 1):
        try:
            worksheet.batch_update(requests, value_input_option=value_input_option)
            return True
        except APIError as e:
            if '429' in str(e) or 'RATE_LIMIT_EXCEEDED' in str(e):
                if attempt < max_retries:
                    wait = 2 ** attempt
                    ts(f"[RATE LIMIT] Sheets API quota hit (attempt {attempt}/{max_retries}). Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    ts(f"[RATE LIMIT] Sheets API quota exceeded after {max_retries} attempts. Giving up.")
                    raise
            else:
                raise
    return False


def update_sheet_with_retake_data(worksheet, parsed_data, target_sheet_name,
                                   header_indices, all_reg_numbers_in_sheet,
                                   course_name_map, all_sheet_values,
                                   credit_hours=None):
    """Updates specific course cells with improved grades from a retake result.

    Only writes if the new grade point is higher than the existing grade,
    or if the cell is empty. Recalculates semester GPA if credit_hours provided.
    Returns the count of cells updated.
    """
    scraped_reg = parsed_data['Reg']
    try:
        target_list_index = all_reg_numbers_in_sheet.index(scraped_reg)
        target_row_num = target_list_index + 2
        existing_row_data = all_sheet_values[target_list_index + 1]
    except ValueError:
        ts(f"Could not find registration '{scraped_reg}' in sheet '{target_sheet_name}'. Skipping.")
        return 0

    ts(f"Found on row {target_row_num}. Comparing grades for retake update...")
    update_requests = []
    updated_count = 0

    for course in parsed_data.get('courses', []):
        course_code = course['code']
        new_grade = course['grade']
        new_point = grade_to_point(new_grade)

        col_index = find_course_column(course_code, course_name_map)
        if col_index is None:
            ts(f"  Course '{course_code}' not found in sheet columns. Skipping.")
            continue
        if col_index >= len(existing_row_data):
            ts(f"  Course '{course_code}' column index out of range in row. Skipping.")
            continue

        existing_val = existing_row_data[col_index]
        existing_point = grade_to_point(existing_val) if existing_val else 0.0

        if not existing_val:
            col_letter = gspread.utils.rowcol_to_a1(1, col_index + 1).rstrip('1')  # pyright: ignore[reportAttributeAccessIssue]
            update_requests.append({'range': f'{col_letter}{target_row_num}', 'values': [[new_grade]]})
            ts(f"  {course_code}: empty -> {new_grade}")
            updated_count += 1
        elif new_point > existing_point:
            col_letter = gspread.utils.rowcol_to_a1(1, col_index + 1).rstrip('1')  # pyright: ignore[reportAttributeAccessIssue]
            update_requests.append({'range': f'{col_letter}{target_row_num}', 'values': [[new_grade]]})
            ts(f"  {course_code}: {existing_val} -> {new_grade} (improved)")
            updated_count += 1
        else:
            ts(f"  {course_code}: {existing_val} unchanged (new: {new_grade})")

    if update_requests:
        _batch_update_with_retry(worksheet, update_requests, value_input_option='USER_ENTERED')
        ts(f"✅ Updated {updated_count} course grade(s) in '{target_sheet_name}'.")

        if credit_hours:
            sheet_row_2 = all_sheet_values[1] if len(all_sheet_values) > 1 else []
            updated_row = list(existing_row_data)
            for course in parsed_data.get('courses', []):
                col_index = find_course_column(course['code'], course_name_map)
                if col_index is not None and col_index < len(updated_row):
                    updated_row[col_index] = course['grade']
            recalculate_semester_gpa(worksheet, target_row_num, credit_hours,
                                     sheet_row_2, updated_row)
    else:
        ts("No grades to update — all unchanged or not found.")

    return updated_count


def select_retake_exam(force=False):
    """Interactive exam selector filtered for retake/improvement exams only."""
    current_config = load_config()
    if current_config.get("retake_exam") and not force:
        ts(f"Current retake exam: \"{current_config['retake_exam']}\"")
        ts("Use --force to re-select.")
        return

    ts("Loading available retake/improvement exams...")
    driver = initialize_webdriver()

    try:
        driver.get(URL)
        wait = WebDriverWait(driver, 15)

        Select(wait.until(EC.presence_of_element_located((By.ID, 'pro_id')))).select_by_visible_text(FORM_DATA['program'])
        Select(wait.until(EC.presence_of_element_located((By.ID, 'sess_id')))).select_by_visible_text(FORM_DATA['session'])

        exam_select = wait_for_exam_options(driver)

        all_exams = [normalize_text(opt.text) for opt in exam_select.options if opt.text.strip()]
        retake_keywords = ['improvement', 'retake']
        retake_exams = [e for e in all_exams if any(kw in e.lower() for kw in retake_keywords)]

        if not retake_exams:
            ts("No retake/improvement exams found for this program/session.")
            ts("Available exams:")
            for e in all_exams:
                ts(f"  - {e}")
            return

        ts(f"Found {len(retake_exams)} retake/improvement exam(s):")

        inquirerpy_available = False
        inq_prompt = None
        if USE_INQUIRERPY:
            try:
                from InquirerPy import prompt as _inq_prompt
                inq_prompt = _inq_prompt
                inquirerpy_available = True
            except ImportError:
                pass

        if inquirerpy_available and inq_prompt is not None:
            questions = [
                {
                    "type": "fuzzy",
                    "name": "exam",
                    "message": "Select retake/improvement exam:",
                    "choices": retake_exams,
                }
            ]
            with _alt_screen():
                answers = inq_prompt(questions)
            selected = answers["exam"]
        else:
            ts("")
            for i, opt in enumerate(retake_exams, 1):
                ts(f"  {i}. {opt}")
            ts("")

            while True:
                try:
                    choice = input(f"Select exam (1-{len(retake_exams)}): ").strip()
                    idx = int(choice) - 1
                    if 0 <= idx < len(retake_exams):
                        selected = retake_exams[idx]
                        break
                    ts(f"Invalid choice. Enter a number between 1 and {len(retake_exams)}.")
                except (ValueError, EOFError):
                    ts("Invalid input. Enter a number.")

        current_config["retake_exam"] = str(selected)
        save_config(current_config)
        ts(f"✅ Updated config.json with retake exam: \"{selected}\"")
    finally:
        driver.quit()


# ===================================================================
# Config / IO Functions
# ===================================================================
def load_env():
    """Parse KEY=value pairs from .env file."""
    env = {}
    assert ENV_FILE is not None
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def save_env(env_dict):
    """Write KEY=value pairs to .env file."""
    assert ENV_FILE is not None
    with open(ENV_FILE, 'w') as f:
        for key, value in env_dict.items():
            f.write(f'{key}={value}\n')


def save_config(config):
    """Write config dict back to config.json."""
    assert DATA_DIR is not None
    config_path = os.path.join(DATA_DIR, 'config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    ts(f"[INFO] Saved config to {config_path}")


def load_config():
    """Load config from config.json, creating it with defaults if missing."""
    config_path = os.path.join(DATA_DIR, 'config.json') if DATA_DIR else None
    if config_path and os.path.exists(config_path):
        with open(config_path, 'r') as f:
            user_config = json.load(f)
        config = {k: user_config.get(k, v) for k, v in DEFAULTS.items()}
        ts(f"[INFO] Loaded config from {config_path}")
    else:
        config = dict(DEFAULTS)
        if DATA_DIR:
            save_config(config)
            ts(f"[INFO] Created default config at {config_path}")
    return config


def _make_progress_key():
    """Create a deterministic key from the current config to scope progress."""
    key_parts = f"{GOOGLE_SHEET_URL}|{WORKSHEET_NAME}|{FORM_DATA['exam']}"
    return hashlib.md5(key_parts.encode()).hexdigest()[:12]


def _get_progress_file():
    """Get the progress file path for the current exam."""
    assert DATA_DIR is not None
    exam = FORM_DATA.get('exam', '')
    if exam:
        exam_hash = hashlib.md5(exam.encode()).hexdigest()[:8]
        return os.path.join(DATA_DIR, f'progress_{exam_hash}.json')
    return os.path.join(DATA_DIR, 'progress.json')


def load_progress():
    """Load list of already-scraped reg numbers from progress file."""
    progress_file = _get_progress_file()
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            current_key = _make_progress_key()
            if data.get("config_key") == current_key:
                scraped = data.get("scraped", [])
                ts(f"[INFO] Loaded progress: {len(scraped)} students already scraped")
                return scraped
            else:
                ts("[INFO] Progress file exists but config changed — starting fresh")
                return []
        ts(f"[INFO] Loaded progress (legacy): {len(data)} students already scraped")
        return data
    return []


def save_progress(scraped_list):
    """Save progress list to disk with config key for scoping."""
    progress_file = _get_progress_file()
    data = {
        "config_key": _make_progress_key(),
        "scraped": scraped_list
    }
    with open(progress_file, 'w') as f:
        json.dump(data, f)


# ===================================================================
# Interactive Functions
# ===================================================================
def first_run_setup(config):
    """Prompt user for critical config values on first run."""
    env = load_env()
    env_hint = " (loaded from .env)" if env else ""

    ts(f"\nFirst run detected — let's set up config.json.{env_hint}\n")

    inquirerpy_available = False
    inq_prompt = None
    if USE_INQUIRERPY:
        try:
            from InquirerPy import prompt as _inq_prompt
            inq_prompt = _inq_prompt
            inquirerpy_available = True
        except ImportError:
            pass

    if inquirerpy_available and inq_prompt is not None:
        questions = [
            {
                "type": "input",
                "name": "google_sheet_url",
                "message": "Google Sheet URL:",
                "default": env.get("GOOGLE_SHEET_URL") or config.get("google_sheet_url", ""),
            },
            {
                "type": "input",
                "name": "worksheet_name",
                "message": "Worksheet name:",
                "default": env.get("WORKSHEET_NAME") or config.get("worksheet_name", ""),
            },
            {
                "type": "input",
                "name": "program",
                "message": "Program name:",
                "default": env.get("PROGRAM") or config.get("program", DEFAULTS["program"]),
            },
            {
                "type": "input",
                "name": "session",
                "message": "Session (e.g. 2021-2022):",
                "default": env.get("SESSION") or config.get("session", DEFAULTS["session"]),
            },
            {
                "type": "input",
                "name": "start_regi",
                "message": "Start regi number:",
                "default": str(config.get("start_regi", 710)),
            },
            {
                "type": "input",
                "name": "end_regi",
                "message": "End regi number:",
                "default": str(config.get("end_regi", 813)),
            },
            {
                "type": "input",
                "name": "request_delay",
                "message": "Seconds between requests (4+ for concurrent runs):",
                "default": str(config.get("request_delay", 1)),
            },
        ]
        with _alt_screen():
            answers = inq_prompt(questions)
        config["google_sheet_url"] = str(answers["google_sheet_url"])
        config["worksheet_name"] = str(answers["worksheet_name"])
        config["program"] = str(answers["program"])
        config["session"] = str(answers["session"])
        config["start_regi"] = int(str(answers["start_regi"]))
        config["end_regi"] = int(str(answers["end_regi"]))
        config["request_delay"] = int(str(answers["request_delay"]))
    else:
        def _input(prompt_text, default):
            hint = f" [{default}]" if default else ""
            val = input(f"{prompt_text}{hint}: ").strip()
            return val if val else str(default)

        config["google_sheet_url"] = _input("Google Sheet URL", env.get("GOOGLE_SHEET_URL") or config.get("google_sheet_url", ""))
        config["worksheet_name"] = _input("Worksheet name", env.get("WORKSHEET_NAME") or config.get("worksheet_name", ""))
        config["program"] = _input("Program name", env.get("PROGRAM") or config.get("program", DEFAULTS["program"]))
        config["session"] = _input("Session (e.g. 2021-2022)", env.get("SESSION") or config.get("session", DEFAULTS["session"]))
        config["start_regi"] = int(_input("Start regi number", config.get("start_regi", 710)))
        config["end_regi"] = int(_input("End regi number", config.get("end_regi", 813)))
        config["request_delay"] = int(_input("Seconds between requests (4+ for concurrent runs)", config.get("request_delay", 1)))

    save_config(config)

    save_env({
        "GOOGLE_SHEET_URL": config["google_sheet_url"],
        "WORKSHEET_NAME": config["worksheet_name"],
        "PROGRAM": config["program"],
        "SESSION": config["session"],
    })
    ts("✅ Config saved.\n")


def select_exam(force=False):
    """Interactive exam selector."""
    current_config = load_config()
    if current_config.get("exam") and not force:
        ts(f"Current exam: \"{current_config['exam']}\"")
        ts("Use --force to re-select.")
        return

    ts("Loading available exams...")
    driver = initialize_webdriver()

    try:
        driver.get(URL)
        wait = WebDriverWait(driver, 15)

        Select(wait.until(EC.presence_of_element_located((By.ID, 'pro_id')))).select_by_visible_text(FORM_DATA['program'])
        Select(wait.until(EC.presence_of_element_located((By.ID, 'sess_id')))).select_by_visible_text(FORM_DATA['session'])

        exam_select = wait_for_exam_options(driver)

        exam_options = [normalize_text(opt.text) for opt in exam_select.options if opt.text.strip()]

        if not exam_options:
            ts("No exams found for this program/session.")
            return

        ts(f"Found {len(exam_options)} exam(s) for \"{FORM_DATA['program']}\"")

        inquirerpy_available = False
        inq_prompt = None
        if USE_INQUIRERPY:
            try:
                from InquirerPy import prompt as _inq_prompt
                inq_prompt = _inq_prompt
                inquirerpy_available = True
            except ImportError:
                pass

        if inquirerpy_available and inq_prompt is not None:
            questions = [
                {
                    "type": "fuzzy",
                    "name": "exam",
                    "message": "Select exam:",
                    "choices": exam_options,
                }
            ]
            with _alt_screen():
                answers = inq_prompt(questions)
            selected = answers["exam"]
        else:
            ts("")
            for i, opt in enumerate(exam_options, 1):
                ts(f"  {i}. {opt}")
            ts("")

            while True:
                try:
                    choice = input(f"Select exam (1-{len(exam_options)}): ").strip()
                    idx = int(choice) - 1
                    if 0 <= idx < len(exam_options):
                        selected = exam_options[idx]
                        break
                    ts(f"Invalid choice. Enter a number between 1 and {len(exam_options)}.")
                except (ValueError, EOFError):
                    ts("Invalid input. Enter a number.")

        current_config["exam"] = str(selected)
        save_config(current_config)
        ts(f"✅ Updated config.json with exam: \"{selected}\"")
    finally:
        driver.quit()


# ===================================================================
# Sheet Functions
# ===================================================================
def get_worksheet():
    """Authenticates with Google Sheets and returns the worksheet object."""
    ts("Authenticating with Google Sheets...")
    assert CREDENTIALS_FILE is not None
    if not os.path.exists(CREDENTIALS_FILE):
        ts(f"FATAL ERROR: credentials.json not found at: {CREDENTIALS_FILE}")
        return None

    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(GOOGLE_SHEET_URL)
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        ts(f"✅ Successfully connected to '{spreadsheet.title}' and selected worksheet '{worksheet.title}'.")
        return worksheet
    except Exception as e:
        ts(f"FATAL ERROR: Could not connect to Google Sheets. {e}")
        return None


def get_spreadsheet():
    """Authenticates with Google Sheets and returns the spreadsheet object."""
    ts("Authenticating with Google Sheets...")
    assert CREDENTIALS_FILE is not None
    if not os.path.exists(CREDENTIALS_FILE):
        ts(f"FATAL ERROR: credentials.json not found at: {CREDENTIALS_FILE}")
        return None

    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(GOOGLE_SHEET_URL)
        ts(f"✅ Successfully connected to '{spreadsheet.title}'.")
        return spreadsheet
    except Exception as e:
        ts(f"FATAL ERROR: Could not connect to Google Sheets. {e}")
        return None


def get_sheet_data(worksheet):
    """Fetches and processes data from the worksheet."""
    ts("Fetching sheet data for comparison...")
    all_sheet_values = worksheet.get_all_values()
    sheet_headers = all_sheet_values[0]

    try:
        header_indices = {
            "name": sheet_headers.index("Student's Name"),
            "roll": sheet_headers.index("Student's ID"),
            "reg": sheet_headers.index('Reg. No.'),
            "gpa": sheet_headers.index('GPA'),
            "cgpa": sheet_headers.index('CGPA'),
        }
    except ValueError as e:
        ts(f"FATAL ERROR: A required column header was not found in Row 1 of your sheet: {e}")
        ts(f"  Found headers: {sheet_headers}")
        return None, None, None, None, None, None

    try:
        header_indices["retake"] = sheet_headers.index('Retake Courses')
        has_retake_column = True
    except ValueError:
        header_indices["retake"] = len(sheet_headers)
        has_retake_column = False

    all_reg_numbers_in_sheet = [row[header_indices["reg"]] for row in all_sheet_values[1:]]
    row2_codes = all_sheet_values[1] if len(all_sheet_values) > 1 else []
    course_name_map = {}
    for i, header in enumerate(sheet_headers):
        if header.strip():
            course_name_map[sanitize_text(header.split('\n')[0].strip())] = i
    for i, code in enumerate(row2_codes):
        if code.strip():
            course_name_map[sanitize_text(code)] = i

    has_course_columns = header_indices['retake'] > header_indices['gpa'] + 2

    return header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, has_course_columns, has_retake_column


def setup_course_columns(worksheet, courses, next_col_index, has_retake_column):
    """Write course name headers (row 1) and course code headers (row 2) to the sheet."""
    ts(f"Setting up {len(courses)} course columns in the sheet...")

    course_name_cells = []
    course_code_cells = []
    for i, course in enumerate(courses):
        col_num = next_col_index + i + 1
        col_letter = gspread.utils.rowcol_to_a1(1, col_num).rstrip('1')  # pyright: ignore[reportAttributeAccessIssue]
        course_name_cells.append({'range': f'{col_letter}1', 'values': [[course['name']]]})
        course_code_cells.append({'range': f'{col_letter}2', 'values': [[course['code']]]})

    if not has_retake_column:
        retake_col_num = next_col_index + len(courses) + 1
        retake_col_letter = gspread.utils.rowcol_to_a1(1, retake_col_num).rstrip('1')  # pyright: ignore[reportAttributeAccessIssue]
        course_name_cells.append({'range': f'{retake_col_letter}1', 'values': [['Retake Courses']]})
        course_code_cells.append({'range': f'{retake_col_letter}2', 'values': [['']]})

    _batch_update_with_retry(worksheet, course_name_cells + course_code_cells)
    ts(f"✅ Created {len(courses)} course columns.")


def set_column_widths(worksheet, course_count, next_col_index):
    """Set column widths: 150px for course columns, 300px for retake column."""
    requests = []
    for i in range(course_count):
        col_idx = next_col_index + i
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1
                },
                "properties": {"pixelSize": 150},
                "fields": "pixelSize"
            }
        })
    retake_idx = next_col_index + course_count
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": worksheet.id,
                "dimension": "COLUMNS",
                "startIndex": retake_idx,
                "endIndex": retake_idx + 1
            },
            "properties": {"pixelSize": 300},
            "fields": "pixelSize"
        }
    })
    for attempt in range(1, 6):
        try:
            worksheet.spreadsheet.batch_update({"requests": requests})
            break
        except APIError as e:
            if ('429' in str(e) or 'RATE_LIMIT_EXCEEDED' in str(e)) and attempt < 5:
                wait = 2 ** attempt
                ts(f"[RATE LIMIT] Sheets API quota hit. Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
    ts(f"✅ Set column widths: 150px for {course_count} courses, 300px for retake.")


def update_sheet_with_student_data(worksheet, parsed_data, header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, retake_col_index):
    """Updates the Google Sheet with the scraped student data."""
    scraped_reg = parsed_data['Reg']
    try:
        target_list_index = all_reg_numbers_in_sheet.index(scraped_reg)
        target_row_num = target_list_index + 2
        existing_row_data = all_sheet_values[target_list_index + 1]
    except ValueError:
        ts(f"Could not find registration '{scraped_reg}' in the sheet. Skipping.")
        return

    ts(f"Found on row {target_row_num}. Checking for empty cells...")
    update_requests = []

    student_name = parsed_data.get('Name', '').title()
    student_roll = format_roll_number(parsed_data.get('Roll', ''))

    if not existing_row_data[header_indices['name']] and student_name:
        update_requests.append({'range': f'B{target_row_num}', 'values': [[student_name]]})
    if not existing_row_data[header_indices['roll']] and student_roll:
        update_requests.append({'range': f'C{target_row_num}', 'values': [[student_roll]]})
    if not existing_row_data[header_indices['gpa']] and parsed_data.get('GPA'):
        update_requests.append({'range': f'E{target_row_num}', 'values': [[parsed_data.get('GPA')]]})
    if not existing_row_data[header_indices['cgpa']] and parsed_data.get('CGPA'):
        update_requests.append({'range': f'F{target_row_num}', 'values': [[parsed_data.get('CGPA')]]})

    scraped_fail_subs = parsed_data.get('Fail Subs')
    if scraped_fail_subs and retake_col_index < len(existing_row_data) and not existing_row_data[retake_col_index]:
        retake_col_letter = gspread.utils.rowcol_to_a1(1, retake_col_index + 1).rstrip('1')  # pyright: ignore[reportAttributeAccessIssue]
        update_requests.append({'range': f'{retake_col_letter}{target_row_num}', 'values': [[scraped_fail_subs]]})

    for course in parsed_data.get('courses', []):
        sanitized_name = sanitize_text(course['name'])
        if sanitized_name in course_name_map:
            col_index = course_name_map[sanitized_name]
            if col_index < len(existing_row_data) and not existing_row_data[col_index]:
                col_letter = gspread.utils.rowcol_to_a1(1, col_index + 1).rstrip('1')  # pyright: ignore[reportAttributeAccessIssue]
                update_requests.append({'range': f'{col_letter}{target_row_num}', 'values': [[course['grade']]]})

    if update_requests:
        _batch_update_with_retry(worksheet, update_requests, value_input_option='USER_ENTERED')
        ts(f"✅ Successfully wrote {len(update_requests)} new value(s) to the Google Sheet.")
    else:
        ts("No empty cells to update. Data is already present.")


def format_gpa_cgpa_columns(worksheet):
    """Converts and formats GPA/CGPA columns in the sheet."""
    ts("\n--- Converting and formatting GPA/CGPA columns... ---")
    try:
        ranges_to_process = ["E3:E", "F3:F"]
        data_from_ranges = worksheet.batch_get(ranges_to_process, value_render_option='UNFORMATTED_VALUE')
        update_payload = []

        for i, values_list in enumerate(data_from_ranges):
            range_name = ranges_to_process[i]
            converted_values = []
            for row in values_list:
                cell_value = row[0] if row else ''
                try:
                    numeric_value = float(cell_value)
                    converted_values.append([numeric_value])
                except (ValueError, TypeError):
                    converted_values.append([cell_value])

            update_payload.append({
                'range': range_name,
                'values': converted_values
            })

        if update_payload:
            _batch_update_with_retry(worksheet, update_payload, value_input_option='USER_ENTERED')
            ts("Step 1/2: Successfully converted any existing text values to numbers.")

        worksheet.format(ranges_to_process, {
            "numberFormat": {
                "type": "NUMBER",
                "pattern": "0.00"
            }
        })
        ts("Step 2/2: ✅ Successfully applied number formatting to GPA and CGPA columns.")
    except Exception as e:
        ts(f"⚠️ Could not apply formatting. Error: {e}")


def _load_credit_hours():
    """Load credit hours mapping from data/cse_credit_hours.json."""
    if not DATA_DIR:
        return None
    path = os.path.join(DATA_DIR, 'cse_credit_hours.json')
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)


def recalculate_semester_gpa(worksheet, target_row_num, credit_hours,
                              sheet_row_2_codes, student_row_data):
    """Recalculate and write semester GPA after course grade updates.

    Reads course codes from sheet row 2, looks up credit hours and grade
    points from the student row, computes weighted average, and writes
    to column E (GPA). Uses max(existing, calculated) as sanity check.
    Returns True if GPA was updated, False otherwise.
    """
    if not credit_hours:
        return False

    sanitized_credits = {sanitize_text(k): v for k, v in credit_hours.items()}
    total_points = 0.0
    total_credits = 0.0

    for col_index, code in enumerate(sheet_row_2_codes):
        if not code or not str(code).strip():
            continue
        code_str = str(code).strip()
        credits = sanitized_credits.get(sanitize_text(code_str))
        if credits is None:
            continue

        grade_val = student_row_data[col_index] if col_index < len(student_row_data) else ''
        if not grade_val or not str(grade_val).strip():
            continue

        point = grade_to_point(str(grade_val))
        total_points += point * credits
        total_credits += credits

    if total_credits == 0:
        return False

    formula_gpa = round(total_points / total_credits, 2)

    existing_vals = worksheet.batch_get([f'E{target_row_num}'], value_render_option='FORMATTED_VALUE')[0]
    existing_gpa = 0.0
    if existing_vals and existing_vals[0] and existing_vals[0][0]:
        try:
            existing_gpa = float(existing_vals[0][0])
        except (ValueError, TypeError):
            existing_gpa = 0.0

    if existing_gpa > formula_gpa:
        ts(f"  GPA {existing_gpa} (existing) > {formula_gpa} (calculated) — keeping existing")
        final_gpa = existing_gpa
    else:
        final_gpa = formula_gpa

    worksheet.update_cell(target_row_num, 5, final_gpa)
    ts(f"  Recalculated GPA: {final_gpa} (from {total_credits} credits)")
    return True


# ===================================================================
# Scraper Functions
# ===================================================================
def initialize_webdriver():
    """Initializes and returns the Selenium WebDriver."""
    if BROWSER == "chrome":
        ts("WebDriver: Initializing Chrome.")
        options = ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = webdriver.Chrome(options=options)
    else:
        ts("WebDriver: Initializing Firefox.")
        options = FirefoxOptions()
        options.add_argument("--headless")
        driver = webdriver.Firefox(options=options)
    return driver


def wait_for_exam_options(driver, timeout=10):
    """Waits for the portal's exam dropdown to populate after program selection.

    The exam list loads asynchronously (ajax/get_program_by_exam.php), so
    reading #exam_id immediately often yields only the placeholder option.
    Polls until at least one real exam option exists, then returns a Select.
    Raises TimeoutException on timeout — callers' retry logic handles it.
    """
    deadline = time.time() + timeout
    while True:
        exam_select = Select(driver.find_element(By.ID, 'exam_id'))
        real_options = [opt for opt in exam_select.options if opt.text.strip()]
        if len(real_options) > 1:  # placeholder + at least one exam
            return exam_select
        if time.time() >= deadline:
            raise TimeoutException("Timed out waiting for exam dropdown to populate.")
        time.sleep(0.5)


def scrape_student_result(driver, reg_num):
    """Scrapes the result for a single student, with retries."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        ts(f"\n--- Processing Registration No.: {reg_num} (attempt {attempt}/{RETRY_ATTEMPTS}) ---")
        try:
            driver.get(URL)
            wait = WebDriverWait(driver, 15)

            Select(wait.until(EC.presence_of_element_located((By.ID, 'pro_id')))).select_by_visible_text(FORM_DATA['program'])
            Select(wait.until(EC.presence_of_element_located((By.ID, 'sess_id')))).select_by_visible_text(FORM_DATA['session'])

            normalized_exam = normalize_text(FORM_DATA['exam'])
            exam_select = wait_for_exam_options(driver)
            matched = False
            for option in exam_select.options:
                if normalize_text(option.text) == normalized_exam:
                    option.click()
                    matched = True
                    break
            if not matched:
                available = [normalize_text(o.text) for o in exam_select.options if o.text.strip()]
                ts(f"[ERROR] Exam '{FORM_DATA['exam']}' not found. Available:")
                for opt in available:
                    ts(f"  - {opt}")
                return None

            driver.find_element(By.ID, 'reg_no').send_keys(reg_num)
            driver.find_element(By.XPATH, "//button[text()='Submit']").click()

            wait.until(EC.presence_of_element_located((By.XPATH, "//h3[contains(text(), 'Result')]")))
            time.sleep(0.5)

            parsed_data = parse_result_html(driver.page_source)

            if not parsed_data or 'Reg' not in parsed_data:
                ts(f"Result not found or page is invalid for Reg No: {reg_num}.")
                return None

            ts(f"Found result for Reg No: {parsed_data['Reg']} (Name: {parsed_data.get('Name', 'N/A')})")
            return parsed_data

        except (TimeoutException, NoSuchElementException) as e:
            ts(f"[WARN] Attempt {attempt} failed for {reg_num}: {type(e).__name__}: {e}")
            if attempt < RETRY_ATTEMPTS:
                wait_time = RETRY_BACKOFF * (2 ** (attempt - 1))
                ts(f"[INFO] Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                ts(f"[ERROR] All {RETRY_ATTEMPTS} attempts failed for {reg_num}. Skipping.")
                return None
        except Exception as e:
            ts(f"[ERROR] Unexpected error for {reg_num}: {e}")
            return None


def _load_retake_progress():
    """Load list of already-scraped reg numbers from retake progress file."""
    progress_file = _get_retake_progress_file()
    if os.path.exists(progress_file):
        with open(progress_file, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            current_key = _make_retake_progress_key()
            if data.get("config_key") == current_key:
                scraped = data.get("scraped", [])
                ts(f"[INFO] Loaded retake progress: {len(scraped)} students already scraped")
                return scraped
            else:
                ts("[INFO] Retake progress file exists but config changed — starting fresh")
                return []
        return data
    return []


def _save_retake_progress(scraped_list):
    """Save retake progress list to disk with config key for scoping."""
    progress_file = _get_retake_progress_file()
    data = {
        "config_key": _make_retake_progress_key(),
        "scraped": scraped_list
    }
    with open(progress_file, 'w') as f:
        json.dump(data, f)


def _make_retake_progress_key():
    """Create a deterministic key from the current config to scope retake progress."""
    key_parts = f"{GOOGLE_SHEET_URL}|{WORKSHEET_NAME}|{FORM_DATA.get('retake_exam', '')}"
    return hashlib.md5(key_parts.encode()).hexdigest()[:12]


def _get_retake_progress_file():
    """Get the progress file path for the current retake exam."""
    assert DATA_DIR is not None
    exam = FORM_DATA.get('retake_exam', '')
    if exam:
        exam_hash = hashlib.md5(exam.encode()).hexdigest()[:8]
        return os.path.join(DATA_DIR, f'progress_retake_{exam_hash}.json')
    return os.path.join(DATA_DIR, 'progress_retake.json')


def scrape_retake_results(dry_run=False, reg_num=None, regs=None, on_event=None):
    """Main retake scraping loop.

    Fetches retake/improvement results and updates PerCourse sheets with
    improved grades. Uses separate progress tracking from normal mode.
    regs is an explicit reg list (takes precedence over reg_num/range).
    on_event(kind) is called per student with 'skip', 'ok', or 'fail'.
    Returns the stats dict.
    """
    start_time = time.time()
    stats = {"updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}

    retake_exam = CONFIG.get('retake_exam', '')
    if not retake_exam:
        ts("ERROR: No retake exam configured. Run with --list-exams --retake to select one.")
        return stats

    if reg_num:
        start_regi = reg_num
        end_regi = reg_num
    else:
        start_regi = START_REGI
        end_regi = END_REGI

    if dry_run:
        ts("[DRY RUN] Scraping retake results without writing to sheet.")

    ts("\n--- Retake Mode ---")
    ts(f"--- Retake Exam: \"{retake_exam}\" ---")

    scraped_list = _load_retake_progress()
    targets, label = _reg_targets(regs, reg_num, start_regi, end_regi)
    if not targets:
        ts("[INFO] No reg numbers to scrape.")
        return stats
    total = len(targets)
    in_targets = set(targets)
    already_done = len([r for r in scraped_list if str(r).isdigit() and int(r) in in_targets])
    remaining = total - already_done

    ts(f"\n--- Will scrape {label} ({total} students, {remaining} remaining) ---")
    if not dry_run:
        ts("--- Press Ctrl+C to cancel ---\n")

    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        return

    driver = initialize_webdriver()

    # Save original exam and set retake exam
    original_exam = FORM_DATA['exam']
    FORM_DATA['exam'] = retake_exam  # pyright: ignore[reportConstantRedefinition]

    stats = {"updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    interrupted = False

    try:
        for i, regi_num in enumerate(targets):
            reg_str = str(regi_num)

            if reg_str in scraped_list:
                ts(f"[SKIP] Reg {reg_str} already scraped (from progress file)")
                stats["skipped"] += 1
                if on_event:
                    on_event("skip")
                continue

            ts(f"\n--- Processing Registration No.: {reg_str} ---")
            parsed_data = None
            for attempt in range(1, RETRY_ATTEMPTS + 1):
                try:
                    driver.get(URL)
                    wait = WebDriverWait(driver, 15)

                    Select(wait.until(EC.presence_of_element_located((By.ID, 'pro_id')))).select_by_visible_text(FORM_DATA['program'])
                    Select(wait.until(EC.presence_of_element_located((By.ID, 'sess_id')))).select_by_visible_text(FORM_DATA['session'])

                    normalized_exam = normalize_text(FORM_DATA['exam'])
                    exam_select = wait_for_exam_options(driver)
                    matched = False
                    for option in exam_select.options:
                        if normalize_text(option.text) == normalized_exam:
                            option.click()
                            matched = True
                            break
                    if not matched:
                        ts("[ERROR] Retake exam not found in dropdown.")
                        break

                    driver.find_element(By.ID, 'reg_no').send_keys(reg_str)
                    driver.find_element(By.XPATH, "//button[text()='Submit']").click()

                    wait.until(EC.presence_of_element_located((By.XPATH, "//h3[contains(text(), 'Result')]")))
                    time.sleep(0.5)

                    parsed_data = parse_retake_result_html(driver.page_source)
                    if parsed_data and 'Reg' in parsed_data:
                        break
                    else:
                        ts(f"[WARN] Retake result not found for Reg {reg_str}.")
                        parsed_data = None
                        break

                except (TimeoutException, NoSuchElementException) as e:
                    ts(f"[WARN] Attempt {attempt} failed for {reg_str}: {type(e).__name__}: {e}")
                    if attempt < RETRY_ATTEMPTS:
                        wait_time = RETRY_BACKOFF * (2 ** (attempt - 1))
                        ts(f"[INFO] Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        ts(f"[ERROR] All {RETRY_ATTEMPTS} attempts failed for {reg_str}. Skipping.")
                except Exception as e:
                    ts(f"[ERROR] Unexpected error for {reg_str}: {e}")
                    break

            if parsed_data and parsed_data.get('courses'):
                exam_title = parsed_data.get('exam_title', '')
                target_sheet_name = map_exam_to_sheet(exam_title)

                if not target_sheet_name:
                    ts(f"Could not determine target sheet from exam title: '{exam_title}'. Skipping.")
                    stats["failed"] += 1
                else:
                    ts(f"Target sheet: {target_sheet_name}")

                    if dry_run:
                        courses_str = ", ".join([f"{c['code']}: {c['grade']}" for c in parsed_data['courses']])
                        ts(f"[DRY RUN] Reg {reg_str} → {parsed_data.get('Name', '?')}, Courses: [{courses_str}]")
                        stats["updated"] += 1
                    else:
                        target_ws = None
                        try:
                            target_ws = spreadsheet.worksheet(target_sheet_name)
                        except Exception:
                            ts(f"Could not open worksheet '{target_sheet_name}'. Skipping.")
                            stats["failed"] += 1
                            if on_event:
                                on_event("fail")
                            scraped_list.append(reg_str)
                            if not dry_run:
                                _save_retake_progress(scraped_list)
                            if REQUEST_DELAY > 0 and i < total - 1:
                                time.sleep(REQUEST_DELAY)
                            continue

                        header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, _, _ = get_sheet_data(target_ws)
                        if not header_indices:
                            ts(f"Could not read sheet data from '{target_sheet_name}'. Skipping.")
                            stats["failed"] += 1
                        else:
                            credit_hours = _load_credit_hours()
                            updated = update_sheet_with_retake_data(
                                target_ws, parsed_data, target_sheet_name,
                                header_indices, all_reg_numbers_in_sheet,
                                course_name_map, all_sheet_values,
                                credit_hours
                            )
                            if updated > 0:
                                stats["updated"] += 1
                            else:
                                stats["unchanged"] += 1
                            if on_event:
                                on_event("ok")

                scraped_list.append(reg_str)
                if not dry_run:
                    _save_retake_progress(scraped_list)
            elif parsed_data and not parsed_data.get('courses'):
                ts(f"No retake courses found for Reg {reg_str}. Student may not have taken this retake.")
                stats["unchanged"] += 1
                if on_event:
                    on_event("ok")
                scraped_list.append(reg_str)
                if not dry_run:
                    _save_retake_progress(scraped_list)
            else:
                stats["failed"] += 1
                if on_event:
                    on_event("fail")

            if REQUEST_DELAY > 0 and i < total - 1:
                time.sleep(REQUEST_DELAY)

    except KeyboardInterrupt:
        interrupted = True
        ts("\nInterrupted — saving progress...")
    finally:
        FORM_DATA['exam'] = original_exam  # pyright: ignore[reportConstantRedefinition]
        driver.quit()
        ts("WebDriver closed.")

    if dry_run:
        ts("[INFO] Dry run — retake progress file left untouched.")
    elif not interrupted and stats['failed'] == 0:
        _save_retake_progress([])
        ts("[INFO] Retake progress file cleared — all students processed successfully.")
    elif stats['failed'] > 0:
        ts(f"[INFO] Retake progress file kept — {stats['failed']} student(s) failed and will be retried on next run.")

    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    ts(f"\n--- Retake Summary{'(interrupted)' if interrupted else ''}{'(dry run)' if dry_run else ''} ---")
    ts(f"Updated (grade improved): {stats['updated']}")
    ts(f"Unchanged: {stats['unchanged']}")
    ts(f"Skipped (already done): {stats['skipped']}")
    ts(f"Failed: {stats['failed']}")
    ts(f"Total time: {minutes}m {seconds}s")
    return stats


def main(dry_run=False, reg_num=None, regs=None, on_event=None):
    """Main execution function to run the scraper and update the sheet.

    regs is an explicit reg list (takes precedence over reg_num/range).
    on_event(kind) is called per student with 'skip', 'ok', or 'fail'
    (for live progress displays). Returns the stats dict.
    """
    start_time = time.time()
    stats = {"scraped": 0, "skipped": 0, "failed": 0}

    if reg_num:
        start_regi = reg_num
        end_regi = reg_num
    else:
        start_regi = START_REGI
        end_regi = END_REGI

    targets, label = _reg_targets(regs, reg_num, start_regi, end_regi)
    if not targets:
        ts("[INFO] No reg numbers to scrape.")
        return stats

    if dry_run:
        ts("[DRY RUN] Scraping without writing to sheet.")

    worksheet = get_worksheet()
    if not worksheet:
        return

    header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, has_course_columns, has_retake_column = get_sheet_data(worksheet)
    if not header_indices:
        return

    scraped_list = load_progress()
    total = len(targets)
    in_targets = set(targets)
    already_done = len([r for r in scraped_list if str(r).isdigit() and int(r) in in_targets])
    remaining = total - already_done

    ts(f"\n--- Will scrape {label} ({total} students, {remaining} remaining) ---")
    ts(f"--- Sheet: '{WORKSHEET_NAME}' ---")
    if not dry_run:
        ts("--- Press Ctrl+C to cancel ---\n")

    driver = initialize_webdriver()

    stats = {"scraped": 0, "skipped": 0, "failed": 0}
    course_setup_done = has_course_columns
    interrupted = False

    try:
        for i, regi_num in enumerate(targets):
            reg_str = str(regi_num)

            if reg_str in scraped_list:
                ts(f"[SKIP] Reg {reg_str} already scraped (from progress file)")
                stats["skipped"] += 1
                if on_event:
                    on_event("skip")
                continue

            parsed_data = scrape_student_result(driver, reg_str)
            if parsed_data:
                if dry_run:
                    courses_str = ", ".join([f"{c['code']}: {c['grade']}" for c in parsed_data.get('courses', [])])
                    ts(f"[DRY RUN] Reg {reg_str} → {parsed_data.get('Name', '?')}, GPA: {parsed_data.get('GPA', '?')}, CGPA: {parsed_data.get('CGPA', '?')}, Courses: [{courses_str}]")
                else:
                    if not course_setup_done and parsed_data.get('courses'):
                        next_col_index = header_indices['retake'] + (0 if has_retake_column else 1)
                        setup_course_columns(worksheet, parsed_data['courses'], next_col_index, has_retake_column)
                        set_column_widths(worksheet, len(parsed_data['courses']), next_col_index)
                        header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, _, _ = get_sheet_data(worksheet)
                        assert header_indices is not None
                        course_setup_done = True

                    update_sheet_with_student_data(worksheet, parsed_data, header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, header_indices['retake'])

                scraped_list.append(reg_str)
                if not dry_run:
                    save_progress(scraped_list)
                stats["scraped"] += 1
                if on_event:
                    on_event("ok")
            else:
                stats["failed"] += 1
                if on_event:
                    on_event("fail")

            if REQUEST_DELAY > 0 and i < total - 1:
                time.sleep(REQUEST_DELAY)
    except KeyboardInterrupt:
        interrupted = True
        ts("\nInterrupted — saving progress...")
    finally:
        driver.quit()
        ts("WebDriver closed.")

    if not dry_run:
        format_gpa_cgpa_columns(worksheet)

    if dry_run:
        ts("[INFO] Dry run — progress file left untouched.")
    elif not interrupted and stats['failed'] == 0:
        save_progress([])
        ts("[INFO] Progress file cleared — all students scraped successfully.")
    elif stats['failed'] > 0:
        ts(f"[INFO] Progress file kept — {stats['failed']} student(s) failed and will be retried on next run.")

    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    ts(f"\n--- Summary{'(interrupted)' if interrupted else ''}{'(dry run)' if dry_run else ''} ---")
    ts(f"Scraped: {stats['scraped']}")
    ts(f"Skipped (already done): {stats['skipped']}")
    ts(f"Failed: {stats['failed']}")
    ts(f"Total time: {minutes}m {seconds}s")
    return stats


# ===================================================================
# CLI / UI Functions
# ===================================================================
def show_config():
    """Print the active configuration."""
    ts("\n--- Active Configuration ---")
    ts(f"  Sheet URL:       {GOOGLE_SHEET_URL or '(not set)'}")
    ts(f"  Worksheet:       {WORKSHEET_NAME or '(not set)'}")
    ts(f"  Program:         {FORM_DATA['program']}")
    ts(f"  Session:         {FORM_DATA['session']}")
    ts(f"  Exam:            {FORM_DATA['exam'] or '(not set — will auto-select)'}")
    if RETAKE_MODE:
        ts(f"  Retake exam:     {FORM_DATA.get('retake_exam') or CONFIG.get('retake_exam') or '(not set)'}")
    ts(f"  Reg range:       {START_REGI} – {END_REGI} ({END_REGI - START_REGI + 1} students)")
    ts(f"  Request delay:   {REQUEST_DELAY}s")
    ts(f"  Credentials:     {CREDENTIALS_FILE}")
    ts(f"  Progress file:   {_get_progress_file()}")
    ts("")


def show_status():
    """Print progress status."""
    scraped = load_progress()
    total = END_REGI - START_REGI + 1
    remaining = total - len(scraped)
    ts("\n--- Progress Status ---")
    ts(f"  Total students:  {total}")
    ts(f"  Scraped:         {len(scraped)}")
    ts(f"  Remaining:       {remaining}")
    ts(f"  Progress file:   {_get_progress_file()}")
    if scraped:
        ts(f"  Scraped regs:    {', '.join(scraped[:10])}{'...' if len(scraped) > 10 else ''}")
    ts("")


def validate_config():
    """Test browser + sheet connection before a long scrape."""
    ts("\n--- Pre-flight Validation ---\n")
    errors = []

    ts("[1/3] Testing Google Sheets connection...")
    worksheet = get_worksheet()
    if worksheet:
        ts("  ✅ Sheet connected successfully")
    else:
        errors.append("Google Sheets connection failed")

    ts("[2/3] Testing browser and portal dropdowns...")
    driver = None
    try:
        driver = initialize_webdriver()
        driver.get(URL)
        wait = WebDriverWait(driver, 15)

        try:
            Select(wait.until(EC.presence_of_element_located((By.ID, 'pro_id')))).select_by_visible_text(FORM_DATA['program'])
            ts(f"  ✅ Program: \"{FORM_DATA['program']}\"")
        except (NoSuchElementException, TimeoutException) as e:
            ts(f"  ❌ Program not found: \"{FORM_DATA['program']}\"")
            errors.append(f"Program dropdown: {e}")

        try:
            Select(wait.until(EC.presence_of_element_located((By.ID, 'sess_id')))).select_by_visible_text(FORM_DATA['session'])
            ts(f"  ✅ Session: \"{FORM_DATA['session']}\"")
        except (NoSuchElementException, TimeoutException) as e:
            ts(f"  ❌ Session not found: \"{FORM_DATA['session']}\"")
            errors.append(f"Session dropdown: {e}")

        if FORM_DATA['exam']:
            try:
                exam_select = wait_for_exam_options(driver)
                normalized_exam = normalize_text(FORM_DATA['exam'])
                matched = any(normalize_text(opt.text) == normalized_exam for opt in exam_select.options if opt.text.strip())
                if matched:
                    ts(f"  ✅ Exam: \"{FORM_DATA['exam']}\"")
                else:
                    ts(f"  ❌ Exam not found: \"{FORM_DATA['exam']}\"")
                    errors.append("Exam not found in dropdown")
            except (NoSuchElementException, TimeoutException) as e:
                ts(f"  ❌ Exam dropdown error: {e}")
                errors.append(f"Exam dropdown: {e}")
        else:
            ts("  ⚠️  No exam configured — will auto-select on first run")

    except Exception as e:
        ts(f"  ❌ Browser error: {e}")
        errors.append(f"Browser: {e}")
    finally:
        if driver:
            driver.quit()

    ts("[3/3] Testing single student scrape...")
    if not errors:
        try:
            driver = initialize_webdriver()
            result = scrape_student_result(driver, str(START_REGI))
            if result:
                ts(f"  ✅ Reg {START_REGI}: {result.get('Name', '?')} — GPA: {result.get('GPA', '?')}, CGPA: {result.get('CGPA', '?')}")
            else:
                ts(f"  ❌ Reg {START_REGI}: No result found")
                errors.append(f"Scrape test failed for reg {START_REGI}")
        except Exception as e:
            ts(f"  ❌ Scrape error: {e}")
            errors.append(f"Scrape test: {e}")
        finally:
            if driver:
                driver.quit()

    ts("\n--- Validation Summary ---")
    if errors:
        ts(f"❌ {len(errors)} error(s) found:")
        for err in errors:
            ts(f"  - {err}")
        return False
    else:
        ts("✅ All checks passed — ready to scrape!")
        return True


# ===================================================================
# Interactive Menu (zero-flag flow, local only)
# ===================================================================
def _short_label(title):
    """Strips the program prefix for compact menu labels."""
    return re.sub(r'^B\.Sc\. in Computer Science and Engineering\s+', '', normalize_text(title))


@contextlib.contextmanager
def _alt_screen():
    """Runs a menu prompt on the terminal's alternate screen buffer.

    Menu chrome never touches scrollback (Yazi-style); only intentional output
    stays. Silent no-op when stdout isn't a real terminal.
    """
    if sys.stdout.isatty() and os.environ.get("TERM", "") not in ("", "dumb"):
        sys.stdout.write("\x1b[?1049h")
        sys.stdout.flush()
        try:
            yield True
        finally:
            sys.stdout.write("\x1b[?1049l")
            sys.stdout.flush()
    else:
        yield False


def _parse_range(text, default_start, default_end):
    """Parses '710-813' or '810' into (start, end); raises ValueError."""
    text = (text or "").strip()
    match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', text)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            raise ValueError(f"Start {start} is after end {end}.")
        return start, end
    if text.isdigit():
        return int(text), int(text)
    if not text:
        return default_start, default_end
    raise ValueError(f"Could not parse range '{text}' (e.g. 710-813).")


def _parse_regs(text, default_start, default_end):
    """Parses reg input into a sorted unique reg list.

    Accepts '710-813', '810', '710,715,720' and mixed '710-713,720,725-730'.
    Empty input returns None (caller falls back to the default range).
    Raises ValueError on garbage.
    """
    text = (text or "").strip()
    if not text:
        return None
    regs = []
    for part in text.split(","):
        part = part.strip()
        match = re.fullmatch(r'(\d+)\s*-\s*(\d+)', part) if part else None
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                raise ValueError(f"Start {start} is after end {end}.")
            regs.extend(range(start, end + 1))
        elif part.isdigit():
            regs.append(int(part))
        else:
            raise ValueError(f"Could not parse '{part}' (e.g. 710-813 or 710,715,720).")
    return sorted(set(regs))


def _reg_targets(regs, reg_num, start_regi, end_regi):
    """Resolves a scrape loop's reg list + display label.

    A contiguous list renders exactly like the legacy range output.
    """
    if regs is not None:
        targets = sorted(set(regs))
    elif reg_num:
        targets = [reg_num]
    else:
        targets = list(range(start_regi, end_regi + 1))
    if len(targets) > 1 and targets == list(range(targets[0], targets[-1] + 1)):
        label = f"reg {targets[0]}–{targets[-1]}"
    elif len(targets) == 1:
        label = f"reg {targets[0]}"
    else:
        shown = ", ".join(str(r) for r in targets[:10])
        label = f"{len(targets)} regs: {shown}{'...' if len(targets) > 10 else ''}"
    return targets, label


def _ensure_sheet_configured():
    """Runs first-run setup if sheet URL or worksheet is missing (exits on cancel)."""
    if not CONFIG.get("google_sheet_url") or not CONFIG.get("worksheet_name"):
        try:
            first_run_setup(CONFIG)
        except (KeyboardInterrupt, Exception):
            ts("\nSetup cancelled.")
            sys.exit(1)
        _sync_globals_from_config()


def _sync_globals_from_config():
    """Copies CONFIG into module globals (sheet URL, worksheet, form data, range)."""
    global GOOGLE_SHEET_URL, WORKSHEET_NAME, START_REGI, END_REGI, REQUEST_DELAY
    GOOGLE_SHEET_URL = str(CONFIG.get("google_sheet_url", ""))  # pyright: ignore[reportConstantRedefinition]
    WORKSHEET_NAME = str(CONFIG.get("worksheet_name", ""))  # pyright: ignore[reportConstantRedefinition]
    FORM_DATA["program"] = str(CONFIG.get("program", ""))
    FORM_DATA["session"] = str(CONFIG.get("session", ""))
    FORM_DATA["exam"] = str(CONFIG.get("exam", ""))
    START_REGI = int(CONFIG.get("start_regi", START_REGI))  # pyright: ignore[reportConstantRedefinition]
    END_REGI = int(CONFIG.get("end_regi", END_REGI))  # pyright: ignore[reportConstantRedefinition]
    try:
        REQUEST_DELAY = int(CONFIG.get("request_delay", REQUEST_DELAY))  # pyright: ignore[reportConstantRedefinition]
    except (TypeError, ValueError):
        ts(f"[WARN] Invalid request_delay {CONFIG.get('request_delay')!r} — keeping {REQUEST_DELAY}s.")


# ===================================================================
# Entry Point
# ===================================================================
def run():
    """CLI argument parsing and main logic. Call from entry point scripts."""
    global CONFIG, GOOGLE_SHEET_URL, WORKSHEET_NAME, FORM_DATA
    global START_REGI, END_REGI, REQUEST_DELAY, LOG_FILE, RETAKE_MODE

    import argparse

    parser = argparse.ArgumentParser(
        description="DUCMC Result Scraper — run with no arguments for the interactive menu.",
        epilog="Examples:\n"
               "  python result_scrapper.py\n"
               "  python result_scrapper.py --exam L3T2\n"
               "  python result_scrapper.py --retake --retake-exam L1T2R-2024\n"
               "  python result_scrapper.py --all\n"
               "  python result_scrapper.py --retake --all --dry-run\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    basic = parser.add_argument_group("basic options")
    basic.add_argument("--exam", type=str, help="Exam code (e.g. L1T2) or name (overrides config, skips interactive selection)")
    basic.add_argument("--retake", action="store_true", help="Retake/improvement mode — update PerCourse sheets with improved grades")
    basic.add_argument("--retake-exam", type=str, help="Retake exam code (e.g. L1T2R, L1T2R-2024) or name (overrides config)")
    basic.add_argument("--all", action="store_true", help="Scrape every targets.json exam for the mode, sequentially")
    basic.add_argument("--dry-run", action="store_true", help="Scrape without writing to sheet (leaves progress files untouched)")
    basic.add_argument("--reg", type=int, help="Scrape a single reg number")
    advanced = parser.add_argument_group("advanced options")
    advanced.add_argument("--list-exams", action="store_true", help="Interactively select an exam and update config.json")
    advanced.add_argument("--force", action="store_true", help="Force re-select even if exam is already set")
    advanced.add_argument("--fresh", action="store_true", help="Ignore progress files and start from scratch")
    advanced.add_argument("--log", action="store_true", help="Save output to a log file")
    advanced.add_argument("--sheet-url", type=str, help="Google Sheet URL (overrides config)")
    advanced.add_argument("--worksheet", type=str, help="Worksheet name (overrides config)")
    advanced.add_argument("--program", type=str, help="Program name (overrides config)")
    advanced.add_argument("--session", type=str, help="Session year (overrides config)")
    advanced.add_argument("--start-regi", type=int, help="Starting reg number (overrides config)")
    advanced.add_argument("--end-regi", type=int, help="Ending reg number (overrides config)")
    advanced.add_argument("--validate", action="store_true", help="Test browser + sheet connection, then exit")
    advanced.add_argument("--show-config", action="store_true", help="Print active config and exit")
    advanced.add_argument("--status", action="store_true", help="Print progress status and exit")
    args = parser.parse_args()

    if len(sys.argv) == 1 and USE_INQUIRERPY:
        try:
            from tui import run_tui
        except ImportError as e:
            ts(f"[ERROR] TUI unavailable ({e}) — use CLI flags (see --help).")
            return
        try:
            run_tui()
        except (KeyboardInterrupt, EOFError):
            ts("\nCancelled.")
        except Exception as e:
            ts(f"\n[ERROR] TUI failed: {e}")
        return

    RETAKE_MODE = args.retake  # pyright: ignore[reportConstantRedefinition]

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
    if args.retake_exam:
        try:
            CONFIG["retake_exam"] = maybe_resolve_exam_arg(args.retake_exam, retake=True)
        except ExamCodeError as e:
            ts(f"[ERROR] {e}")
            sys.exit(1)
    if args.exam:
        try:
            CONFIG["exam"] = maybe_resolve_exam_arg(args.exam)
        except ExamCodeError as e:
            ts(f"[ERROR] {e}")
            sys.exit(1)

    _sync_globals_from_config()
    START_REGI = CONFIG["start_regi"]  # pyright: ignore[reportConstantRedefinition]
    END_REGI = CONFIG["end_regi"]  # pyright: ignore[reportConstantRedefinition]

    # Auto-select worksheet from exam title (unless user explicitly set --worksheet)
    if not RETAKE_MODE and not args.worksheet and CONFIG.get("exam"):
        resolved = _auto_resolve_worksheet(CONFIG["exam"])
        if resolved:
            WORKSHEET_NAME = resolved  # pyright: ignore[reportConstantRedefinition]

    if args.list_exams:
        if RETAKE_MODE:
            select_retake_exam(force=args.force)
        else:
            select_exam(force=args.force)
    elif args.validate:
        validate_config()
    elif args.show_config:
        show_config()
    elif args.status:
        show_status()
    elif args.all:
        run_batch(retake=RETAKE_MODE, dry_run=args.dry_run, reg_num=args.reg,
                  fresh=args.fresh, log=args.log)
    else:
        _ensure_sheet_configured()

        if RETAKE_MODE:
            if not CONFIG.get("retake_exam"):
                select_retake_exam(force=False)
                CONFIG = load_config()  # pyright: ignore[reportConstantRedefinition]
        else:
            if not CONFIG.get("exam"):
                select_exam(force=False)
                CONFIG = load_config()  # pyright: ignore[reportConstantRedefinition]

        if args.log:
            _setup_log_file()

        if args.fresh:
            if args.dry_run:
                ts("[INFO] Dry run — ignoring --fresh, progress file left untouched.")
            elif RETAKE_MODE:
                _save_retake_progress([])
                ts("[INFO] Retake progress cleared — starting fresh.")
            else:
                save_progress([])
                ts("[INFO] Progress cleared — starting fresh.")

        try:
            if RETAKE_MODE:
                scrape_retake_results(dry_run=args.dry_run, reg_num=args.reg)
            else:
                main(dry_run=args.dry_run, reg_num=args.reg)
        finally:
            if LOG_FILE:
                LOG_FILE.close()
