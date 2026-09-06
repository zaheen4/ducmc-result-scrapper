"""Tests for scraper_common.py — pure functions, config/progress, sheet ops, selenium."""

import json
import os
import sys
from unittest.mock import MagicMock, mock_open, patch

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
    parse_retake_result_html,
    grade_to_point,
    map_exam_to_sheet,
    find_course_column,
    load_config,
    save_config,
    load_progress,
    save_progress,
    get_sheet_data,
    setup_course_columns,
    set_column_widths,
    update_sheet_with_student_data,
    update_sheet_with_retake_data,
    format_gpa_cgpa_columns,
    scrape_student_result,
    select_exam,
    ts,
    recalculate_semester_gpa,
    _load_credit_hours,
    wait_for_exam_options,
    main,
    scrape_retake_results,
    resolve_exam_code,
    maybe_resolve_exam_arg,
    run_batch,
    ExamCodeError,
    interactive_menu,
    _parse_range,
    _short_label,
    _menu_pick_single,
    _prompt_list,
    _prompt_fuzzy,
    _prompt_confirm,
    _prompt_input,
    _sync_globals_from_config,
    _ensure_sheet_configured,
    first_run_setup,
    fetch_portal_exams,
    refresh_exam_catalog,
    update_exam_data,
    _derive_catalog_entry,
    _suggestable,
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
        with patch('scraper_common._get_progress_file', return_value=str(progress_file)):
            save_progress(["100", "101", "102"])
            loaded = load_progress()
        assert loaded == ["100", "101", "102"]

    def test_load_progress_no_file(self, tmp_path):
        progress_file = tmp_path / "nonexistent.json"
        with patch('scraper_common._get_progress_file', return_value=str(progress_file)):
            loaded = load_progress()
        assert loaded == []

    def test_save_progress_overwrites(self, tmp_path):
        progress_file = tmp_path / "progress.json"
        with patch('scraper_common._get_progress_file', return_value=str(progress_file)):
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
        mock_placeholder = MagicMock()
        mock_placeholder.text = "Select your Exam Name"
        mock_option = MagicMock()
        mock_option.text = self.EXAM_TEXT
        mock_select.options = [mock_placeholder, mock_option]
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
        mock_placeholder = MagicMock()
        mock_placeholder.text = "Select your Exam Name"
        mock_option = MagicMock()
        mock_option.text = "Some Other Exam"
        mock_select.options = [mock_placeholder, mock_option]
        MockSelect.return_value = mock_select
        result = scrape_student_result(driver, '123')
        assert result is None


class TestSelectExam:
    @patch('scraper_common.time.sleep')
    @patch('scraper_common.FORM_DATA', {'program': 'prog', 'session': 'sess', 'exam': ''})
    @patch('scraper_common.Select')
    def test_no_exams_found(self, MockSelect, mock_sleep):
        from selenium.common.exceptions import TimeoutException
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_select = MagicMock()
        mock_select.options = []
        MockSelect.return_value = mock_select
        with patch('scraper_common.initialize_webdriver', return_value=mock_driver), \
             patch('scraper_common.WebDriverWait', return_value=mock_wait), \
             patch('scraper_common.time.time', side_effect=[0.0, 999.0]):
            with pytest.raises(TimeoutException):
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
        placeholder = MagicMock()
        placeholder.text = "Select your Exam Name"
        opt = MagicMock()
        opt.text = "New Exam"
        mock_select.options = [placeholder, opt]

        mock_save = MagicMock()
        mock_load = MagicMock(return_value={"exam": "Old Exam"})

        scraper_common.USE_INQUIRERPY = False
        with patch('scraper_common.initialize_webdriver', return_value=mock_driver), \
             patch('scraper_common.WebDriverWait', return_value=mock_wait), \
             patch('scraper_common.Select', return_value=mock_select), \
             patch('scraper_common.time.sleep'), \
             patch('scraper_common.load_config', mock_load), \
             patch('scraper_common.save_config', mock_save), \
             patch('builtins.input', return_value="2"):
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


# ===================================================================
# 5. Retake/Improvement Functions
# ===================================================================

@pytest.fixture
def sample_retake_result_html():
    """Synthetic retake result HTML matching parse_retake_result_html's expected structure."""
    return """
    <div id="exam_result">
      <div class="row justify-content-center">
        <div class="col-12">
          <center>
            <h2 style="border-bottom: 2px solid saddlebrown; padding-bottom: 7px;">
              <b>B.Sc. in Computer Science and Engineering 1st year 2nd Semester Improvement Examination of 2023. (Retake/Improvement)</b>
            </h2>
          </center>
          <hr />
        </div>
        <div class="col-12">
          <table class="table table-bordered">
            <tbody>
              <tr><th>College Name</th><td>Test University</td></tr>
              <tr><th>Student's Name</th><td>Mir Zaheen Waseet</td></tr>
              <tr><th>Registration</th><td>810</td></tr>
              <tr><th>Session</th><td>2021-2022</td></tr>
              <tr><th>Program</th><td>B.Sc. in Computer Science and Engineering</td></tr>
              <tr><th>Exam Roll</th><td>1389</td></tr>
              <tr><th>Class Roll</th><td>CS 2203104</td></tr>
              <tr><th>Exam Year</th><td>2023</td></tr>
              <tr>
                <th>
                  <table width="100%" border="1">
                    <tbody>
                      <tr><td colspan="5" style="vertical-align: middle;">Name of the Subject/Subjects appearing at:</td></tr>
                      <tr>
                        <td>1</td>
                        <td>MATH 1204</td>
                        <td style="padding: 3px; vertical-align: middle;">Methods of Integration, Differential Equations and Series</td>
                        <td>D</td>
                        <td>2.00</td>
                      </tr>
                    </tbody>
                  </table>
                </th>
                <td>
                  <div style="text-align: center; font-weight: bold; font-size: 25px;">
                    Improvement
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """


@pytest.fixture
def sample_retake_sheet_values():
    """Synthetic PerCourse sheet data with existing grades for retake testing."""
    return [
        [
            "Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA",
            "MATH-1204", "CSE-1101", "Retake Courses",
        ],
        [""] * 9,
        [
            "1", "Test Student", "CS 001", "810", "2.50", "2.50",
            "C", "", "",
        ],
    ]


class TestGradeToPoint:
    def test_letter_grades(self):
        assert grade_to_point("A+") == 4.00
        assert grade_to_point("A") == 3.75
        assert grade_to_point("A-") == 3.50
        assert grade_to_point("B+") == 3.25
        assert grade_to_point("B") == 3.00
        assert grade_to_point("B-") == 2.75
        assert grade_to_point("C+") == 2.50
        assert grade_to_point("C") == 2.25
        assert grade_to_point("D") == 2.00
        assert grade_to_point("F") == 0.00

    def test_numeric_string(self):
        assert grade_to_point("3.75") == 3.75
        assert grade_to_point("0.00") == 0.00

    def test_empty_string(self):
        assert grade_to_point("") == 0.00

    def test_none(self):
        assert grade_to_point(None) == 0.00

    def test_unknown_grade(self):
        assert grade_to_point("X") == 0.00


class TestParseRetakeResultHtml:
    def test_extracts_student_info(self, sample_retake_result_html):
        result = parse_retake_result_html(sample_retake_result_html)
        assert result['Name'] == 'Mir Zaheen Waseet'
        assert result['Roll'] == 'CS 2203104'
        assert result['Reg'] == '810'

    def test_extracts_exam_title(self, sample_retake_result_html):
        result = parse_retake_result_html(sample_retake_result_html)
        assert 'exam_title' in result
        assert '1st year 2nd semester' in result['exam_title'].lower()

    def test_extracts_courses(self, sample_retake_result_html):
        result = parse_retake_result_html(sample_retake_result_html)
        assert len(result['courses']) == 1
        assert result['courses'][0]['code'] == 'MATH-1204'
        assert result['courses'][0]['grade'] == '2.00'

    def test_no_gpa_cgpa(self, sample_retake_result_html):
        result = parse_retake_result_html(sample_retake_result_html)
        assert result['GPA'] == ''
        assert result['CGPA'] == ''

    def test_empty_html(self):
        result = parse_retake_result_html("<html><body></body></html>")
        assert result.get('Name') is None
        assert result['courses'] == []
        assert result['exam_title'] == ''


class TestMapExamToSheet:
    def test_first_year_second_semester(self):
        title = "B.Sc. in Computer Science and Engineering 1st year 2nd Semester Improvement Examination of 2023"
        assert map_exam_to_sheet(title) == 'PerCourse_L1T2'

    def test_third_year_first_semester(self):
        title = "B.Sc. in CSE 3rd year 1st Semester Retake Examination of 2024"
        assert map_exam_to_sheet(title) == 'PerCourse_L3T1'

    def test_second_year_second_semester(self):
        title = "B.Sc. in Textile Engineering Level-2 Term-2 Improvement Examination of 2025"
        assert map_exam_to_sheet(title) == 'PerCourse_L2T2'

    def test_unknown_format(self):
        title = "Some random exam title without year/semester info"
        assert map_exam_to_sheet(title) is None


class TestFindCourseColumn:
    def test_finds_existing_course(self):
        course_name_map = {
            'computernetworking': 7,
            'softwareengineering': 8,
            'math1204': 6,
        }
        assert find_course_column('MATH-1204', course_name_map) == 6

    def test_returns_none_for_missing(self):
        course_name_map = {'computernetworking': 7}
        assert find_course_column('MATH-1204', course_name_map) is None


class TestUpdateSheetWithRetakeData:
    def _make_sheet_with_existing_grade(self):
        ws = MagicMock()
        ws.get_all_values.return_value = [
            [
                "Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA",
                "MATH-1204", "CSE-1101", "Retake Courses",
            ],
            [""] * 9,
            [
                "1", "Test Student", "CS 001", "810", "2.50", "2.50",
                "C", "", "",
            ],
        ]
        return ws

    def test_updates_if_grade_improved(self):
        ws = self._make_sheet_with_existing_grade()
        parsed = {
            'Name': 'Test Student',
            'Roll': 'CS 001',
            'Reg': '810',
            'courses': [
                {'name': 'Methods of Integration', 'code': 'MATH-1204', 'grade': '3.50'},
            ],
        }
        header_indices, regs, course_map, values, _, _ = get_sheet_data(ws)
        assert header_indices is not None
        count = update_sheet_with_retake_data(ws, parsed, 'PerCourse_L1T2', header_indices, regs, course_map, values)
        assert count == 1
        ws.batch_update.assert_called_once()

    def test_skips_if_grade_not_improved(self):
        ws = self._make_sheet_with_existing_grade()
        parsed = {
            'Name': 'Test Student',
            'Roll': 'CS 001',
            'Reg': '810',
            'courses': [
                {'name': 'Methods of Integration', 'code': 'MATH-1204', 'grade': '2.25'},
            ],
        }
        header_indices, regs, course_map, values, _, _ = get_sheet_data(ws)
        assert header_indices is not None
        count = update_sheet_with_retake_data(ws, parsed, 'PerCourse_L1T2', header_indices, regs, course_map, values)
        assert count == 0
        ws.batch_update.assert_not_called()

    def test_writes_if_cell_empty(self):
        ws = self._make_sheet_with_existing_grade()
        parsed = {
            'Name': 'Test Student',
            'Roll': 'CS 001',
            'Reg': '810',
            'courses': [
                {'name': 'CSE Intro', 'code': 'CSE-1101', 'grade': '3.00'},
            ],
        }
        header_indices, regs, course_map, values, _, _ = get_sheet_data(ws)
        assert header_indices is not None
        count = update_sheet_with_retake_data(ws, parsed, 'PerCourse_L1T2', header_indices, regs, course_map, values)
        assert count == 1
        ws.batch_update.assert_called_once()

    def test_reg_not_found_skips(self):
        ws = self._make_sheet_with_existing_grade()
        parsed = {
            'Name': 'Nobody',
            'Roll': 'XX 000',
            'Reg': '999',
            'courses': [{'name': 'X', 'code': 'X-000', 'grade': '4.00'}],
        }
        header_indices, regs, course_map, values, _, _ = get_sheet_data(ws)
        assert header_indices is not None
        count = update_sheet_with_retake_data(ws, parsed, 'PerCourse_L1T2', header_indices, regs, course_map, values)
        assert count == 0
        ws.batch_update.assert_not_called()


class TestRecalculateSemesterGpa:
    """Tests for recalculate_semester_gpa function."""

    def _make_ws(self):
        ws = MagicMock()
        ws.update_cell = MagicMock()
        return ws

    def test_basic_gpa_calculation(self):
        ws = self._make_ws()
        credit_hours = {'MATH-1105': 3.0, 'CSE-1101': 2.0}
        # Row 2 has course codes
        sheet_row_2 = ['', '', 'MATH-1105', 'CSE-1101']
        # Student has: A- (3.5) in MATH-1105, B+ (3.25) in CSE-1101
        student_row = ['', '', 'A-', 'B+', '', '']

        result = recalculate_semester_gpa(ws, 3, credit_hours, sheet_row_2, student_row)

        assert result is True
        # (3.5*3 + 3.25*2) / (3+2) = (10.5+6.5)/5 = 3.4
        ws.update_cell.assert_called_once_with(3, 5, 3.4)

    def test_skips_empty_grades(self):
        ws = self._make_ws()
        credit_hours = {'CSE-1101': 3.0, 'CHE-1104': 3.0}
        sheet_row_2 = ['', 'CSE-1101', 'CHE-1104']
        # CHE-1104 grade is empty
        student_row = ['', 'A', '', '', '', '', '']

        result = recalculate_semester_gpa(ws, 3, credit_hours, sheet_row_2, student_row)

        assert result is True
        # Only CSE-1101 counts: (3.75*3) / 3 = 3.75
        ws.update_cell.assert_called_once_with(3, 5, 3.75)

    def test_skips_unknown_course_codes(self):
        ws = self._make_ws()
        credit_hours = {'CSE-1101': 3.0}
        sheet_row_2 = ['', 'CSE-1101', 'UNKNOWN-999']
        student_row = ['', 'A', 'A', '', '', '', '']

        result = recalculate_semester_gpa(ws, 3, credit_hours, sheet_row_2, student_row)

        assert result is True
        # Only CSE-1101 counts
        ws.update_cell.assert_called_once_with(3, 5, 3.75)

    def test_returns_false_when_no_credits(self):
        ws = self._make_ws()
        credit_hours = None
        sheet_row_2 = ['', 'CSE-1101']
        student_row = ['', 'A']

        result = recalculate_semester_gpa(ws, 3, credit_hours, sheet_row_2, student_row)

        assert result is False
        ws.update_cell.assert_not_called()

    def test_returns_false_when_zero_credits(self):
        ws = self._make_ws()
        credit_hours = {}
        sheet_row_2 = ['', 'CSE-1101']
        student_row = ['', 'A']

        result = recalculate_semester_gpa(ws, 3, credit_hours, sheet_row_2, student_row)

        assert result is False
        ws.update_cell.assert_not_called()

    def test_rounds_to_two_decimals(self):
        ws = self._make_ws()
        credit_hours = {'CSE-1101': 3.0, 'CHE-1104': 3.0}
        sheet_row_2 = ['', 'CSE-1101', 'CHE-1104']
        # A- (3.5) and B+ (3.25)
        student_row = ['', 'A-', 'B+', '', '', '', '']

        result = recalculate_semester_gpa(ws, 3, credit_hours, sheet_row_2, student_row)

        assert result is True
        # (3.5*3 + 3.25*3) / 6 = 20.25/6 = 3.375 -> rounds to 3.38
        ws.update_cell.assert_called_once_with(3, 5, 3.38)


class TestLoadCreditHours:
    """Tests for _load_credit_hours function."""

    def test_loads_credit_hours(self):
        with patch('scraper_common.DATA_DIR', 'data'), \
             patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data='{"CSE-1101": 3.0}')):
            result = _load_credit_hours()
            assert result == {'CSE-1101': 3.0}

    def test_returns_none_when_no_data_dir(self):
        with patch('scraper_common.DATA_DIR', None):
            result = _load_credit_hours()
            assert result is None

    def test_returns_none_when_file_missing(self):
        with patch('scraper_common.DATA_DIR', 'data'), \
             patch('os.path.exists', return_value=False):
            result = _load_credit_hours()
            assert result is None


class TestExamCatalog:
    """Tests for data/exam_catalog.json integrity."""

    def _load_catalog(self):
        catalog_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'exam_catalog.json')
        with open(catalog_path, 'r') as f:
            return json.load(f)

    def test_ids_unique(self):
        catalog = self._load_catalog()
        ids = [e['id'] for e in catalog['exams']]
        assert len(ids) == len(set(ids))

    def test_required_fields_present(self):
        catalog = self._load_catalog()
        for exam in catalog['exams']:
            assert {'id', 'name', 'type', 'year', 'term', 'exam_year'} <= set(exam.keys())
            assert exam['type'] in ('normal', 'retake')

    def test_newest_exams_present(self):
        catalog = self._load_catalog()
        by_id = {e['id']: e for e in catalog['exams']}
        assert by_id[1889]['type'] == 'normal'
        assert by_id[1888]['type'] == 'retake'
        assert by_id[1878]['special'] is True
        assert by_id[1851]['special'] is True
        assert by_id[1777]['type'] == 'normal'
        assert by_id[1776]['type'] == 'retake'

    def test_new_exams_map_to_sheets(self):
        catalog = self._load_catalog()
        by_id = {e['id']: e for e in catalog['exams']}
        assert map_exam_to_sheet(by_id[1889]['name']) == 'PerCourse_L3T2'
        assert map_exam_to_sheet(by_id[1888]['name']) == 'PerCourse_L3T2'
        assert map_exam_to_sheet(by_id[1878]['name']) == 'PerCourse_L2T2'
        assert map_exam_to_sheet(by_id[1851]['name']) == 'PerCourse_L4T2'
        assert map_exam_to_sheet(by_id[1777]['name']) == 'PerCourse_L1T2'
        assert map_exam_to_sheet(by_id[1776]['name']) == 'PerCourse_L1T2'

    def test_new_retakes_match_retake_filter(self):
        catalog = self._load_catalog()
        retake_keywords = ['improvement', 'retake']
        by_id = {e['id']: e for e in catalog['exams']}
        for exam_id in (1888, 1878, 1851, 1776):
            name = by_id[exam_id]['name']
            assert any(kw in name.lower() for kw in retake_keywords)


class TestWaitForExamOptions:
    def _mock_select(self, texts):
        mock_select = MagicMock()
        opts = []
        for t in texts:
            o = MagicMock()
            o.text = t
            opts.append(o)
        mock_select.options = opts
        return mock_select

    def test_returns_immediately_when_populated(self):
        driver = MagicMock()
        full = self._mock_select(["Select your Exam Name", "Exam A 2024"])
        with patch('scraper_common.Select', return_value=full):
            result = wait_for_exam_options(driver, timeout=10)
        assert result is full
        driver.find_element.assert_called_once()

    def test_polls_until_populated(self):
        driver = MagicMock()
        empty = self._mock_select(["Select your Exam Name"])
        full = self._mock_select(["Select your Exam Name", "Exam A 2024"])
        with patch('scraper_common.Select', side_effect=[empty, empty, full]), \
             patch('scraper_common.time.sleep') as mock_sleep, \
             patch('scraper_common.time.time', side_effect=[0.0, 1.0, 2.0]):
            result = wait_for_exam_options(driver, timeout=10)
        assert result is full
        assert mock_sleep.call_count == 2

    def test_raises_on_timeout(self):
        from selenium.common.exceptions import TimeoutException
        driver = MagicMock()
        empty = self._mock_select(["Select your Exam Name"])
        with patch('scraper_common.Select', return_value=empty), \
             patch('scraper_common.time.sleep'), \
             patch('scraper_common.time.time', side_effect=[0.0, 999.0]):
            with pytest.raises(TimeoutException):
                wait_for_exam_options(driver, timeout=10)


class TestDryRunProgress:
    """Dry runs must never create, modify, or clear progress files."""

    def _make_ws(self):
        ws = MagicMock()
        ws.get_all_values.return_value = [
            ["Sl.", "Student's Name", "Student's ID", "Reg. No.", "GPA", "CGPA"],
            [""] * 6,
            ["1", "Test", "CS 001", "710", "", ""],
        ]
        return ws

    def _make_parsed(self):
        return {
            'Name': 'Test', 'Roll': 'CS 001', 'Reg': '710',
            'GPA': '3.50', 'CGPA': '3.50', 'Fail Subs': '',
            'courses': [{'name': 'X', 'code': 'CSE-1101', 'grade': 'A-'}],
        }

    def test_normal_dry_run_writes_no_progress(self, tmp_path):
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.START_REGI', 710), \
             patch('scraper_common.END_REGI', 710), \
             patch('scraper_common.REQUEST_DELAY', 0), \
             patch('scraper_common.get_worksheet', return_value=self._make_ws()), \
             patch('scraper_common.initialize_webdriver', return_value=MagicMock()), \
             patch('scraper_common.scrape_student_result', return_value=self._make_parsed()), \
             patch('scraper_common.format_gpa_cgpa_columns'):
            main(dry_run=True)
        assert os.listdir(str(tmp_path)) == []

    def test_normal_real_run_writes_progress(self, tmp_path):
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.START_REGI', 710), \
             patch('scraper_common.END_REGI', 710), \
             patch('scraper_common.REQUEST_DELAY', 0), \
             patch('scraper_common.get_worksheet', return_value=self._make_ws()), \
             patch('scraper_common.initialize_webdriver', return_value=MagicMock()), \
             patch('scraper_common.scrape_student_result', return_value=self._make_parsed()), \
             patch('scraper_common.update_sheet_with_student_data'), \
             patch('scraper_common.format_gpa_cgpa_columns'):
            main(dry_run=False)
        assert os.listdir(str(tmp_path)) != []

    def test_retake_dry_run_writes_no_progress(self, tmp_path):
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.CONFIG', {'retake_exam': 'Retake Exam'}), \
             patch('scraper_common.FORM_DATA', {'program': 'p', 'session': 's', 'exam': 'e'}), \
             patch('scraper_common.START_REGI', 710), \
             patch('scraper_common.END_REGI', 710), \
             patch('scraper_common.REQUEST_DELAY', 0), \
             patch('scraper_common.get_spreadsheet', return_value=MagicMock()), \
             patch('scraper_common.initialize_webdriver', return_value=MagicMock()), \
             patch('scraper_common.scrape_student_result', return_value=self._make_parsed()):
            scrape_retake_results(dry_run=True)
        assert os.listdir(str(tmp_path)) == []


class TestResolveExamCode:
    TARGETS = {
        "session": "2021-2022",
        "normal": {"L1T2": "Normal L1T2 Examination of 2022"},
        "retake": {"L1T2": ["Retake L1T2 Examination of 2022", "Retake L1T2 Examination of 2023"]},
    }

    def test_normal_slot(self):
        assert resolve_exam_code("L1T2", targets=self.TARGETS, catalog=[]) == "Normal L1T2 Examination of 2022"

    def test_case_insensitive(self):
        assert resolve_exam_code("l1t2", targets=self.TARGETS, catalog=[]) == "Normal L1T2 Examination of 2022"

    def test_retake_year_suffix(self):
        assert resolve_exam_code("L1T2R-2023", targets=self.TARGETS, catalog=[]) == "Retake L1T2 Examination of 2023"

    def test_bare_retake_slot_ambiguous(self):
        with pytest.raises(ExamCodeError, match="L1T2R-2022"):
            resolve_exam_code("L1T2R", targets=self.TARGETS, catalog=[])

    def test_retake_flag_selects_section(self):
        with pytest.raises(ExamCodeError):
            resolve_exam_code("L1T2", retake=True, targets=self.TARGETS, catalog=[])

    def test_invalid_code(self):
        with pytest.raises(ExamCodeError):
            resolve_exam_code("banana", targets=self.TARGETS, catalog=[])

    def test_unknown_slot_no_catalog(self):
        with pytest.raises(ExamCodeError, match="No exam found"):
            resolve_exam_code("L4T2", targets=self.TARGETS, catalog=[])

    def test_catalog_fallback(self):
        catalog = [
            {"name": "Cat L4T1 Examination of 2024", "year": 4, "term": 1, "type": "normal"},
            {"name": "Cat L4T1 Examination of 2022", "year": 4, "term": 1, "type": "normal"},
        ]
        assert resolve_exam_code("L4T1-2022", targets=self.TARGETS, catalog=catalog) == "Cat L4T1 Examination of 2022"
        with pytest.raises(ExamCodeError, match="L4T1-2024"):
            resolve_exam_code("L4T1", targets=self.TARGETS, catalog=catalog)

    def test_real_targets_file(self):
        catalog_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'targets.json')
        with open(catalog_path, 'r') as f:
            targets = json.load(f)
        title = resolve_exam_code("L3T2", targets=targets, catalog=[])
        assert title == "B.Sc. in Computer Science and Engineering 3rd year 2nd Semester Examination of 2024"

    def test_maybe_resolve_passthrough(self):
        assert maybe_resolve_exam_arg("Some Raw Exam Title") == "Some Raw Exam Title"
        with patch('scraper_common.load_targets', return_value=self.TARGETS):
            assert maybe_resolve_exam_arg("L1T2") == "Normal L1T2 Examination of 2022"


class TestRunBatch:
    TARGETS = {
        "session": "2021-2022",
        "normal": {"L1T2": "Normal L1T2", "L2T1": "Normal L2T1"},
        "retake": {"L1T2": ["Retake L1T2 2022", "Retake L1T2 2023"]},
    }

    def test_normal_batch_calls_main_per_target(self):
        seen = []
        with patch('scraper_common.load_targets', return_value=self.TARGETS), \
             patch('scraper_common.CONFIG', {"google_sheet_url": "http://x"}), \
             patch('scraper_common.FORM_DATA', {"program": "p", "session": "s", "exam": ""}), \
             patch('scraper_common._auto_resolve_worksheet', return_value=None), \
             patch('scraper_common.main', side_effect=lambda **kw: seen.append(dict(scraper_common.CONFIG))) as mock_main:
            run_batch(retake=False)
        assert mock_main.call_count == 2
        assert [c["exam"] for c in seen] == ["Normal L1T2", "Normal L2T1"]

    def test_retake_batch_calls_scrape_per_exam(self):
        with patch('scraper_common.load_targets', return_value=self.TARGETS), \
             patch('scraper_common.CONFIG', {"google_sheet_url": "http://x"}), \
             patch('scraper_common.FORM_DATA', {"program": "p", "session": "s", "exam": ""}), \
             patch('scraper_common.scrape_retake_results') as mock_retake:
            run_batch(retake=True)
        assert mock_retake.call_count == 2

    def test_empty_targets_no_crash(self):
        with patch('scraper_common.load_targets', return_value={"normal": {}, "retake": {}}), \
             patch('scraper_common.main') as mock_main:
            run_batch(retake=False)
        mock_main.assert_not_called()


class TestMenuHelpers:
    def test_short_label(self):
        assert _short_label("B.Sc. in Computer Science and Engineering 3rd year 2nd Semester Examination of 2024") == \
            "3rd year 2nd Semester Examination of 2024"

    def test_parse_range_pair(self):
        assert _parse_range("710-813", 1, 2) == (710, 813)

    def test_parse_range_single(self):
        assert _parse_range("810", 1, 2) == (810, 810)

    def test_parse_range_empty_uses_defaults(self):
        assert _parse_range("", 710, 813) == (710, 813)

    def test_parse_range_invalid(self):
        with pytest.raises(ValueError):
            _parse_range("abc", 710, 813)
        with pytest.raises(ValueError):
            _parse_range("813-710", 710, 813)


class TestInteractiveMenu:
    TARGETS = {
        "normal": {"L1T2": "Normal L1T2 Examination of 2022"},
        "retake": {"L1T2": ["Retake L1T2 Examination of 2022", "Retake L1T2 Examination of 2023"]},
    }

    def _base_patches(self):
        return (
            patch('scraper_common.CONFIG', {
                "google_sheet_url": "http://x", "worksheet_name": "ws",
                "program": "p", "session": "s", "start_regi": 710, "end_regi": 813,
            }),
            patch('scraper_common.FORM_DATA', {"program": "p", "session": "s", "exam": ""}),
            patch('scraper_common.START_REGI', 710),
            patch('scraper_common.END_REGI', 813),
            patch('scraper_common.load_targets', return_value=self.TARGETS),
            patch('scraper_common._auto_resolve_worksheet', return_value=None),
        )

    def test_single_normal_runs_main(self, capsys):
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch('scraper_common._prompt_list', side_effect=["Normal semester", "Single semester", "Exit"]), \
             patch('scraper_common._prompt_fuzzy', return_value="Normal L1T2 Examination of 2022"), \
             patch('scraper_common._prompt_input', return_value="710-813"), \
             patch('scraper_common._prompt_confirm', return_value=False), \
             patch('scraper_common.main') as mock_main:
            interactive_menu()
            exam = scraper_common.CONFIG["exam"]
            sheet_url = scraper_common.GOOGLE_SHEET_URL
            program = scraper_common.FORM_DATA["program"]
        mock_main.assert_called_once_with(dry_run=False, reg_num=None)
        assert exam == "Normal L1T2 Examination of 2022"
        assert sheet_url == "http://x"
        assert program == "p"
        assert "already scraped" in capsys.readouterr().out

    def test_single_retake_dry_run(self):
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch('scraper_common._prompt_list', side_effect=["Retake / improvement", "Single semester", "Exit"]), \
             patch('scraper_common._prompt_fuzzy', return_value="Retake L1T2 Examination of 2023"), \
             patch('scraper_common._prompt_input', return_value="810"), \
             patch('scraper_common._prompt_confirm', return_value=True) as mock_confirm, \
             patch('scraper_common.scrape_retake_results') as mock_retake:
            interactive_menu()
            retake_exam = scraper_common.CONFIG["retake_exam"]
        mock_retake.assert_called_once_with(dry_run=True, reg_num=810)
        assert retake_exam == "Retake L1T2 Examination of 2023"
        assert mock_confirm.call_count == 1  # fresh prompt skipped on dry run

    def test_single_fresh_clears_progress(self):
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch('scraper_common._prompt_list', side_effect=["Normal semester", "Single semester", "Exit"]), \
             patch('scraper_common._prompt_fuzzy', return_value="Normal L1T2 Examination of 2022"), \
             patch('scraper_common._prompt_input', return_value="710-813"), \
             patch('scraper_common._prompt_confirm', side_effect=[False, True]), \
             patch('scraper_common.save_progress') as mock_save, \
             patch('scraper_common.main') as mock_main:
            interactive_menu()
        mock_save.assert_called_once_with([])
        mock_main.assert_called_once_with(dry_run=False, reg_num=None)

    def test_everything_runs_batch(self):
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch('scraper_common._prompt_list', side_effect=["Normal semester", "Everything pending (targets.json)", "Exit"]), \
             patch('scraper_common._prompt_input', return_value="710-813"), \
             patch('scraper_common._prompt_confirm', side_effect=[False, False, True]), \
             patch('scraper_common.run_batch') as mock_batch:
            interactive_menu()
        mock_batch.assert_called_once_with(retake=False, dry_run=False, fresh=False)

    def test_everything_fresh_passthrough(self):
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch('scraper_common._prompt_list', side_effect=["Normal semester", "Everything pending (targets.json)", "Exit"]), \
             patch('scraper_common._prompt_input', return_value="710-813"), \
             patch('scraper_common._prompt_confirm', side_effect=[False, True, True]), \
             patch('scraper_common.run_batch') as mock_batch:
            interactive_menu()
        mock_batch.assert_called_once_with(retake=False, dry_run=False, fresh=True)

    def test_exit_first_quits_immediately(self):
        with patch('scraper_common._prompt_list', return_value="Exit"), \
             patch('scraper_common.main') as mock_main:
            interactive_menu()
        mock_main.assert_not_called()

    def test_back_from_scope_returns_to_mode(self):
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch('scraper_common._prompt_list', side_effect=["Normal semester", "← Back", "Exit"]), \
             patch('scraper_common.main') as mock_main:
            interactive_menu()
        mock_main.assert_not_called()

    def test_back_from_picker_returns_to_mode(self):
        patches = self._base_patches()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
             patch('scraper_common._prompt_list', side_effect=["Normal semester", "Single semester", "Exit"]), \
             patch('scraper_common._prompt_fuzzy', return_value="← Back"), \
             patch('scraper_common.main') as mock_main:
            interactive_menu()
        mock_main.assert_not_called()

    def test_update_from_mode(self):
        with patch('scraper_common._prompt_list', side_effect=["Update exam data", "Exit"]), \
             patch('scraper_common.update_exam_data') as mock_update:
            interactive_menu()
        mock_update.assert_called_once_with()

    def test_show_commands_prints_one_liners(self, capsys):
        with patch('scraper_common._prompt_list', side_effect=["Normal semester", "Show multi-terminal commands", "Exit"]), \
             patch('scraper_common.load_targets', return_value=self.TARGETS):
            interactive_menu()
        out = capsys.readouterr().out
        assert "--exam L1T2" in out

    def test_freeform_code_fallback(self):
        with patch('scraper_common._prompt_fuzzy', return_value=None), \
             patch('scraper_common._prompt_input', return_value="L1T2"), \
             patch('scraper_common.load_targets', return_value=self.TARGETS):
            title = _menu_pick_single(self.TARGETS["normal"], retake=False)
        assert title == "Normal L1T2 Examination of 2022"

    def test_picker_back_returns_sentinel(self):
        with patch('scraper_common._prompt_fuzzy', return_value="← Back"):
            assert _menu_pick_single(self.TARGETS["normal"], retake=False) == "← Back"


class TestSyncGlobals:
    def _sync_with(self, config):
        with patch('scraper_common.CONFIG', config), \
             patch('scraper_common.FORM_DATA', {"program": "", "session": "", "exam": ""}):
            _sync_globals_from_config()

    def test_request_delay_honored(self):
        with patch('scraper_common.REQUEST_DELAY', 1):
            self._sync_with({"request_delay": 4})
            assert scraper_common.REQUEST_DELAY == 4

    def test_request_delay_garbage_keeps_default(self):
        with patch('scraper_common.REQUEST_DELAY', 1):
            self._sync_with({"request_delay": "soon"})
            assert scraper_common.REQUEST_DELAY == 1

    def test_sheet_and_range_synced(self):
        with patch('scraper_common.FORM_DATA', {"program": "", "session": "", "exam": ""}):
            with patch('scraper_common.CONFIG', {
                "google_sheet_url": "http://sheet", "worksheet_name": "ws",
                "program": "prog", "session": "sess", "exam": "ex",
                "start_regi": 700, "end_regi": 800, "request_delay": 2,
            }):
                _sync_globals_from_config()
                assert scraper_common.GOOGLE_SHEET_URL == "http://sheet"
                assert scraper_common.WORKSHEET_NAME == "ws"
                assert scraper_common.START_REGI == 700
                assert scraper_common.END_REGI == 800
                assert scraper_common.REQUEST_DELAY == 2
            assert scraper_common.FORM_DATA["program"] == "prog"
            assert scraper_common.FORM_DATA["session"] == "sess"

    def test_ensure_resyncs_after_setup(self):
        config = {"google_sheet_url": "", "worksheet_name": ""}

        def fake_setup(cfg):
            cfg["google_sheet_url"] = "http://fresh"
            cfg["worksheet_name"] = "fresh-ws"

        with patch('scraper_common.CONFIG', config), \
             patch('scraper_common.FORM_DATA', {"program": "", "session": "", "exam": ""}), \
             patch('scraper_common.first_run_setup', side_effect=fake_setup):
            _ensure_sheet_configured()
            assert scraper_common.GOOGLE_SHEET_URL == "http://fresh"
            assert scraper_common.WORKSHEET_NAME == "fresh-ws"


class TestFirstRunSetup:
    def test_asks_request_delay(self):
        config = {"google_sheet_url": "", "worksheet_name": "", "program": "p",
                  "session": "s", "start_regi": 710, "end_regi": 813}
        inputs = iter(["http://sheet", "ws", "p", "s", "710", "813", "4"])
        with patch('scraper_common.USE_INQUIRERPY', False), \
             patch('builtins.input', side_effect=lambda *a: next(inputs)), \
             patch('scraper_common.save_config'), \
             patch('scraper_common.save_env'):
            first_run_setup(config)
        assert config["request_delay"] == 4


class TestMenuSettings:
    def test_settings_runs_setup_and_resyncs(self):
        config = {
            "google_sheet_url": "http://x", "worksheet_name": "ws",
            "program": "p", "session": "s", "start_regi": 710, "end_regi": 813,
        }
        with patch('scraper_common.CONFIG', config), \
             patch('scraper_common.FORM_DATA', {"program": "", "session": "", "exam": ""}), \
             patch('scraper_common._prompt_list', side_effect=["Normal semester", "Settings (sheet, session, range, delay)", "Exit"]), \
             patch('scraper_common.first_run_setup') as mock_setup:
            interactive_menu()
            synced_url = scraper_common.GOOGLE_SHEET_URL
        mock_setup.assert_called_once_with(config)
        assert synced_url == "http://x"


class TestPromptKwargs:
    """Guard against InquirerPy version drift: every question shape we use
    must construct against the installed version (cf. NumberPrompt 'float')."""

    def test_used_question_types_construct(self):
        from InquirerPy.resolver import question_mapping
        question_mapping["input"](message="m", default="1")
        question_mapping["list"](message="m", choices=["a", "b"])
        question_mapping["confirm"](message="m", default=False)
        question_mapping["fuzzy"](message="m", choices=["a", "b"])

    def test_setup_asks_numbers_as_input(self):
        import sys
        import types
        fake = types.ModuleType("InquirerPy")
        captured = {}

        def fake_prompt(questions):
            captured["types"] = [q["type"] for q in questions]
            return {"google_sheet_url": "http://s", "worksheet_name": "ws",
                    "program": "p", "session": "s",
                    "start_regi": "710", "end_regi": "813", "request_delay": "4"}

        fake.prompt = fake_prompt
        config = {}
        with patch.dict(sys.modules, {"InquirerPy": fake}), \
             patch('scraper_common.USE_INQUIRERPY', True), \
             patch('scraper_common.save_config'), \
             patch('scraper_common.save_env'):
            first_run_setup(config)
        assert "number" not in captured["types"]
        assert config["start_regi"] == 710
        assert config["end_regi"] == 813
        assert config["request_delay"] == 4


class TestFetchPortalExams:
    def _fake_response(self, body):
        resp = MagicMock()
        resp.read.return_value = body.encode('utf-8')
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_parses_options(self):
        body = ('<option value="">Select</option>'
                '<option value="1889">B.Sc. X 3rd year 2nd Semester Examination of 2024</option>'
                '<option value="abc">Bad ID</option>')
        with patch('scraper_common.urllib.request.urlopen', return_value=self._fake_response(body)):
            exams = fetch_portal_exams()
        assert exams == [(1889, "B.Sc. X 3rd year 2nd Semester Examination of 2024")]

    def test_network_failure_returns_none(self):
        with patch('scraper_common.urllib.request.urlopen', side_effect=Exception("down")):
            assert fetch_portal_exams() is None


class TestDeriveCatalogEntry:
    def test_normal(self):
        entry = _derive_catalog_entry(
            1889, "B.Sc. in Computer Science and Engineering 3rd year 2nd Semester Examination of 2024")
        assert entry == {"id": 1889,
                         "name": "B.Sc. in Computer Science and Engineering 3rd year 2nd Semester Examination of 2024",
                         "type": "normal", "year": 3, "term": 2, "exam_year": 2024}

    def test_retake_special_old(self):
        entry = _derive_catalog_entry(
            1878, "B.Sc. in Computer Science and Engineering 2nd year 2nd Semester Special Improvement Examination of 2023 (Special Improvement - Old Syllabus)")
        assert entry["type"] == "retake"
        assert (entry["year"], entry["term"]) == (2, 2)
        assert entry["exam_year"] == 2023
        assert entry["special"] is True
        assert entry["curriculum"] == "old"


class TestRefreshCatalog:
    def _write_catalog(self, path, exams):
        with open(os.path.join(str(path), 'exam_catalog.json'), 'w') as f:
            json.dump({"program": "p", "exams": exams}, f, indent=2)

    def test_adds_new_keeps_old(self, tmp_path):
        old = {"id": 100, "name": "Old Exam 2020", "type": "normal",
               "year": 1, "term": 1, "exam_year": 2020, "session": "kept"}
        self._write_catalog(tmp_path, [old])
        live = [(100, "Old Exam 2020"),
                (200, "B.Sc. in Computer Science and Engineering 1st year 1st Semester Examination of 2022")]
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.fetch_portal_exams', return_value=live):
            added, removed = refresh_exam_catalog()
        assert [e["id"] for e in added] == [200]
        assert removed == []
        catalog = json.load(open(os.path.join(str(tmp_path), 'exam_catalog.json')))
        assert [e["id"] for e in catalog["exams"]] == [200, 100]  # newest first
        assert catalog["exams"][1] == old  # untouched, custom fields kept

    def test_reports_removed(self, tmp_path):
        old = {"id": 100, "name": "Gone Exam", "type": "normal",
               "year": 1, "term": 1, "exam_year": 2020}
        self._write_catalog(tmp_path, [old])
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.fetch_portal_exams', return_value=[]):
            added, removed = refresh_exam_catalog()
        assert added == [] and removed == [100]
        catalog = json.load(open(os.path.join(str(tmp_path), 'exam_catalog.json')))
        assert [e["id"] for e in catalog["exams"]] == [100]  # kept locally

    def test_fetch_failure_returns_none(self, tmp_path):
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.fetch_portal_exams', return_value=None):
            assert refresh_exam_catalog() is None


class TestSuggestable:
    def test_filters(self):
        assert _suggestable({"name": "B.Sc. X 2nd year 2nd Semester Special Improvement Examination of 2023 (Special Improvement)"}, "2021-2022") is False
        assert _suggestable({"name": "B.Sc. X 4th year 1st Semester Examination of 2024 (2020-2021)"}, "2021-2022") is False
        assert _suggestable({"name": "B.Sc. X 2nd year 2nd Semester Retake/Improvement Examination of 2022 (Retake/Improvement - Old Curriculum)"}, "2021-2022") is False
        assert _suggestable({"name": "B.Sc. X 3rd year 2nd Semester Examination of 2024"}, "2021-2022") is True


class TestUpdateExamData:
    def test_accept_and_reject(self, tmp_path):
        targets = {"session": "2021-2022",
                   "normal": {},
                   "retake": {"L1T2": ["B.Sc. X 1st year 2nd Semester Improvement Examination of 2022 (Retake/Improvement)"]}}
        with open(os.path.join(str(tmp_path), 'targets.json'), 'w') as f:
            json.dump(targets, f)
        new_entries = [
            {"id": 1, "name": "B.Sc. X 5th year 9th Semester Examination of 2025",
             "type": "normal", "year": 1, "term": 1, "exam_year": 2025},
            {"id": 2, "name": "B.Sc. X 1st year 2nd Semester Improvement Examination of 2023 (Retake/Improvement)",
             "type": "retake", "year": 1, "term": 2, "exam_year": 2023},
        ]
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.refresh_exam_catalog', return_value=(new_entries, [])), \
             patch('scraper_common._prompt_confirm', side_effect=[True, False]):
            update_exam_data()
        updated = json.load(open(os.path.join(str(tmp_path), 'targets.json')))
        assert updated["normal"]["L1T1"] == "B.Sc. X 5th year 9th Semester Examination of 2025"
        assert updated["retake"]["L1T2"] == ["B.Sc. X 1st year 2nd Semester Improvement Examination of 2022 (Retake/Improvement)"]

    def test_no_new_exams(self, tmp_path):
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.refresh_exam_catalog', return_value=([], [])):
            update_exam_data()  # must not crash without targets.json present

    def test_fetch_failure_aborts(self, tmp_path):
        with patch('scraper_common.DATA_DIR', str(tmp_path)), \
             patch('scraper_common.refresh_exam_catalog', return_value=None), \
             patch('scraper_common._prompt_confirm') as mock_confirm:
            update_exam_data()
        mock_confirm.assert_not_called()


class TestPromptHelpers:
    def test_helpers_enable_vi_mode(self):
        import InquirerPy
        cases = [
            (_prompt_list, {"message": "m", "choices": ["a"]}, "choice"),
            (_prompt_fuzzy, {"message": "m", "choices": ["a"]}, "choice"),
            (_prompt_confirm, {"message": "m"}, "ok"),
            (_prompt_input, {"message": "m"}, "value"),
        ]
        for helper, kwargs, key in cases:
            with patch.object(InquirerPy, 'prompt', return_value={key: "x"}) as mock_prompt:
                helper(**kwargs)
                assert mock_prompt.call_args[1].get("vi_mode") is True
