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

def setup_environment():
    """Installs dependencies if running in a Google Colab environment."""
    if IN_COLAB:
        print("✅ Detected Google Colab environment. Installing dependencies...")
        
        # Install Chromium
        subprocess.run(["apt-get", "update"], check=True)
        subprocess.run(["apt-get", "install", "-y", "chromium-browser"], check=True)

        subprocess.run(["pip", "install", "selenium", "gspread==5.12.4", "oauth2client==4.1.3", "beautifulsoup4==4.12.3", "webdriver-manager==4.0.1"], check=True)

        print("✅ Dependencies installed.")
    else:
        print("✅ Detected local environment. Assuming dependencies are pre-installed.")

setup_environment()


# ===================================================================
# Main Script Imports
# ===================================================================
import time
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
    print("\n--- Missing Dependency ---")
    print("This script requires the 'webdriver-manager' library.")
    print("Please install it by running: pip install webdriver-manager")
    sys.exit(1)


# ===================================================================
# Configuration
# ===================================================================
# This section defines universal settings and then selects specific
# configurations based on the detected environment.

# --- Universal Settings ---
GOOGLE_SHEET_URL = 'https://docs.google.com/spreadsheets/d/1aZWYV-V_YjdA_QRLNv5-vDx9YEWdaL2Is9RbK4ObLzw/edit?usp=drive_link'
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file'
]

# WORKSHEET_NAME = 'PerCourse_L1T1'
# WORKSHEET_NAME = 'PerCourse_L1T2'
# WORKSHEET_NAME = 'PerCourse_L2T1'
# WORKSHEET_NAME = 'PerCourse_L2T2'
# WORKSHEET_NAME = 'PerCourse_L3T1'
# WORKSHEET_NAME = 'PerCourse_L3T2'
WORKSHEET_NAME = 'Copy of PerCourse_L3T1'

FORM_DATA = {
    "program": "B.Sc. in Computer Science and Engineering",
    "session": "2020-2021",
    # "exam": "B.Sc. in Computer Science and Engineering 1st year 1st Semester Examination of 2022"
    # "exam": "B.Sc. in Computer Science and Engineering 1st year 2nd Semester Examination of 2022"
    # "exam": "B.Sc. in Computer Science and Engineering 2nd year 1st Semester Examination of 2023"
    # "exam": "B.Sc. in Computer Science and Engineering 2nd year 2nd Semester Examination of 2023"
    # "exam": "B.Sc. in Computer Science and Engineering 3rd year 1st Semester Examination of 2024"

    # "exam": "B.Sc. in Computer Science and Engineering 3rd year 1st Semester Examination of 2023 (New Curriculum)"
}
URL = 'https://ducmc.du.ac.bd/result.php'
START_REGI = 710    #442
END_REGI = 813      #508

# --- Environment-Specific Settings ---
if IN_COLAB:
    from google.colab import drive # type: ignore
    print("Mounting Google Drive to access credentials...")
    drive.mount('/content/drive', force_remount=True)
    # Defines the path for the credentials file stored in Google Drive.
    CREDENTIALS_FILE = '/content/drive/My Drive/ColabScripts/credentials.json'
else:
    # Defines the path for the credentials file on a local machine,
    # expecting it to be in the same directory as the script.
    CREDENTIALS_FILE = 'credentials.json'


# ===================================================================
# Helper Functions
# ===================================================================
def sanitize_text(text):
    """Cleans and standardizes text for reliable matching."""
    return re.sub(r'[^a-z0-9]', '', text.lower())

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
                    "grade": cols[4].get_text(strip=True) or '0.00'
                })
    return student_data

# ===================================================================
# Core Logic Functions
# ===================================================================

def get_worksheet():
    """Authenticates with Google Sheets and returns the worksheet object."""
    print("Authenticating with Google Sheets...")
    if not IN_COLAB and not os.path.exists(CREDENTIALS_FILE):
        print(f"FATAL ERROR: The credentials file ('{CREDENTIALS_FILE}') was not found in this directory.")
        return None

    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(GOOGLE_SHEET_URL)
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        print(f"✅ Successfully connected to '{spreadsheet.title}' and selected worksheet '{worksheet.title}'.")
        return worksheet
    except Exception as e:
        print(f"FATAL ERROR: Could not connect to Google Sheets. {e}")
        return None

def get_sheet_data(worksheet):
    """Fetches and processes data from the worksheet."""
    print("Fetching sheet data for comparison...")
    all_sheet_values = worksheet.get_all_values()
    sheet_headers = all_sheet_values[0]

    try:
        header_indices = {
            "name": sheet_headers.index("Student's Name"),
            "roll": sheet_headers.index("Student's ID"),
            "reg": sheet_headers.index('Reg. No.'),
            "gpa": sheet_headers.index('GPA'),
            "cgpa": sheet_headers.index('CGPA'),
            "retake": sheet_headers.index('Retake Courses')
        }
    except ValueError as e:
        print(f"FATAL ERROR: A required column header was not found in Row 1 of your sheet: {e}")
        return None, None, None, None

    all_reg_numbers_in_sheet = [row[header_indices["reg"]] for row in all_sheet_values[1:]]
    course_name_map = {sanitize_text(header.split('\n')[0].strip()): i for i, header in enumerate(sheet_headers) if header.strip()}
    
    return header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values

def initialize_webdriver():
    """Initializes and returns the Selenium WebDriver."""
    options = Options()
    options.add_argument("--headless")
    if IN_COLAB:
        print("WebDriver: Initializing Chrome for Colab.")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = webdriver.Chrome(options=options)
    else:
        print("WebDriver: Initializing Firefox for local execution.")
        driver = webdriver.Firefox(options=options)
    return driver

def scrape_student_result(driver, reg_num):
    """Scrapes the result for a single student."""
    print(f"\n--- Processing Registration No.: {reg_num} ---")
    try:
        driver.get(URL)
        wait = WebDriverWait(driver, 15)
        
        Select(wait.until(EC.presence_of_element_located((By.ID, 'pro_id')))).select_by_visible_text(FORM_DATA['program'])
        Select(wait.until(EC.presence_of_element_located((By.ID, 'sess_id')))).select_by_visible_text(FORM_DATA['session'])
        
        exam_xpath = f"//select[@id='exam_id']/option[text()='{FORM_DATA['exam']}']"
        wait.until(EC.element_to_be_clickable((By.XPATH, exam_xpath)))
        Select(driver.find_element(By.ID, 'exam_id')).select_by_visible_text(FORM_DATA['exam'])
        
        driver.find_element(By.ID, 'reg_no').send_keys(reg_num)
        driver.find_element(By.XPATH, "//button[text()='Submit']").click()
        
        wait.until(EC.presence_of_element_located((By.XPATH, "//h3[contains(text(), 'Result')]")))
        time.sleep(0.5)

        parsed_data = parse_result_html(driver.page_source)

        if not parsed_data or 'Reg' not in parsed_data:
            print(f"Result not found or page is invalid for Reg No: {reg_num}.")
            return None

        print(f"Found result for Reg No: {parsed_data['Reg']} (Name: {parsed_data.get('Name', 'N/A')})")
        return parsed_data

    except TimeoutException:
        print(f"No result found for {reg_num} (timeout).")
        return None
    except Exception as e:
        print(f"An unexpected error occurred for {reg_num}: {e}")
        return None

def update_sheet_with_student_data(worksheet, parsed_data, header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values):
    """Updates the Google Sheet with the scraped student data."""
    scraped_reg = parsed_data['Reg']
    try:
        target_list_index = all_reg_numbers_in_sheet.index(scraped_reg)
        target_row_num = target_list_index + 2
        existing_row_data = all_sheet_values[target_list_index + 1]
    except ValueError:
        print(f"Could not find registration '{scraped_reg}' in the sheet. Skipping.")
        return

    print(f"Found on row {target_row_num}. Checking for empty cells...")
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
    if scraped_fail_subs and not existing_row_data[header_indices['retake']]:
        retake_col_letter = gspread.utils.rowcol_to_a1(1, header_indices['retake'] + 1).rstrip('1')
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
        print(f"✅ Successfully wrote {len(update_requests)} new value(s) to the Google Sheet.")
    else:
        print("No empty cells to update. Data is already present.")

def format_gpa_cgpa_columns(worksheet):
    """Converts and formats GPA/CGPA columns in the sheet."""
    print("\n--- Converting and formatting GPA/CGPA columns... ---")
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
            print("Step 1/2: Successfully converted any existing text values to numbers.")

        worksheet.format(ranges_to_process, {
            "numberFormat": {
                "type": "NUMBER",
                "pattern": "0.00"
            }
        })
        print("Step 2/2: ✅ Successfully applied number formatting to GPA and CGPA columns.")
    except Exception as e:
        print(f"⚠️ Could not apply formatting. Error: {e}")


# ===================================================================
# Main Execution Logic
# ===================================================================
def main():
    """Main execution function to run the scraper and update the sheet."""
    worksheet = get_worksheet()
    if not worksheet:
        return

    header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values = get_sheet_data(worksheet)
    if not header_indices:
        return

    driver = initialize_webdriver()

    try:
        for regi_num in range(START_REGI, END_REGI + 1):
            parsed_data = scrape_student_result(driver, str(regi_num))
            if parsed_data:
                update_sheet_with_student_data(worksheet, parsed_data, header_indices, all_reg_numbers_in_sheet, course_name_map, all_sheet_values)
    finally:
        driver.quit()
        print("\n--- All registration numbers processed. WebDriver closed. ---")

    format_gpa_cgpa_columns(worksheet)


# ===================================================================
# Script Entry Point
# ===================================================================
if __name__ == '__main__':
    # This check allows the script to be run directly. In Colab, a single
    # cell execution context makes this block run automatically.
    if IN_COLAB or __name__ == '__main__':
        main()