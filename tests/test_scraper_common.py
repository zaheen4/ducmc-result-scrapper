"""Tests for scraper_common.py — pure functions, config/progress, sheet ops, selenium."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import scraper_common

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scraper_common.configure(
    data_dir=os.path.join(SCRIPT_DIR, "data"),
    credentials_file=os.path.join(SCRIPT_DIR, "credentials.json"),
    env_file=os.path.join(SCRIPT_DIR, ".env"),
    browser="firefox",
)

from scraper_common import (  # noqa: E402
    sanitize_text,
    normalize_text,
    format_roll_number,
    parse_result_html,
    load_config,
    save_config,
    load_progress,
    save_progress,
    get_sheet_data,
    setup_course_columns,
    set_column_widths,
    update_sheet_with_student_data,
    format_gpa_cgpa_columns,
    scrape_student_result,
    select_exam,
    ts,
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def sample_result_html():
    """Synthetic result HTML matching parse_result_html's expected structure."""
    return """
    <div id="exam_result">
      <div class="row">
        <div class="col-12">
          <table class="table-bordered">
            <tbody>
              <tr><th>College Name</th><td>Test University</td></tr>
              <tr><th>Student's Name</th><td>Jane Doe</td></tr>
              <tr><th>Registration</th><td>CS 1234567</td></tr>
              <tr><th>Session</th><td>2020-2021</td></tr>
              <tr><th>Program</th><td>B.Sc. in CSE</td></tr>
              <tr><th>Exam Roll</th><td>123</td></tr>
              <tr><th>Class Roll</th><td>CS-1234567</td></tr>
              <tr><th>Exam Year</th><td>2024</td></tr>
              <tr><th>Result Publication Date</th><td>2024-01-01</td></tr>
              <tr><th>
                <table width="100%">
                  <tr><th>#</th><th>Code</th><th>Name</th><th>Grade</th><th>GPA</th></tr>
                  <tr><td>1</td><td>CSE-3101</td><td>Computer Networking</td><td>A-</td><td>3.75</td></tr>
                  <tr><td>2</td><td>CSE-3102</td><td>Software Engineering</td><td>B+</td><td>3.25</td></tr>
                  <tr><td>3</td><td>CSE-3116</td><td>Microcontroller Lab</td><td>A+</td><td>4.00</td></tr>
                </table>
              </th></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div style="text-align: center; font-weight: bold; font-size: 25px;">
        Passed<small> GPA: 3.67</small> <small>CGPA: 3.67</small>
      </div>
    </div>
    """


@pytest.fixture
def sample_sheet_values():
    """Synthetic sheet data: headers + 2 sample rows."""
    return [
        [
            "Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA", "",
            "Computer Networking", "Software Engineering",
            "Microprocessor and Microcontroller", "Database Management Systems-II",
            "Multivariable Calculus and Geometry", "Computer Networking Lab",
            "Software Engineering Lab", "Microprocessor and Assembly Language Lab",
            "Microcontroller Lab", "Retake Courses",
        ],
        [
            "", "", "", "", "", "", "",
            "CSE-3101", "CSE-3102", "CSE-3103", "CSE-3104", "MATH-3105",
            "CSE-3111", "CSE-3112", "CSE-3113", "CSE-3116", "",
        ],
        [
            "41", "John Smith", "CS 1234568", "101", "2.31", "2.55", "",
            "3.00", "2.75", "2.00", "2.00", "0.00", "3.50", "3.75", "3.25", "3.75",
            "MATH-3105",
        ],
        [
            "101", "Jane Doe", "CS 1234567", "123", "3.42", "3.32", "",
            "3.75", "3.25", "3.50", "3.50", "2.25", "4.00", "4.00", "4.00", "4.00", "",
        ],
    ]


@pytest.fixture
def mock_worksheet(sample_sheet_values):
    ws = MagicMock()
    ws.get_all_values.return_value = sample_sheet_values
    return ws


# ===================================================================
# 1. Pure Functions
# ===================================================================

class TestSanitizeText:
    def test_normal_text(self):
        assert sanitize_text("Computer Networking") == "computernetworking"

    def test_special_chars(self):
        assert sanitize_text("DBMS-II") == "dbmsii"

    def test_mixed_case(self):
        assert sanitize_text("CSE-3101") == "cse3101"

    def test_empty_string(self):
        assert sanitize_text("") == ""

    def test_spaces_and_punctuation(self):
        assert sanitize_text("Student's Name") == "studentsname"

    def testCourse_names_from_sheet(self):
        assert sanitize_text("Database Management Systems-II") == "databasemanagementsystemsii"
        assert sanitize_text("Microprocessor and Assembly Language Lab") == "microprocessorandassemblylanguagelab"
        assert sanitize_text("Multivariable Calculus and Geometry") == "multivariablecalculusandgeometry"


class TestNormalizeText:
    def test_html_entities(self):
        assert normalize_text("&amp;") == "&"

    def test_nbsp(self):
        assert normalize_text("hello&nbsp;world") == "hello world"

    def test_whitespace_collapse(self):
        assert normalize_text("  hello   world  ") == "hello world"

    def test_newlines(self):
        assert normalize_text("line1\nline2\nline3") == "line1 line2 line3"

    def test_tabs(self):
        assert normalize_text("col1\tcol2") == "col1 col2"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_real_exam_text(self):
        raw = "B.Sc. in Computer Science and Engineering\u00a03rd year 1st Semester\u00a0Examination of 2023 (New Curriculum)"
        result = normalize_text(raw)
        assert "\u00a0" not in result
        assert "  " not in result


class TestFormatRollNumber:
    def test_normal_roll(self):
        assert format_roll_number("CS 1234567") == "CS 1234567"

    def test_no_space(self):
        assert format_roll_number("CS1234567") == "CS 1234567"

    def test_lowercase(self):
        assert format_roll_number("cs 1234567") == "CS 1234567"

    def test_empty_string(self):
        assert format_roll_number("") == ""

    def test_none(self):
        assert format_roll_number(None) == ""

    def test_numbers_only(self):
        assert format_roll_number("1234567") == "1234567"

    def test_letters_only(self):
        assert format_roll_number("CS") == "CS"


class TestParseResultHtml:
    def test_extracts_student_info(self, sample_result_html):
        result = parse_result_html(sample_result_html)
        assert result['Name'] == 'Jane Doe'
        assert result['Roll'] == 'CS-1234567'
        assert result['Reg'] == 'CS 1234567'

    def test_extracts_gpa_cgpa(self, sample_result_html):
        result = parse_result_html(sample_result_html)
        assert result['GPA'] == '3.67'
        assert result['CGPA'] == '3.67'

    def test_extracts_courses(self, sample_result_html):
        result = parse_result_html(sample_result_html)
        assert len(result['courses']) == 3
        names = [c['name'] for c in result['courses']]
        assert 'Computer Networking' in names
        assert 'Microcontroller Lab' in names

    def test_extracts_grades(self, sample_result_html):
        result = parse_result_html(sample_result_html)
        grades = {c['name']: c['grade'] for c in result['courses']}
        assert grades['Computer Networking'] == '3.75'
        assert grades['Software Engineering'] == '3.25'
        assert grades['Microcontroller Lab'] == '4.00'

    def test_empty_html(self):
        result = parse_result_html("<html><body></body></html>")
        assert result.get('Name') is None
        assert result.get('GPA') == ''
        assert result.get('CGPA') == ''
        assert result['courses'] == []

    def test_malformed_html(self):
        result = parse_result_html("not html at all")
        assert result['courses'] == []
        assert result['GPA'] == ''

    def test_extracts_course_codes(self, sample_result_html):
        result = parse_result_html(sample_result_html)
        codes = {c['name']: c['code'] for c in result['courses']}
        assert codes['Computer Networking'] == 'CSE-3101'
        assert codes['Software Engineering'] == 'CSE-3102'
        assert codes['Microcontroller Lab'] == 'CSE-3116'


class TestSetupCourseColumns:
    def test_writes_course_names_and_codes(self):
        ws = MagicMock()
        courses = [
            {'name': 'Computer Networking', 'code': 'CSE-3101', 'grade': '3.75'},
            {'name': 'Software Engineering', 'code': 'CSE-3102', 'grade': '3.25'},
        ]
        setup_course_columns(ws, courses, next_col_index=7, has_retake_column=True)
        ws.batch_update.assert_called_once()
        calls = ws.batch_update.call_args[0][0]
        ranges = {c['range']: c['values'][0][0] for c in calls}
        assert 'H1' in ranges
        assert ranges['H1'] == 'Computer Networking'
        assert 'I1' in ranges
        assert ranges['I1'] == 'Software Engineering'
        assert 'H2' in ranges
        assert ranges['H2'] == 'CSE-3101'
        assert 'I2' in ranges
        assert ranges['I2'] == 'CSE-3102'
        assert len(calls) == 4

    def test_creates_retake_column_when_missing(self):
        ws = MagicMock()
        courses = [
            {'name': 'Computer Networking', 'code': 'CSE-3101', 'grade': '3.75'},
        ]
        setup_course_columns(ws, courses, next_col_index=6, has_retake_column=False)
        calls = ws.batch_update.call_args[0][0]
        ranges = {c['range']: c['values'][0][0] for c in calls}
        assert 'G1' in ranges
        assert ranges['G1'] == 'Computer Networking'
        assert 'H1' in ranges
        assert ranges['H1'] == 'Retake Courses'


class TestSetColumnWidths:
    def test_sets_widths_for_courses_and_retake(self):
        ws = MagicMock()
        ws.id = 0
        ws.spreadsheet = MagicMock()
        set_column_widths(ws, course_count=2, next_col_index=7)
        ws.spreadsheet.batch_update.assert_called_once()
        payload = ws.spreadsheet.batch_update.call_args[0][0]
        requests = payload['requests']
        assert len(requests) == 3
        assert requests[0]['updateDimensionProperties']['properties']['pixelSize'] == 150
        assert requests[0]['updateDimensionProperties']['range']['startIndex'] == 7
        assert requests[1]['updateDimensionProperties']['properties']['pixelSize'] == 150
        assert requests[1]['updateDimensionProperties']['range']['startIndex'] == 8
        assert requests[2]['updateDimensionProperties']['properties']['pixelSize'] == 300
        assert requests[2]['updateDimensionProperties']['range']['startIndex'] == 9


# ===================================================================
# 2. Config / Progress (tmp_path)
# ===================================================================

class TestConfig:
    def test_load_config_defaults(self, tmp_path):
        with patch('scraper_common.os.path.exists', return_value=False), \
             patch('scraper_common.DATA_DIR', str(tmp_path)):
            config = load_config()
        assert config['start_regi'] == 710
        assert config['end_regi'] == 813
        assert config['session'] == '2021-2022'
        assert config['exam'] == ''

    def test_load_config_from_file(self, tmp_path):
        cfg = {"start_regi": 100, "end_regi": 200, "session": "2019-2020"}
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps(cfg))
        with patch('scraper_common.DATA_DIR', str(tmp_path)):
            config = load_config()
        assert config['start_regi'] == 100
        assert config['end_regi'] == 200
        assert config['session'] == '2019-2020'

    def test_load_config_partial_merges_defaults(self):
        cfg = {"exam": "Some Exam"}
        with patch('scraper_common.os.path.exists', return_value=True), \
             patch('builtins.open', create=True) as mock_open:
            import io
            mock_open.return_value = io.StringIO(json.dumps(cfg))
            config = load_config()
        assert config['exam'] == 'Some Exam'
        assert config['start_regi'] == 710
        assert config['session'] == '2021-2022'


class TestSaveConfig:
    def test_save_config_writes_file(self, tmp_path):
        config = {"exam": "Test Exam", "start_regi": 100}
        with patch('scraper_common.DATA_DIR', str(tmp_path)):
            save_config(config)
        config_path = tmp_path / "config.json"
        assert config_path.exists()
        with open(config_path) as f:
            saved = json.load(f)
        assert saved["exam"] == "Test Exam"
        assert saved["start_regi"] == 100


class TestProgress:
    def test_save_and_load_roundtrip(self, tmp_path):
        progress_file = tmp_path / "progress.json"
        with patch('scraper_common.PROGRESS_FILE', str(progress_file)):
            save_progress(["100", "101", "102"])
            loaded = load_progress()
        assert loaded == ["100", "101", "102"]

    def test_load_progress_no_file(self, tmp_path):
        progress_file = tmp_path / "nonexistent.json"
        with patch('scraper_common.PROGRESS_FILE', str(progress_file)):
            loaded = load_progress()
        assert loaded == []

    def test_save_progress_overwrites(self, tmp_path):
        progress_file = tmp_path / "progress.json"
        with patch('scraper_common.PROGRESS_FILE', str(progress_file)):
            save_progress(["100"])
            save_progress(["100", "101"])
            loaded = load_progress()
        assert loaded == ["100", "101"]


# ===================================================================
# 3. Sheet Operations (mocked)
# ===================================================================

class TestGetSheetData:
    def test_returns_correct_indices(self, mock_worksheet):
        header_indices, regs, course_map, values, has_courses, has_retake = get_sheet_data(mock_worksheet)
        assert header_indices is not None
        assert header_indices['name'] == 1
        assert header_indices['roll'] == 2
        assert header_indices['reg'] == 3
        assert header_indices['gpa'] == 4
        assert header_indices['cgpa'] == 5
        assert header_indices['retake'] == 16
        assert has_courses is True
        assert has_retake is True

    def test_returns_reg_numbers(self, mock_worksheet):
        _, regs, _, _, _, _ = get_sheet_data(mock_worksheet)
        assert regs is not None
        assert '101' in regs
        assert '123' in regs

    def test_course_name_map_matches(self, mock_worksheet):
        _, _, course_map, _, _, _ = get_sheet_data(mock_worksheet)
        assert course_map is not None
        assert 'computernetworking' in course_map
        assert 'softwareengineering' in course_map
        assert 'microprocessorandmicrocontroller' in course_map
        assert course_map['computernetworking'] == 7

    def test_no_course_columns_detected(self):
        ws = MagicMock()
        ws.get_all_values.return_value = [
            ["Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA", "Retake Courses"],
            [""] * 7,
            ["1", "Test", "CS 001", "710", "", "", ""],
        ]
        _, _, _, _, has_courses, _ = get_sheet_data(ws)
        assert has_courses is False

    def test_no_retake_column_detected(self):
        ws = MagicMock()
        ws.get_all_values.return_value = [
            ["Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA"],
            [""] * 6,
            ["1", "Test", "CS 001", "710", "", ""],
        ]
        _, _, _, _, has_courses, has_retake = get_sheet_data(ws)
        assert has_retake is False
        assert has_courses is False

    def test_missing_header_returns_none(self):
        ws = MagicMock()
        ws.get_all_values.return_value = [
            ["Wrong", "Headers", "Here"],
            ["", "", ""],
        ]
        result = get_sheet_data(ws)
        assert result == (None, None, None, None, None, None)


class TestUpdateSheet:
    def _make_sheet_with_empty_row(self):
        """Create a sheet where reg 101 exists but has empty GPA/CGPA/course cells."""
        ws = MagicMock()
        ws.get_all_values.return_value = [
            [
                "Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA", "",
                "Computer Networking", "Software Engineering",
                "Microprocessor and Microcontroller", "Database Management Systems-II",
                "Multivariable Calculus and Geometry", "Computer Networking Lab",
                "Software Engineering Lab", "Microprocessor and Assembly Language Lab",
                "Microcontroller Lab", "Retake Courses",
            ],
            [""] * 17,
            [
                "1", "", "", "101", "", "", "",
                "", "", "", "", "", "", "", "", "", "",
            ],
        ]
        return ws

    def test_writes_to_empty_cells(self):
        ws = self._make_sheet_with_empty_row()
        parsed = {
            'Name': 'Test Student',
            'Roll': 'CS 1234569',
            'Reg': '101',
            'GPA': '3.00',
            'CGPA': '3.10',
            'Fail Subs': '',
            'courses': [
                {'name': 'Computer Networking', 'grade': '3.50'},
                {'name': 'Software Engineering', 'grade': '3.75'},
            ],
        }
        header_indices, regs, course_map, values, _, _ = get_sheet_data(ws)
        assert header_indices is not None
        update_sheet_with_student_data(
            ws, parsed, header_indices, regs, course_map, values, header_indices['retake']
        )
        ws.batch_update.assert_called_once()
        calls = ws.batch_update.call_args[0][0]
        ranges_written = [c['range'] for c in calls]
        assert 'B3' in ranges_written
        assert 'C3' in ranges_written
        assert 'E3' in ranges_written
        assert 'F3' in ranges_written

    def test_skips_filled_cells(self, mock_worksheet):
        parsed = {
            'Name': 'Jane Doe',
            'Roll': 'CS 1234567',
            'Reg': '123',
            'GPA': '3.42',
            'CGPA': '3.32',
            'Fail Subs': '',
            'courses': [
                {'name': 'Computer Networking', 'grade': '3.75'},
            ],
        }
        header_indices, regs, course_map, values, _, _ = get_sheet_data(mock_worksheet)
        assert header_indices is not None
        update_sheet_with_student_data(
            mock_worksheet, parsed, header_indices, regs, course_map, values, header_indices['retake']
        )
        mock_worksheet.batch_update.assert_not_called()

    def test_reg_not_found_skips(self, mock_worksheet):
        parsed = {'Reg': '999', 'Name': 'Nobody'}
        header_indices, regs, course_map, values, _, _ = get_sheet_data(mock_worksheet)
        assert header_indices is not None
        update_sheet_with_student_data(
            mock_worksheet, parsed, header_indices, regs, course_map, values, header_indices['retake']
        )
        mock_worksheet.batch_update.assert_not_called()

    def test_retake_courses_written(self):
        ws = MagicMock()
        ws.get_all_values.return_value = [
            [
                "Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA", "",
                "Computer Networking", "Software Engineering",
                "Microprocessor and Microcontroller", "Database Management Systems-II",
                "Multivariable Calculus and Geometry", "Computer Networking Lab",
                "Software Engineering Lab", "Microprocessor and Assembly Language Lab",
                "Microcontroller Lab", "Retake Courses",
            ],
            [""] * 17,
            ["1", "Test", "CS 001", "710", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ]
        parsed = {
            'Name': 'Test',
            'Roll': 'CS 001',
            'Reg': '710',
            'GPA': '2.50',
            'CGPA': '2.60',
            'Fail Subs': 'CSE-3101, MATH-3105',
            'courses': [],
        }
        header_indices, regs, course_map, values, _, _ = get_sheet_data(ws)
        assert header_indices is not None
        update_sheet_with_student_data(ws, parsed, header_indices, regs, course_map, values, header_indices['retake'])
        ws.batch_update.assert_called_once()
        calls = ws.batch_update.call_args[0][0]
        ranges_written = [c['range'] for c in calls]
        assert any('Q' in r for r in ranges_written)


class TestFormatGpaCgpaColumns:
    def test_converts_text_to_numbers(self):
        ws = MagicMock()
        ws.batch_get.return_value = [
            [["3.42"], ["2.99"], [""], ["3.85"]],
            [["3.32"], ["3.15"], [""], ["3.66"]],
        ]
        format_gpa_cgpa_columns(ws)
        ws.batch_get.assert_called_once()
        ws.batch_update.assert_called_once()
        payload = ws.batch_update.call_args[0][0]
        e_values = payload[0]['values']
        assert e_values[0] == [3.42]
        assert e_values[1] == [2.99]
        assert e_values[2] == ['']
        assert e_values[3] == [3.85]

    def test_applies_format(self):
        ws = MagicMock()
        ws.batch_get.return_value = [[], []]
        format_gpa_cgpa_columns(ws)
        ws.format.assert_called_once()


# ===================================================================
# 4. Selenium (mocked driver)
# ===================================================================

class TestScrapeStudentResult:
    EXAM_TEXT = "B.Sc. in Computer Science and Engineering 3rd year 1st Semester Examination of 2024"

    def _make_driver_html(self, html_content):
        driver = MagicMock()
        driver.page_source = html_content
        return driver

    def _mock_select_for_exam(self):
        """Return a patched Select that matches EXAM_TEXT."""
        mock_select = MagicMock()
        mock_option = MagicMock()
        mock_option.text = self.EXAM_TEXT
        mock_select.options = [mock_option]
        return mock_select

    @patch('scraper_common.time.sleep')
    @patch('scraper_common.FORM_DATA', {'program': 'prog', 'session': 'sess', 'exam': EXAM_TEXT})
    @patch('scraper_common.Select')
    def test_success(self, MockSelect, mock_sleep, sample_result_html):
        driver = self._make_driver_html(sample_result_html)
        MockSelect.return_value = self._mock_select_for_exam()
        result = scrape_student_result(driver, '123')
        assert result is not None
        assert result['Reg'] == 'CS 1234567'
        assert result['Name'] == 'Jane Doe'
        assert result['GPA'] == '3.67'

    @patch('scraper_common.time.sleep')
    @patch('scraper_common.FORM_DATA', {'program': 'prog', 'session': 'sess', 'exam': EXAM_TEXT})
    @patch('scraper_common.Select')
    def test_retry_on_timeout(self, MockSelect, mock_sleep, sample_result_html):
        from selenium.common.exceptions import TimeoutException
        driver = MagicMock()
        call_count = {'n': 0}

        def fake_get(url):
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise TimeoutException("timeout")

        driver.get.side_effect = fake_get
        driver.page_source = sample_result_html
        MockSelect.return_value = self._mock_select_for_exam()
        result = scrape_student_result(driver, '123')
        assert result is not None

    @patch('scraper_common.time.sleep')
    def test_all_retries_fail(self, mock_sleep):
        from selenium.common.exceptions import TimeoutException
        driver = MagicMock()
        driver.get.side_effect = TimeoutException("always fail")
        result = scrape_student_result(driver, '999')
        assert result is None
        assert driver.get.call_count == 3

    @patch('scraper_common.time.sleep')
    @patch('scraper_common.FORM_DATA', {'program': 'prog', 'session': 'sess', 'exam': 'Wrong Exam Name'})
    @patch('scraper_common.Select')
    def test_exam_not_found(self, MockSelect, mock_sleep):
        driver = MagicMock()
        mock_select = MagicMock()
        mock_option = MagicMock()
        mock_option.text = "Some Other Exam"
        mock_select.options = [mock_option]
        MockSelect.return_value = mock_select
        result = scrape_student_result(driver, '123')
        assert result is None


class TestSelectExam:
    @patch('scraper_common.time.sleep')
    @patch('scraper_common.FORM_DATA', {'program': 'prog', 'session': 'sess', 'exam': ''})
    @patch('scraper_common.Select')
    def test_no_exams_found(self, MockSelect, mock_sleep):
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_select = MagicMock()
        mock_select.options = []
        MockSelect.return_value = mock_select
        with patch('scraper_common.initialize_webdriver', return_value=mock_driver), \
             patch('scraper_common.WebDriverWait', return_value=mock_wait):
            select_exam(force=True)
        mock_driver.get.assert_called_once()

    @patch('scraper_common.time.sleep')
    @patch('scraper_common.FORM_DATA', {'program': 'prog', 'session': 'sess', 'exam': ''})
    @patch('scraper_common.Select')
    def test_exams_found_saves_config(self, MockSelect, mock_sleep):
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_select = MagicMock()
        opt1 = MagicMock()
        opt1.text = "Exam A 2024"
        opt2 = MagicMock()
        opt2.text = "Exam B 2023"
        mock_select.options = [opt1, opt2]
        MockSelect.return_value = mock_select

        mock_save = MagicMock()
        mock_load = MagicMock(return_value={"exam": "", "start_regi": 710})

        scraper_common.USE_INQUIRERPY = False
        with patch('scraper_common.initialize_webdriver', return_value=mock_driver), \
             patch('scraper_common.WebDriverWait', return_value=mock_wait), \
             patch('scraper_common.load_config', mock_load), \
             patch('scraper_common.save_config', mock_save), \
             patch('builtins.input', return_value="1"):
            select_exam(force=True)
        scraper_common.USE_INQUIRERPY = True

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert saved_config["exam"] == "Exam A 2024"

    def test_skips_when_exam_already_set(self):
        mock_load = MagicMock(return_value={"exam": "Existing Exam"})
        with patch('scraper_common.load_config', mock_load):
            select_exam(force=False)
        mock_load.assert_called_once()

    def test_force_overrides_existing_exam(self):
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_select = MagicMock()
        opt = MagicMock()
        opt.text = "New Exam"
        mock_select.options = [opt]

        mock_save = MagicMock()
        mock_load = MagicMock(return_value={"exam": "Old Exam"})

        scraper_common.USE_INQUIRERPY = False
        with patch('scraper_common.initialize_webdriver', return_value=mock_driver), \
             patch('scraper_common.WebDriverWait', return_value=mock_wait), \
             patch('scraper_common.Select', return_value=mock_select), \
             patch('scraper_common.time.sleep'), \
             patch('scraper_common.load_config', mock_load), \
             patch('scraper_common.save_config', mock_save), \
             patch('builtins.input', return_value="1"):
            select_exam(force=True)
        scraper_common.USE_INQUIRERPY = True

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert saved_config["exam"] == "New Exam"


class TestTsLogging:
    def test_ts_writes_to_log_file(self):
        import io
        mock_file = io.StringIO()
        with patch('scraper_common.LOG_FILE', mock_file):
            ts("test message")
        mock_file.seek(0)
        content = mock_file.read()
        assert "test message" in content
        assert "[" in content


class TestRequestDelay:
    def test_config_defaults_include_request_delay(self):
        with patch('scraper_common.os.path.exists', return_value=False), \
             patch('scraper_common.DATA_DIR', '/tmp'):
            config = load_config()
        assert config['request_delay'] == 1
