"""Terminal-following themes for the TUI.

Theme choices (persisted as `theme` in config.json):
- "flexoki-light" (default/base): custom theme from the flexoki-light palette.
- "system": auto ansi-light/ansi-dark from terminal detection.
- "dark": fixed ansi-dark escape hatch.
"""

import os
import select
import sys
import termios
import tty

from textual.theme import Theme

ACCENT_BLUE = "#205EA6"

FLEXOKI_LIGHT = Theme(
    name="flexoki-light",
    dark=False,
    primary="#205EA6",
    secondary="#3AA99F",
    warning="#D0A215",
    error="#D14D41",
    success="#879A39",
    accent=ACCENT_BLUE,
    foreground="#100F0F",
    background="#FFFCF0",
    surface="#FFFCF0",
    panel="#CECDC3",
)

THEME_CHOICES = ("flexoki-light", "system", "dark")
DEFAULT_THEME = "flexoki-light"


def _colorfgbg_is_light():
    """Parses COLORFGBG (e.g. '0;15'); True=light bg, False=dark, None=unknown."""
    parts = [p for p in os.environ.get("COLORFGBG", "").replace(";", ":").split(":")]
    nums = [int(p) for p in parts if p.strip().isdigit()]
    if not nums:
        return None
    return nums[-1] >= 8


def _luminance_is_light(response):
    """Parses an OSC-11 reply (rgb:RRRR/GGGG/BBBB); None if unparseable."""
    import re
    match = re.search(r'rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)', response)
    if not match:
        return None

    def scale(part):
        return int(part, 16) / float(16 ** len(part) - 1)

    r, g, b = (scale(match.group(i)) for i in (1, 2, 3))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5


def probe_osc11(read_fd, write_fn, timeout=0.1):
    """Asks the terminal for its background color. True=light, False=dark, None=unknown."""
    try:
        write_fn("\x1b]11;?\x1b\\")
        ready, _, _ = select.select([read_fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(read_fd, 1024).decode("utf-8", errors="replace")
        return _luminance_is_light(data)
    except Exception:
        return None


def detect_terminal_light():
    """Best-effort light-terminal detection. None when undetectable."""
    via_env = _colorfgbg_is_light()
    if via_env is not None:
        return via_env
    if not sys.stdin.isatty():
        return None
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception:
        return None
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b]11;?\x1b\\")
        sys.stdout.flush()
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            return None
        data = os.read(fd, 1024).decode("utf-8", errors="replace")
        return _luminance_is_light(data)
    except Exception:
        return None
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass


def resolve_theme_name(setting):
    """Maps the `theme` setting to a registered Textual theme name."""
    if setting == "dark":
        return "ansi-dark"
    if setting in ("system", "auto"):
        return "ansi-light" if detect_terminal_light() else "ansi-dark"
    return "flexoki-light"
