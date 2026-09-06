"""Pilot tests for the Textual TUI (local only, headless)."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import scraper_common as S

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scraper_common_configured = False


def _configure():
    global scraper_common_configured
    if not scraper_common_configured:
        S.configure(
            data_dir=os.path.join(SCRIPT_DIR, "data"),
            credentials_file=os.path.join(SCRIPT_DIR, "credentials.json"),
            env_file=os.path.join(SCRIPT_DIR, ".env"),
            browser="firefox",
        )
        scraper_common_configured = True


_configure()

import tui  # noqa: E402
from tui import ScraperApp  # noqa: E402


def _text(widget):
    return str(widget.render())


def _targets():
    return {
        "session": "2021-2022",
        "normal": {"L1T2": "Normal L1T2 Examination of 2022",
                   "L3T2": "Normal L3T2 Examination of 2024"},
        "retake": {"L1T2": ["Retake L1T2 Examination of 2022",
                             "Retake L1T2 Examination of 2023"]},
    }


async def test_root_quit_with_q():
    app = ScraperApp()
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert True  # exited cleanly


async def test_l_descends_h_backs_out():
    app = ScraperApp()
    async with app.run_test() as pilot:
        assert type(app.screen).__name__ == "TaskScreen"
        await pilot.press("l")
        assert type(app.screen).__name__ == "ScopeScreen"
        await pilot.press("h")
        assert type(app.screen).__name__ == "TaskScreen"
        await pilot.press("q")


async def test_exam_filter_narrows_list():
    from textual.widgets import Input
    from tui import LabeledList
    app = ScraperApp()
    async with app.run_test() as pilot:
        await pilot.press("l", "l")  # root -> scope -> exam picker
        assert type(app.screen).__name__ == "ExamScreen"
        await pilot.press("slash")
        assert app.screen.query_one("#filter", Input).has_focus
        await pilot.press(*list("l3t2"))
        await pilot.pause()
        rows = app.screen.query_one(LabeledList)._rows
        assert len(rows) == 1 and rows[0][1].startswith("L3T2 —")
        await pilot.press("q", "q", "q")


async def test_update_screen_no_new_exams():
    from textual.widgets import Static
    app = ScraperApp()
    async with app.run_test() as pilot:
        await pilot.press("l", "l")
        with patch.object(tui.S, 'refresh_exam_catalog', return_value=([], [])):
            await app.push_screen(tui.UpdateScreen())
            await pilot.pause()
            assert "up to date" in _text(app.screen.query_one("#status", Static))
        await pilot.press("q", "q", "q", "q")


async def test_run_screen_live_counters():
    from textual.widgets import Static

    def fake_main(dry_run=False, reg_num=None, on_event=None):
        on_event("ok")
        on_event("fail")
        return {"scraped": 1, "skipped": 0, "failed": 1}

    app = ScraperApp()
    async with app.run_test() as pilot:
        with patch.object(tui.S, '_set_target_context'), \
             patch.object(tui.S, 'main', side_effect=fake_main):
            await app.push_screen(tui.RunScreen("normal", "Some Exam 2024", 710, 711, True, False))
            await pilot.pause(1.0)
            text = _text(app.screen.query_one("#counters", Static))
            assert "ok=1" in text and "failed=1" in text
            assert "DONE" in text


async def test_settings_save_applies_config():
    from textual.widgets import Input
    app = ScraperApp()
    async with app.run_test() as pilot:
        with patch.object(tui.S, 'save_config') as mock_save, \
             patch.object(tui.S, 'save_env'):
            await app.push_screen(tui.SettingsScreen())
            await pilot.pause()
            app.screen.query_one("#f-session", Input).value = "2022-2023"
            app.screen.query_one("#f-request_delay", Input).value = "9"
            app.screen._save()
            await pilot.pause()
            assert S.CONFIG["session"] == "2022-2023"
            assert S.CONFIG["request_delay"] == 9
            mock_save.assert_called_once()
            assert type(app.screen).__name__ != "SettingsScreen"


async def test_options_bad_range_stays():
    from textual.widgets import Input
    app = ScraperApp()
    async with app.run_test() as pilot:
        with patch.object(tui.S, 'load_targets', return_value=_targets()):
            await app.push_screen(tui.OptionsScreen("normal", "Normal L1T2 Examination of 2022"))
            await pilot.pause()
            app.screen.query_one("#range", Input).value = "bogus"
            app.screen._launch()
            await pilot.pause()
            assert type(app.screen).__name__ == "OptionsScreen"


async def test_code_screen_resolves_and_continues():
    from textual.widgets import Input
    app = ScraperApp()
    async with app.run_test() as pilot:
        with patch.object(tui.S, 'load_targets', return_value=_targets()):
            await app.push_screen(tui.CodeScreen("normal"))
            await pilot.pause()
            app.screen.query_one(Input).value = "L1T2"
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "OptionsScreen"


async def test_app_uses_configured_theme():
    app = ScraperApp()
    async with app.run_test() as pilot:
        assert app.theme in ("flexoki-light", "ansi-light", "ansi-dark")
        assert "flexoki-light" in app._registered_themes
        await pilot.press("q")


async def test_task_about_pane_shows_detail():
    from textual.widgets import Static
    app = ScraperApp()
    async with app.run_test() as pilot:
        assert "Single exam" in _text(app.screen.query_one("#about", Static))
        await pilot.press("j")
        await pilot.pause()
        assert "GPA" in _text(app.screen.query_one("#about", Static))
        await pilot.press("q")


async def test_exam_detail_pane_shows_progress():
    from textual.widgets import Static
    app = ScraperApp()
    async with app.run_test() as pilot:
        with patch.object(tui.S, 'load_targets', return_value=_targets()):
            await pilot.press("l", "l")
            await pilot.pause()
            assert type(app.screen).__name__ == "ExamScreen"
            await pilot.press("j")
            await pilot.pause()
            text = _text(app.screen.query_one("#examinfo", Static))
            assert "Progress:" in text
            await pilot.press("q", "q", "q")


async def test_settings_rejects_bad_theme():
    from textual.widgets import Input
    app = ScraperApp()
    async with app.run_test() as pilot:
        with patch.object(tui.S, 'save_config') as mock_save, \
             patch.object(tui.S, 'save_env'):
            await app.push_screen(tui.SettingsScreen())
            await pilot.pause()
            app.screen.query_one("#f-theme", Input).value = "neon"
            app.screen._save()
            await pilot.pause()
            mock_save.assert_not_called()
            assert type(app.screen).__name__ == "SettingsScreen"
