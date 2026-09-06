"""Textual TUI for the DUCMC result scraper (local only; flags bypass it).

Yazi-style motion everywhere: j/k + arrows move, l/Enter/Right select,
h/Esc/Left/q go back, / focuses the exam filter. Scraping streams into an
in-TUI run view; the engine in scraper_common.py is unchanged.
"""

import contextlib
import queue
import threading

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Log,
    ProgressBar,
    Static,
)

import scraper_common as S
from tui_theme import (
    FLEXOKI_LIGHT,
    THEME_CHOICES,
    resolve_theme_name,
)


class QueueWriter:
    """File-like sink that pushes log lines into a queue (for ts() output)."""

    def __init__(self, q):
        self._q = q

    def write(self, s):
        for line in str(s).splitlines():
            if line.strip():
                self._q.put(("log", line))
        return len(s)

    def flush(self):
        pass


def target_progress(title, retake):
    """Reads a target's progress file: (done_in_range, total). No globals touched."""
    import hashlib
    import json
    import os
    start, end = S.START_REGI, S.END_REGI
    total = end - start + 1
    prefix = "progress_retake_" if retake else "progress_"
    path = os.path.join(S.DATA_DIR or "", f"{prefix}{hashlib.md5(title.encode()).hexdigest()[:8]}.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0, total
    scraped = data.get("scraped", data) if isinstance(data, dict) else data
    done = sum(1 for r in scraped
               if str(r).isdigit() and start <= int(r) <= end)
    return done, total


class MenuScreen(Screen):
    """Base screen: Yazi motion over a ListView. h/Esc/q back out."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=True),
        Binding("up", "cursor_up", "Up", show=True),
        Binding("l", "select", "Select", show=True),
        Binding("right", "select", "Select", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("h", "back", "Back", show=True),
        Binding("left", "back", "Back", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "back", "Quit", show=True),
    ]

    def _list(self):
        try:
            return self.query_one(ListView)
        except Exception:
            return None

    def action_cursor_down(self):
        view = self._list()
        if view is not None:
            view.action_cursor_down()

    def action_cursor_up(self):
        view = self._list()
        if view is not None:
            view.action_cursor_up()

    def action_select(self):
        view = self._list()
        if view is not None:
            view.action_select_cursor()

    def action_back(self):
        self.on_back()

    def on_back(self):
        """Back out of this screen (override where Exit differs)."""
        self.app.pop_screen()


class LabeledList(ListView):
    """ListView carrying (key, label) rows with Yazi-friendly lookup."""

    def __init__(self, rows, **kwargs):
        super().__init__(**kwargs)
        self._rows = list(rows)

    async def refill(self, rows):
        """Replaces rows (awaited removal — rapid refills can't duplicate)."""
        self._rows = list(rows)
        await self.remove_children()
        await self.mount_all(ListItem(Label(label)) for _, label in self._rows)

    def selected_key(self):
        if self.index is None or not self._rows:
            return None
        return self._rows[self.index][0]

    async def on_mount(self):
        await self.refill(self._rows)


class TaskScreen(MenuScreen):
    """Root: pick a task (right pane explains the highlighted one)."""

    DETAILS = {
        "normal": "Scrape a normal semester into its PerCourse sheet.\n\nSingle exam or everything pending.",
        "retake": "Scrape retake/improvement grades.\n\nOnly better-than-existing grades are written; GPA recalculated.",
        "update": "Refresh the exam catalog from the portal,\nthen pick additions for targets.json.",
        "settings": "Edit saved config:\nsheet, worksheet, program, session,\nreg range, delay, theme.",
        "exit": "Quit the scraper.",
    }

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="subtitle")
        with Horizontal(id="taskcols"):
            yield LabeledList([
                ("normal", "Scrape normal semester"),
                ("retake", "Scrape retake / improvement"),
                ("update", "Update exam data"),
                ("settings", "Settings"),
                ("exit", "Exit"),
            ], id="menu")
            yield Static("", classes="detail", id="about")
        yield Footer()

    def on_mount(self):
        self.query_one("#subtitle", Static).update(
            f"{S.CONFIG.get('google_sheet_url', '(not set)')} · "
            f"{S.CONFIG.get('session', '')} · "
            f"{S.CONFIG.get('start_regi')}–{S.CONFIG.get('end_regi')}")
        self._show_about("normal")

    def _show_about(self, key):
        try:
            self.query_one("#about", Static).update(self.DETAILS.get(key, ""))
        except Exception:
            pass

    @on(ListView.Highlighted)
    def hover(self, event):
        idx = event.list_view.index
        view = self.query_one(LabeledList)
        if idx is not None and 0 <= idx < len(view._rows):
            self._show_about(view._rows[idx][0])

    @on(ListView.Selected)
    def choose(self):
        key = self.query_one(LabeledList).selected_key()
        if key in (None, "exit"):
            self.app.exit()
        elif key == "update":
            self.app.push_screen(UpdateScreen())
        elif key == "settings":
            self.app.push_screen(SettingsScreen())
        else:
            self.app.push_screen(ScopeScreen(key))

    def on_back(self):
        self.app.exit()


class ScopeScreen(MenuScreen):
    """Breadth picker for one scrape mode."""

    def __init__(self, kind):
        super().__init__()
        self._kind = kind

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Scrape {'retake' if self._kind == 'retake' else 'normal'} — pick breadth")
        yield LabeledList([
            ("single", "Single semester"),
            ("all", "Everything pending (targets.json)"),
            ("commands", "Show multi-terminal commands"),
        ], id="menu")
        yield Footer()

    @on(ListView.Selected)
    def choose(self):
        key = self.query_one(LabeledList).selected_key()
        if key == "single":
            self.app.push_screen(ExamScreen(self._kind))
        elif key == "all":
            self.app.push_screen(OptionsScreen(self._kind, None))
        elif key == "commands":
            self.app.push_screen(CommandsScreen(self._kind))


class ExamScreen(MenuScreen):
    """Fuzzy exam picker (/ focuses the filter)."""

    BINDINGS = MenuScreen.BINDINGS + [
        Binding("/", "focus_filter", "Filter", show=True),
    ]

    def __init__(self, kind):
        super().__init__()
        self._kind = kind
        self._retake = kind == "retake"
        self._all_rows = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Pick a semester (type to filter, / focuses filter)")
        with Horizontal(id="examcols"):
            with Vertical(id="examleft"):
                yield Input(placeholder="filter…", id="filter")
                yield LabeledList([], id="menu")
            yield Static("", classes="detail", id="examinfo")
        yield Footer()

    async def on_mount(self):
        section = S.load_targets().get('retake' if self._retake else 'normal', {})
        rows = []
        for slot, value in section.items():
            titles = value if isinstance(value, list) else [value]
            for title in titles:
                code = slot + ('R' if self._retake else '')
                if len(titles) > 1:
                    code += f"-{S._exam_year_of(title)}"
                rows.append((title, f"{code} — {S._short_label(title)}"))
        rows.append(("", "Other exam code… (e.g. L4T1, L1T2R-2022)"))
        self._all_rows = rows
        await self.query_one(LabeledList).refill(rows)
        self.query_one(ListView).focus()

    def action_focus_filter(self):
        self.query_one("#filter", Input).focus()

    def action_back(self):
        if self.query_one("#filter", Input).has_focus:
            self.query_one(ListView).focus()
        else:
            self.app.pop_screen()

    @on(Input.Changed)
    async def refilter(self, event):
        needle = event.value.strip().lower()
        rows = [(k, label) for k, label in self._all_rows if needle in label.lower()]
        await self.query_one(LabeledList).refill(rows)

    @on(ListView.Highlighted)
    def show_detail(self, event):
        view = self.query_one(LabeledList)
        idx = event.list_view.index
        info = self.query_one("#examinfo", Static)
        if idx is None or not (0 <= idx < len(view._rows)):
            return
        title, label = view._rows[idx]
        if not title:
            info.update("Enter a short code\n(e.g. L4T1, L1T2R-2022).")
            return
        done, total = target_progress(title, self._retake)
        year = S._exam_year_of(title) or "?"
        kind = "retake" if self._retake else "normal"
        info.update(f"{label}\n\nType: {kind}\nYear: {year}\nProgress: {done}/{total} in range")

    @on(ListView.Selected)
    def choose(self):
        key = self.query_one(LabeledList).selected_key()
        if key is None:
            return
        if not key:
            self.app.push_screen(CodeScreen(self._kind))
        else:
            self.app.push_screen(OptionsScreen(self._kind, key))


class CodeScreen(Screen):
    """Free-form exam code entry."""

    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
        Binding("q", "back", "Quit", show=False),
    ]

    def __init__(self, kind):
        super().__init__()
        self._kind = kind

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Exam code (e.g. L4T1, L1T2R-2022) — Enter confirms, Esc backs out")
        yield Input(id="code")
        yield Static("", id="error")
        yield Footer()

    def on_mount(self):
        self.query_one(Input).focus()

    def action_back(self):
        self.app.pop_screen()

    @on(Input.Submitted)
    def resolve(self, event):
        try:
            title = S.resolve_exam_code(event.value.strip(), retake=self._kind == "retake")
        except S.ExamCodeError as e:
            self.query_one("#error", Static).update(f"[ERROR] {e}")
            return
        self.app.pop_screen()
        self.app.push_screen(OptionsScreen(self._kind, title))


class OptionsScreen(MenuScreen):
    """Pre-run options: range, dry-run, fresh."""

    def __init__(self, kind, title):
        super().__init__()
        self._kind = kind
        self._title = title

    def compose(self) -> ComposeResult:
        yield Header()
        label = S._short_label(self._title) if self._title else "everything pending"
        yield Static(f"Run: {label}")
        yield Label("Regs (e.g. 710-813, 710,715 or 710-713,720):")
        yield Input(value=f"{S.CONFIG.get('start_regi')}-{S.CONFIG.get('end_regi')}", id="range")
        yield Checkbox("Dry run (no sheet writes)", id="dry")
        yield Checkbox("Start fresh (ignore saved progress)", id="fresh")
        yield Static("Tab moves · Space toggles · Enter on Run launches · h backs out")
        yield Footer()

    @on(Input.Submitted)
    def launch_from_input(self, event):
        self._launch()

    def action_select(self):
        self._launch()

    def _launch(self):
        try:
            regs = S._parse_regs(
                self.query_one("#range", Input).value,
                S.CONFIG.get('start_regi'), S.CONFIG.get('end_regi'))
        except ValueError as e:
            self.app.notify(f"[ERROR] {e}", severity="error")
            return
        dry = self.query_one("#dry", Checkbox).value
        fresh = self.query_one("#fresh", Checkbox).value if not dry else False
        if regs is None:
            start, end = S.CONFIG.get('start_regi'), S.CONFIG.get('end_regi')
            reg_num, regs_arg = None, None
        elif len(regs) == 1:
            start = end = reg_num = regs[0]
            regs_arg = None
        else:
            start, end, reg_num, regs_arg = regs[0], regs[-1], None, regs
        S.CONFIG["start_regi"] = start
        S.CONFIG["end_regi"] = end
        S.START_REGI = start
        S.END_REGI = end
        self.app.push_screen(RunScreen(self._kind, self._title, start, end, dry, fresh,
                                       reg_num=reg_num, regs=regs_arg))


class RunScreen(Screen):
    """Live scrape view: progress, counters, log tail."""

    BINDINGS = [
        Binding("h", "back", "Back", show=True),
        Binding("left", "back", "Back", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "back", "Quit", show=False),
    ]

    def __init__(self, kind, title, start, end, dry_run, fresh, reg_num=None, regs=None):
        super().__init__()
        self._kind = kind
        self._title = title
        self._start = start
        self._end = end
        self._dry = dry_run
        self._fresh = fresh
        self._reg_num = reg_num
        self._regs = regs
        self._q = queue.Queue()
        self._counts = {"ok": 0, "skip": 0, "fail": 0}
        self._done = False

    def compose(self) -> ComposeResult:
        yield Header()
        label = S._short_label(self._title) if self._title else "everything pending"
        total = len(self._regs) if self._regs else self._end - self._start + 1
        with Vertical(id="runpanel"):
            yield Static(f"Running: {label} ({total} regs)"
                         + (" [dry run]" if self._dry else ""))
            if self._title is not None:
                yield ProgressBar(total=total, id="bar")
            yield Static("", id="counters")
        yield Log(id="log", highlight=False)
        yield Footer()

    def action_back(self):
        self.app.pop_screen()

    def on_mount(self):
        self._timer = self.set_interval(0.1, self._drain)
        thread = threading.Thread(target=self._work, daemon=True)
        thread.start()

    def _emit(self, kind):
        self._q.put(("event", kind))

    def _work(self):
        writer = QueueWriter(self._q)
        try:
            with contextlib.redirect_stdout(writer):
                if self._title is not None:
                    S._set_target_context(self._title, self._kind == "retake")
                    if self._fresh and not self._dry:
                        if self._kind == "retake":
                            S._save_retake_progress([])
                        else:
                            S.save_progress([])
                    if self._kind == "retake":
                        S.scrape_retake_results(dry_run=self._dry,
                                                reg_num=self._reg_num, regs=self._regs,
                                                on_event=self._emit)
                    else:
                        S.main(dry_run=self._dry,
                               reg_num=self._reg_num, regs=self._regs,
                               on_event=self._emit)
                else:
                    self._run_batch()
        except Exception as e:  # never kill the UI thread silently
            self._q.put(("log", f"[ERROR] Run failed: {e}"))
        finally:
            self._q.put(("done", None))

    def _run_batch(self):
        targets = S.load_targets()
        section = targets.get('retake' if self._kind == 'retake' else 'normal', {})
        items = [(slot, t) for slot, v in section.items()
                 for t in (v if isinstance(v, list) else [v])]
        for _, title in items:
            S._set_target_context(title, self._kind == 'retake')
            if self._fresh and not self._dry:
                if self._kind == 'retake':
                    S._save_retake_progress([])
                else:
                    S.save_progress([])
            if self._kind == 'retake':
                S.scrape_retake_results(dry_run=self._dry, on_event=self._emit)
            else:
                S.main(dry_run=self._dry, on_event=self._emit)

    def _drain(self):
        try:
            log = self.query_one("#log", Log)
        except Exception:
            return
        progressed = False
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                log.write_line(payload)
            elif kind == "event":
                self._counts[{"ok": "ok", "skip": "skip", "fail": "fail"}.get(payload, "fail")] += 1
                progressed = True
            elif kind == "done":
                self._done = True
        if progressed or self._done:
            done = sum(self._counts.values())
            try:
                bar = self.query_one("#bar", ProgressBar)
                bar.update(progress=done)
            except Exception:
                pass
            self.query_one("#counters", Static).update(
                f"ok={self._counts['ok']} skipped={self._counts['skip']} "
                f"failed={self._counts['fail']}"
                + (" — DONE (h to go back)" if self._done else ""))
            if self._done:
                self._timer.stop()


class CommandsScreen(MenuScreen):
    """Copy-paste one-liners for multi-terminal runs."""

    def __init__(self, kind):
        super().__init__()
        self._kind = kind

    def compose(self) -> ComposeResult:
        import sys
        section = S.load_targets().get('retake' if self._kind == 'retake' else 'normal', {})
        lines = ["Paste one per terminal:", ""]
        for slot, value in section.items():
            titles = value if isinstance(value, list) else [value]
            for title in titles:
                code = slot + ('R' if self._kind == 'retake' else '')
                if len(titles) > 1:
                    code += f"-{S._exam_year_of(title)}"
                flag = "--retake --retake-exam" if self._kind == 'retake' else "--exam"
                lines.append(f"  {sys.executable} result_scrapper.py {flag} {code}")
        yield Header()
        yield Static("\n".join(lines))
        yield Footer()


class SettingsScreen(Screen):
    """Edit saved config (the sanctioned config writer besides first-run setup)."""

    FIELDS = [
        ("google_sheet_url", "Sheet URL"),
        ("worksheet_name", "Worksheet"),
        ("program", "Program"),
        ("session", "Session"),
        ("start_regi", "Start regi"),
        ("end_regi", "End regi"),
        ("request_delay", "Delay (s)"),
        ("theme", "Theme (flexoki-light/system/dark)"),
    ]

    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
        Binding("q", "back", "Quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Settings — Tab moves, Enter on Save applies, Esc backs out")
        with Vertical():
            for key, label in self.FIELDS:
                yield Label(f"{label}:")
                yield Input(value=str(S.CONFIG.get(key, "")), id=f"f-{key}")
        yield Footer()

    def action_back(self):
        self.app.pop_screen()

    @on(Input.Submitted)
    def save_on_enter(self, event):
        self._save()

    def _save(self):
        try:
            for key, _ in self.FIELDS:
                raw = self.query_one(f"#f-{key}", Input).value.strip()
                if key in ("start_regi", "end_regi", "request_delay"):
                    S.CONFIG[key] = int(raw)
                else:
                    S.CONFIG[key] = raw
        except ValueError as e:
            self.app.notify(f"[ERROR] Numbers only: {e}", severity="error")
            return
        if S.CONFIG.get("theme") not in THEME_CHOICES:
            self.app.notify(f"[ERROR] Theme must be one of: {', '.join(THEME_CHOICES)}",
                            severity="error")
            return
        S.save_config(S.CONFIG)
        S.save_env({
            "GOOGLE_SHEET_URL": S.CONFIG.get("google_sheet_url", ""),
            "WORKSHEET_NAME": S.CONFIG.get("worksheet_name", ""),
            "PROGRAM": S.CONFIG.get("program", ""),
            "SESSION": S.CONFIG.get("session", ""),
        })
        S._sync_globals_from_config()
        self.app.theme = resolve_theme_name(S.CONFIG.get("theme", "flexoki-light"))
        self.app.notify("Settings saved.")
        self.app.pop_screen()


class UpdateScreen(MenuScreen):
    """Refresh catalog from portal, then toggle-pick targets additions."""

    def __init__(self):
        super().__init__()
        self._candidates = []
        self._boxes = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Fetching portal exam list…", id="status")
        yield Vertical(id="picks")
        yield Footer()

    def on_mount(self):
        thread = threading.Thread(target=self._fetch, daemon=True)
        thread.start()

    def _fetch(self):
        result = S.refresh_exam_catalog()
        self.app.call_from_thread(self._offer, result)

    def _offer(self, result):
        if result is None:
            self.query_one("#status", Static).update("Could not reach the portal.")
            return
        new_entries, _ = result
        targets = S.load_targets()
        self._candidates = [e for e in sorted(new_entries, key=lambda e: e['id'])
                            if S._suggestable(e, targets)]
        skipped = len(new_entries) - len(self._candidates)
        box = self.query_one("#picks", Vertical)
        if not self._candidates:
            self.query_one("#status", Static).update(
                f"Catalog up to date ({skipped} new exam(s) not for this batch).")
            return
        self.query_one("#status", Static).update(
            "Space toggles · Enter applies checked · h backs out")
        for e in self._candidates:
            slot = f"L{e['year']}T{e['term']}"
            code = slot + ('R' if e['type'] == 'retake' else '')
            label = f"{code} — {S._short_label(e['name'])}"
            cb = Checkbox(label)
            cb.data = e
            box.mount(cb)
        box.mount(Label(""))
        apply_btn = Checkbox("APPLY checked additions")
        apply_btn.data = "apply"
        box.mount(apply_btn)
        box.children[-1].focus()

    def action_select(self):
        focused = self.focused
        if isinstance(focused, Checkbox):
            focused.value = not focused.value
        else:
            super().action_select()

    @on(Checkbox.Changed)
    def maybe_apply(self, event):
        if getattr(event.checkbox, "data", None) == "apply" and event.value:
            self._apply()

    def _apply(self):
        targets = S.load_targets()
        changed = False
        for cb in self.query_one("#picks", Vertical).query(Checkbox):
            e = getattr(cb, "data", None)
            if not isinstance(e, dict) or not cb.value:
                continue
            slot = f"L{e['year']}T{e['term']}"
            retake = e['type'] == 'retake'
            section = targets.setdefault('retake' if retake else 'normal', {})
            existing = section.get(slot)
            existing_list = existing if isinstance(existing, list) else ([existing] if existing else [])
            if e['name'] in existing_list or (existing and not retake):
                continue
            if retake:
                section[slot] = sorted(existing_list + [e['name']], key=S._exam_year_of)
            else:
                section[slot] = e['name']
            changed = True
        if changed:
            S.save_targets(targets)
            self.app.notify("targets.json updated.")
        self.app.pop_screen()


class ScraperApp(App):
    """Root TUI application."""

    TITLE = "DUCMC Result Scraper"

    CSS = """
    Screen {
        background: $background;
    }
    Header {
        background: $panel;
        color: $foreground;
    }
    Footer {
        background: $panel;
        color: $foreground;
    }
    #subtitle {
        color: $foreground;
        padding: 0 1;
    }
    ListView {
        border: solid $accent;
        background: $surface;
        scrollbar-color: $accent;
    }
    ListView > ListItem {
        padding: 0 1;
    }
    .detail {
        border: solid $accent;
        background: $surface;
        color: $foreground;
        padding: 1 2;
    }
    Input {
        border: solid $accent;
        background: $surface;
    }
    Input:focus {
        border: double $accent;
    }
    Log {
        border: solid $accent;
        background: $surface;
        height: 1fr;
    }
    ProgressBar {
        margin: 0 1;
    }
    #counters {
        color: $foreground;
        padding: 0 1;
    }
    Checkbox {
        padding: 0 1;
    }
    #taskcols, #examcols {
        height: 1fr;
    }
    #taskcols > #menu, #examleft {
        width: 60%;
    }
    #about, #examinfo {
        width: 40%;
    }
    #runpanel {
        border: solid $accent;
        background: $surface;
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self):
        super().__init__()
        self.register_theme(FLEXOKI_LIGHT)
        self.theme = resolve_theme_name(S.CONFIG.get("theme", "flexoki-light"))

    def on_mount(self):
        self.push_screen(TaskScreen())


def run_tui():
    """Launches the TUI (local only)."""
    S._sync_globals_from_config()
    ScraperApp().run()
