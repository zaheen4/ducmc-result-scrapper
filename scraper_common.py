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


# ===================================================================
# Module-Level State
# ===================================================================
DATA_DIR = None
CREDENTIALS_FILE = None
ENV_FILE = None
BROWSER = "firefox"
USE_INQUIRERPY = True
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
PROGRESS_FILE = None
LOG_FILE = None
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


# ===================================================================
# Configure
# ===================================================================
def configure(data_dir, credentials_file, env_file, browser="firefox", use_inquirerpy=True):
    """Set module-level state for the given environment.

    Call this once at startup before any other functions.
    """
    global DATA_DIR, CREDENTIALS_FILE, ENV_FILE, BROWSER, USE_INQUIRERPY
    global PROGRESS_FILE, CONFIG

    DATA_DIR = data_dir
    CREDENTIALS_FILE = credentials_file
    ENV_FILE = env_file
    BROWSER = browser
    USE_INQUIRERPY = use_inquirerpy

    os.makedirs(DATA_DIR, exist_ok=True)
    PROGRESS_FILE = os.path.join(DATA_DIR, 'progress.json')
    CONFIG = load_config()


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
        if gpa_match: student_data['GPA'] = gpa_match.group(1)
        cgpa_match = re.search(r"CGPA:\s*([\d.]+)", result_text)
        if cgpa_match: student_data['CGPA'] = cgpa_match.group(1)
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


# ===================================================================
# Config / IO Functions
# ===================================================================
def load_env():
    """Parse KEY=value pairs from .env file."""
    env = {}
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
    with open(ENV_FILE, 'w') as f:
        for key, value in env_dict.items():
            f.write(f'{key}={value}\n')


def save_config(config):
    """Write config dict back to config.json."""
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


def load_progress():
    """Load list of already-scraped reg numbers from progress.json."""
    if PROGRESS_FILE and os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
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
    data = {
        "config_key": _make_progress_key(),
        "scraped": scraped_list
    }
    with open(PROGRESS_FILE, 'w') as f:
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
    if USE_INQUIRERPY:
        try:
            from InquirerPy import prompt as inq_prompt
            inquirerpy_available = True
        except ImportError:
            pass

    if inquirerpy_available:
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
                "type": "number",
                "name": "start_regi",
                "message": "Start regi number:",
                "default": config.get("start_regi", 710),
                "float": False,
            },
            {
                "type": "number",
                "name": "end_regi",
                "message": "End regi number:",
                "default": config.get("end_regi", 813),
                "float": False,
            },
        ]
        answers = inq_prompt(questions)
        config["google_sheet_url"] = str(answers["google_sheet_url"])
        config["worksheet_name"] = str(answers["worksheet_name"])
        config["program"] = str(answers["program"])
        config["session"] = str(answers["session"])
        config["start_regi"] = int(answers["start_regi"])
        config["end_regi"] = int(answers["end_regi"])
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

    save_config(config)

    save_env({
        "GOOGLE_SHEET_URL": config["google_sheet_url"],
        "WORKSHEET_NAME": config["worksheet_name"],
        "PROGRAM": config["program"],
        "SESSION": config["session"],
    })
    ts(f"✅ Config saved.\n")


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

        exam_select = Select(wait.until(EC.presence_of_element_located((By.ID, 'exam_id'))))
        time.sleep(1)

        exam_options = [normalize_text(opt.text) for opt in exam_select.options if opt.text.strip()]

        if not exam_options:
            ts("No exams found for this program/session.")
            return

        ts(f"Found {len(exam_options)} exam(s) for \"{FORM_DATA['program']}\"")

        inquirerpy_available = False
        if USE_INQUIRERPY:
            try:
                from InquirerPy import prompt as inq_prompt
                inquirerpy_available = True
            except ImportError:
                pass

        if inquirerpy_available:
            questions = [
                {
                    "type": "fuzzy",
                    "name": "exam",
                    "message": "Select exam:",
                    "choices": exam_options,
                }
            ]
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

        current_config["exam"] = selected
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
    course_name_map = {sanitize_text(header.split('\n')[0].strip()): i for i, header in enumerate(sheet_headers) if header.strip()}

    has_course_columns = header_indices['retake'] > header_indices['gpa'] + 2

    return header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, has_course_columns, has_retake_column


def setup_course_columns(worksheet, courses, next_col_index, has_retake_column):
    """Write course name headers (row 1) and course code headers (row 2) to the sheet."""
    ts(f"Setting up {len(courses)} course columns in the sheet...")

    course_name_cells = []
    course_code_cells = []
    for i, course in enumerate(courses):
        col_num = next_col_index + i + 1
        col_letter = gspread.utils.rowcol_to_a1(1, col_num).rstrip('1')
        course_name_cells.append({'range': f'{col_letter}1', 'values': [[course['name']]]})
        course_code_cells.append({'range': f'{col_letter}2', 'values': [[course['code']]]})

    if not has_retake_column:
        retake_col_num = next_col_index + len(courses) + 1
        retake_col_letter = gspread.utils.rowcol_to_a1(1, retake_col_num).rstrip('1')
        course_name_cells.append({'range': f'{retake_col_letter}1', 'values': [['Retake Courses']]})
        course_code_cells.append({'range': f'{retake_col_letter}2', 'values': [['']]})

    worksheet.batch_update(course_name_cells + course_code_cells)
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
    worksheet.spreadsheet.batch_update({"requests": requests})
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
    if scraped_fail_subs and not existing_row_data[retake_col_index]:
        retake_col_letter = gspread.utils.rowcol_to_a1(1, retake_col_index + 1).rstrip('1')
        update_requests.append({'range': f'{retake_col_letter}{target_row_num}', 'values': [[scraped_fail_subs]]})

    for course in parsed_data.get('courses', []):
        sanitized_name = sanitize_text(course['name'])
        if sanitized_name in course_name_map:
            col_index = course_name_map[sanitized_name]
            if not existing_row_data[col_index]:
                col_letter = gspread.utils.rowcol_to_a1(1, col_index + 1).rstrip('1')
                update_requests.append({'range': f'{col_letter}{target_row_num}', 'values': [[course['grade']]]})

    if update_requests:
        worksheet.batch_update(update_requests)
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
            worksheet.batch_update(update_payload, value_input_option='USER_ENTERED')
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
            exam_select = Select(wait.until(EC.presence_of_element_located((By.ID, 'exam_id'))))
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


def main(dry_run=False, reg_num=None):
    """Main execution function to run the scraper and update the sheet."""
    start_time = time.time()

    if reg_num:
        start_regi = reg_num
        end_regi = reg_num
    else:
        start_regi = START_REGI
        end_regi = END_REGI

    if dry_run:
        ts("[DRY RUN] Scraping without writing to sheet.")

    worksheet = get_worksheet()
    if not worksheet:
        return

    header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, has_course_columns, has_retake_column = get_sheet_data(worksheet)
    if not header_indices:
        return

    scraped_list = load_progress()
    total = end_regi - start_regi + 1
    already_done = len([r for r in scraped_list if start_regi <= int(r) <= end_regi])
    remaining = total - already_done

    ts(f"\n--- Will scrape reg {start_regi}–{end_regi} ({total} students, {remaining} remaining) ---")
    ts(f"--- Sheet: '{WORKSHEET_NAME}' ---")
    if not dry_run:
        ts("--- Press Ctrl+C to cancel ---\n")

    driver = initialize_webdriver()

    stats = {"scraped": 0, "skipped": 0, "failed": 0}
    course_setup_done = has_course_columns
    interrupted = False

    try:
        for regi_num in range(start_regi, end_regi + 1):
            reg_str = str(regi_num)

            if reg_str in scraped_list:
                ts(f"[SKIP] Reg {reg_str} already scraped (from progress file)")
                stats["skipped"] += 1
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
                        course_setup_done = True

                    update_sheet_with_student_data(worksheet, parsed_data, header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values, header_indices['retake'])

                scraped_list.append(reg_str)
                save_progress(scraped_list)
                stats["scraped"] += 1
            else:
                stats["failed"] += 1

            if REQUEST_DELAY > 0 and regi_num < end_regi:
                time.sleep(REQUEST_DELAY)
    except KeyboardInterrupt:
        interrupted = True
        ts("\nInterrupted — saving progress...")
    finally:
        driver.quit()
        ts("WebDriver closed.")

    if not dry_run:
        format_gpa_cgpa_columns(worksheet)

    if not interrupted and stats['failed'] == 0:
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
    ts(f"  Reg range:       {START_REGI} – {END_REGI} ({END_REGI - START_REGI + 1} students)")
    ts(f"  Request delay:   {REQUEST_DELAY}s")
    ts(f"  Credentials:     {CREDENTIALS_FILE}")
    ts(f"  Progress file:   {PROGRESS_FILE}")
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
    ts(f"  Progress file:   {PROGRESS_FILE}")
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
                exam_select = Select(wait.until(EC.presence_of_element_located((By.ID, 'exam_id'))))
                time.sleep(1)
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
# Entry Point
# ===================================================================
def run():
    """CLI argument parsing and main logic. Call from entry point scripts."""
    global CONFIG, GOOGLE_SHEET_URL, WORKSHEET_NAME, FORM_DATA
    global START_REGI, END_REGI, REQUEST_DELAY, LOG_FILE

    import argparse

    parser = argparse.ArgumentParser(description="DUCMC Result Scraper")
    parser.add_argument("--list-exams", action="store_true", help="Interactively select an exam and update config.json")
    parser.add_argument("--force", action="store_true", help="Force re-select even if exam is already set")
    parser.add_argument("--fresh", action="store_true", help="Ignore progress.json and start from scratch")
    parser.add_argument("--dry-run", action="store_true", help="Scrape without writing to sheet")
    parser.add_argument("--log", action="store_true", help="Save output to a log file")
    parser.add_argument("--reg", type=int, help="Scrape a single reg number")
    parser.add_argument("--sheet-url", type=str, help="Google Sheet URL (overrides config)")
    parser.add_argument("--worksheet", type=str, help="Worksheet name (overrides config)")
    parser.add_argument("--program", type=str, help="Program name (overrides config)")
    parser.add_argument("--session", type=str, help="Session year (overrides config)")
    parser.add_argument("--start-regi", type=int, help="Starting reg number (overrides config)")
    parser.add_argument("--end-regi", type=int, help="Ending reg number (overrides config)")
    parser.add_argument("--validate", action="store_true", help="Test browser + sheet connection, then exit")
    parser.add_argument("--show-config", action="store_true", help="Print active config and exit")
    parser.add_argument("--status", action="store_true", help="Print progress status and exit")
    args = parser.parse_args()

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
        if not CONFIG.get("google_sheet_url") or not CONFIG.get("worksheet_name"):
            try:
                first_run_setup(CONFIG)
            except (KeyboardInterrupt, Exception):
                ts("\nSetup cancelled.")
                sys.exit(1)

        if not CONFIG.get("exam"):
            select_exam(force=False)
            CONFIG = load_config()

        if args.log:
            log_dir = os.path.join(DATA_DIR, 'logs')
            os.makedirs(log_dir, exist_ok=True)
            log_filename = os.path.join(log_dir, f"scraper_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")
            LOG_FILE = open(log_filename, 'w')
            ts(f"Logging to {log_filename}")

        if args.fresh:
            save_progress([])
            ts("[INFO] Progress cleared — starting fresh.")

        try:
            main(dry_run=args.dry_run, reg_num=args.reg)
        finally:
            if LOG_FILE:
                LOG_FILE.close()
