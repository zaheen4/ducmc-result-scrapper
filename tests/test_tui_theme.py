"""Tests for tui_theme (detection + theme resolution, no app needed)."""

import os
from unittest.mock import patch

import tui_theme
from tui_theme import (
    DEFAULT_THEME,
    FLEXOKI_LIGHT,
    THEME_CHOICES,
    _colorfgbg_is_light,
    _luminance_is_light,
    detect_terminal_light,
    probe_osc11,
    resolve_theme_name,
)


class TestFlexokiLight:
    def test_is_light_custom_theme(self):
        assert FLEXOKI_LIGHT.dark is False
        assert FLEXOKI_LIGHT.name == "flexoki-light"
        assert FLEXOKI_LIGHT.accent == tui_theme.ACCENT_BLUE

    def test_default_choice(self):
        assert DEFAULT_THEME == "flexoki-light"
        assert set(THEME_CHOICES) == {"flexoki-light", "system", "dark"}


class TestColorfgbg:
    def test_light_bg(self):
        with patch.dict(os.environ, {"COLORFGBG": "0;15"}):
            assert _colorfgbg_is_light() is True

    def test_dark_bg(self):
        with patch.dict(os.environ, {"COLORFGBG": "15;0"}):
            assert _colorfgbg_is_light() is False

    def test_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COLORFGBG", None)
            assert _colorfgbg_is_light() is None


class TestLuminance:
    def test_white(self):
        assert _luminance_is_light("rgb:ffff/ffff/ffff") is True

    def test_black(self):
        assert _luminance_is_light("rgb:0000/0000/0000") is False

    def test_cream_paper(self):
        assert _luminance_is_light("rgb:fff0/fcf0/f0e0") is True

    def test_garbage(self):
        assert _luminance_is_light("hello") is None


class TestOsc11Probe:
    def test_parses_reply(self):
        import pty as pty_mod
        import tty as tty_mod
        master, slave = pty_mod.openpty()
        try:
            tty_mod.setraw(slave)
            os.write(master, b"rgb:ffff/ffff/ffff\x1b\\")
            assert probe_osc11(slave, lambda s: None, timeout=2) is True
        finally:
            os.close(master)
            os.close(slave)

    def test_timeout_returns_none(self):
        import pty as pty_mod
        master, slave = pty_mod.openpty()
        try:
            assert probe_osc11(slave, lambda s: None, timeout=0.05) is None
        finally:
            os.close(master)
            os.close(slave)


class TestResolveTheme:
    def test_explicit(self):
        assert resolve_theme_name("flexoki-light") == "flexoki-light"
        assert resolve_theme_name("dark") == "ansi-dark"
        assert resolve_theme_name("bogus") == "flexoki-light"

    def test_system_light(self):
        with patch.object(tui_theme, 'detect_terminal_light', return_value=True):
            assert resolve_theme_name("system") == "ansi-light"

    def test_system_dark_and_unknown(self):
        with patch.object(tui_theme, 'detect_terminal_light', return_value=False):
            assert resolve_theme_name("system") == "ansi-dark"
        with patch.object(tui_theme, 'detect_terminal_light', return_value=None):
            assert resolve_theme_name("system") == "ansi-dark"


class TestDetect:
    def test_prefers_colorfgbg(self):
        with patch.dict(os.environ, {"COLORFGBG": "0;15"}):
            assert detect_terminal_light() is True

    def test_non_tty_returns_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COLORFGBG", None)
            with patch.object(tui_theme.sys.stdin, 'isatty', return_value=False):
                assert detect_terminal_light() is None
