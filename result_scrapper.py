# ===================================================================
# Environment Detection & Setup
# ===================================================================
# This block determines if the script is running in Google Colab
# and installs the necessary dependencies at runtime.
import sys
import os
import subprocess

# Detects the execution environment by checking for Colab-specific modules.
IN_COLAB = 'google.colab' in sys.modules

LOG_FILE = None  # Set to file object when --log is used

def ts(msg):
    """Print a message with [HH:MM:SS] timestamp prefix, optionally to log file."""
    from datetime import datetime
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    if LOG_FILE:
        LOG_FILE.write(line + "\n")
        LOG_FILE.flush()

def setup_environment():
    """Installs dependencies if running in a Google Colab environment."""
    if IN_COLAB:
        ts("✅ Detected Google Colab environment. Installing dependencies...")

        # Install Chromium
        subprocess.run(["apt-get", "update"], check=True)
        subprocess.run(["apt-get", "install", "-y", "chromium-browser"], check=True)

        subprocess.run(["pip", "install", "selenium==4.25.0", "gspread==5.12.4", "beautifulsoup4==4.12.3", "webdriver-manager==4.0.1", "InquirerPy==0.3.4"], check=True)

        ts("✅ Dependencies installed.")
    else:
        ts("✅ Detected local environment. Assuming dependencies are pre-installed.")

setup_environment()


# ===================================================================
# Main Script Imports
# ===================================================================
import time
import json
import html as html_module
import gspread
import re
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# Conditionally import the correct WebDriver service for the environment.
# webdriver-manager is used to handle automatic driver installation.
try:
    if IN_COLAB:
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service as ChromeService
        from webdriver_manager.chrome import ChromeDriverManager
    else:
        # For local execution, the script defaults to Firefox.
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service as FirefoxService
        from webdriver_manager.firefox import GeckoDriverManager
except ImportError:
    ts("\n--- Missing Dependency ---")
    ts("This script requires the 'webdriver-manager' library.")
    ts("Please install it by running: pip install webdriver-manager")
    sys.exit(1)


# ===================================================================
# Configuration
# ===================================================================
# Loads settings from config.json. Falls back to hardcoded defaults if missing.

if IN_COLAB:
    DATA_DIR = '/content/drive/MyDrive/ResultScraperData'
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULTS = {
    "google_sheet_url": "",
    "worksheet_name": "",
    "program": "B.Sc. in Computer Science and Engineering",
    "session": "2021-2022",
    "exam": "",
    "start_regi": 710,
    "end_regi": 813,
    "request_delay": 1
}

# .env file for persistent secrets/config (not tracked by git)
if IN_COLAB:
    ENV_FILE = os.path.join(DATA_DIR, '.env')
else:
    ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

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
    config_path = os.path.join(DATA_DIR, 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            user_config = json.load(f)
        config = {k: user_config.get(k, v) for k, v in DEFAULTS.items()}
        ts(f"[INFO] Loaded config from {config_path}")
    else:
        config = dict(DEFAULTS)
        save_config(config)
        ts(f"[INFO] Created default config at {config_path}")
    return config

def first_run_setup(config):
    """Prompt user for critical config values on first run."""
    try:
        from InquirerPy import prompt
    except ImportError:
        ts("\n--- Missing Dependency ---")
        ts("InquirerPy is required for interactive setup.")
        ts("Install it: pip install InquirerPy")
        sys.exit(1)

    # Load .env for pre-filling prompts
    env = load_env()
    env_hint = " (loaded from .env)" if env else ""

    ts(f"\nFirst run detected — let's set up config.json.{env_hint}\n")

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
        },
        {
            "type": "number",
            "name": "end_regi",
            "message": "End regi number:",
            "default": config.get("end_regi", 813),
        },
    ]

    answers = prompt(questions)
    if answers:
        config["google_sheet_url"] = answers.get("google_sheet_url", config["google_sheet_url"])
        config["worksheet_name"] = answers.get("worksheet_name", config["worksheet_name"])
        config["program"] = answers.get("program", config["program"])
        config["session"] = answers.get("session", config["session"])
        config["start_regi"] = int(answers.get("start_regi", config["start_regi"]))
        config["end_regi"] = int(answers.get("end_regi", config["end_regi"]))
        save_config(config)

        # Persist key values to .env for next time
        save_env({
            "GOOGLE_SHEET_URL": config["google_sheet_url"],
            "WORKSHEET_NAME": config["worksheet_name"],
            "PROGRAM": config["program"],
            "SESSION": config["session"],
        })
        ts(f"✅ Config saved.\n")

CONFIG = load_config()
GOOGLE_SHEET_URL = CONFIG["google_sheet_url"]
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file'
]
WORKSHEET_NAME = CONFIG["worksheet_name"]
FORM_DATA = {
    "program": CONFIG["program"],
    "session": CONFIG["session"],
    "exam": CONFIG["exam"]
}
URL = 'https://ducmc.du.ac.bd/result.php'
START_REGI = CONFIG["start_regi"]
END_REGI = CONFIG["end_regi"]
REQUEST_DELAY = CONFIG["request_delay"]
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 2  # seconds, doubles each retry
PROGRESS_FILE = os.path.join(DATA_DIR, 'progress.json')

# --- Environment-Specific Settings ---
if IN_COLAB:
    from google.colab import drive # type: ignore
    ts("Mounting Google Drive to access credentials...")
    drive.mount('/content/drive', force_remount=True)
    # Defines the path for the credentials file stored in Google Drive.
    CREDENTIALS_FILE = os.path.join(DATA_DIR, 'credentials.json')
    if not os.path.exists(CREDENTIALS_FILE):
        ts(f"[ERROR] credentials.json not found at {CREDENTIALS_FILE}")
        ts("Please place it in Google Drive → ResultScraperData/")
        sys.exit(1)
else:
    # Defines the path for the credentials file on a local machine,
    # resolved relative to the script directory (not CWD).
    CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'credentials.json')


def _make_progress_key():
    """Create a deterministic key from the current config to scope progress."""
    import hashlib
    key_parts = f"{GOOGLE_SHEET_URL}|{WORKSHEET_NAME}|{FORM_DATA['exam']}"
    return hashlib.md5(key_parts.encode()).hexdigest()[:12]

def load_progress():
    """Load list of already-scraped reg numbers from progress.json.

    Progress is scoped by (sheet_url, worksheet, exam). If the config
    changes (e.g. different exam), stale progress is ignored.
    """
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            data = json.load(f)
        # New format: dict with config_key and scraped list
        if isinstance(data, dict):
            current_key = _make_progress_key()
            if data.get("config_key") == current_key:
                scraped = data.get("scraped", [])
                ts(f"[INFO] Loaded progress: {len(scraped)} students already scraped")
                return scraped
            else:
                ts("[INFO] Progress file exists but config changed — starting fresh")
                return []
        # Legacy format: flat list (backward compatible)
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
    """
    Parses the HTML content of a student's result page to extract key information.

    Args:
        html_content (str): The raw HTML source of the result page.

    Returns:
        dict: A dictionary containing the student's name, registration, GPA, CGPA,
              failed subjects, and a list of courses with grades.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    student_data = {}

    # Extract basic student information (Name, Registration Number).
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

    # Locate the result summary section to extract GPA, CGPA, and failed subjects.
    result_div = soup.find('div', style=lambda v: v and 'text-align: center' in v)

    # Initialize result fields to ensure they exist in the final dictionary.
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
# Core Logic Functions
# ===================================================================

def get_worksheet():
    """Authenticates with Google Sheets and returns the worksheet object."""
    ts("Authenticating with Google Sheets...")
    if not IN_COLAB and not os.path.exists(CREDENTIALS_FILE):
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
    """Write course name headers (row 1) and course code headers (row 2) to the sheet.

    Courses are inserted at next_col_index. If Retake Courses column is missing,
    it's created after all course columns.
    """
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
    # Course columns (150px)
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
    # Retake column (300px)
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

def initialize_webdriver():
    """Initializes and returns the Selenium WebDriver."""
    options = Options()
    options.add_argument("--headless")
    if IN_COLAB:
        ts("WebDriver: Initializing Chrome for Colab.")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = webdriver.Chrome(options=options)
    else:
        ts("WebDriver: Initializing Firefox for local execution.")
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
# Main Execution Logic
# ===================================================================
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
# Exam Selector (--list-exams)
# ===================================================================
def select_exam(force=False):
    """Interactive exam selector using InquirerPy fuzzy search."""
    try:
        from InquirerPy import prompt
    except ImportError:
        ts("\n--- Missing Dependency ---")
        ts("InquirerPy is required for the exam selector.")
        ts("Install it: pip install InquirerPy")
        sys.exit(1)

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

        questions = [{
            "type": "fuzzy",
            "name": "exam",
            "message": "Select exam:",
            "choices": exam_options,
        }]
        result = prompt(questions)

        if result and result.get("exam"):
            selected = result["exam"]
            current_config["exam"] = selected
            save_config(current_config)
            ts(f"✅ Updated config.json with exam: \"{selected}\"")
    finally:
        driver.quit()


# ===================================================================
# Pre-flight Validation (--validate)
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

    # 1. Test Google Sheets connection
    ts("[1/3] Testing Google Sheets connection...")
    worksheet = get_worksheet()
    if worksheet:
        ts("  ✅ Sheet connected successfully")
    else:
        errors.append("Google Sheets connection failed")

    # 2. Test browser + portal dropdowns
    ts("[2/3] Testing browser and portal dropdowns...")
    driver = None
    try:
        driver = initialize_webdriver()
        driver.get(URL)
        wait = WebDriverWait(driver, 15)

        # Test program dropdown
        try:
            Select(wait.until(EC.presence_of_element_located((By.ID, 'pro_id')))).select_by_visible_text(FORM_DATA['program'])
            ts(f"  ✅ Program: \"{FORM_DATA['program']}\"")
        except (NoSuchElementException, TimeoutException) as e:
            ts(f"  ❌ Program not found: \"{FORM_DATA['program']}\"")
            errors.append(f"Program dropdown: {e}")

        # Test session dropdown
        try:
            Select(wait.until(EC.presence_of_element_located((By.ID, 'sess_id')))).select_by_visible_text(FORM_DATA['session'])
            ts(f"  ✅ Session: \"{FORM_DATA['session']}\"")
        except (NoSuchElementException, TimeoutException) as e:
            ts(f"  ❌ Session not found: \"{FORM_DATA['session']}\"")
            errors.append(f"Session dropdown: {e}")

        # Test exam matching
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

    # 3. Test a single student scrape
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

    # Summary
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
# Script Entry Point
# ===================================================================
if __name__ == '__main__':
    import argparse
    from datetime import datetime

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

    # Rebuild module-level constants from potentially-overridden config
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
    elif IN_COLAB or __name__ == '__main__':
        # First-run setup: prompt for config values if needed
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
