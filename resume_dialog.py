from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

import templates
from claude_history import PriorSession, last_permission_mode, list_prior_sessions
from session import Session


def _relative_time(dt: datetime) -> str:
    seconds = max(0, (datetime.now() - dt).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    return dt.strftime("%Y-%m-%d")


class ResumeSessionDialog(Adw.Dialog):
    __gsignals__ = {
        "session-ready": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, parent, config: dict | None = None):
        super().__init__(title="Resume Session", content_width=560, content_height=560)
        # Only for resolving the permission mode a resumed session starts in;
        # see _on_row_activated.
        self._config = config or {}

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _: self.close())
        header.pack_start(cancel)
        toolbar.add_top_bar(header)

        self._search = Gtk.SearchEntry(placeholder_text="Search by folder or message…")
        self._search.set_margin_start(12)
        self._search.set_margin_end(12)
        self._search.set_margin_top(6)
        self._search.set_margin_bottom(6)
        self._search.connect("search-changed", self._on_search_changed)
        toolbar.add_top_bar(self._search)

        # How the resumed session runs. "Agent SDK" replays the stored
        # transcript into the pane and continues it through the SDK;
        # "Terminal" hands the id to `claude --resume` in a VTE, which is what
        # this dialog did before there was another kind.
        self._kind_values = ["sdk", "terminal"]
        kind_box = Gtk.Box(spacing=8)
        kind_box.set_margin_start(12)
        kind_box.set_margin_end(12)
        kind_box.set_margin_bottom(6)
        kind_label = Gtk.Label(label="Resume as", xalign=0)
        kind_label.add_css_class("dim-label")
        self._kind = Gtk.DropDown.new_from_strings(["Agent SDK (structured)", "Terminal (raw)"])
        self._kind.set_hexpand(True)
        kind_box.append(kind_label)
        kind_box.append(self._kind)
        toolbar.add_top_bar(kind_box)

        self._sessions = list_prior_sessions()

        self._stack = Gtk.Stack()

        scroll = Gtk.ScrolledWindow(vexpand=True)
        self._list = Gtk.ListBox()
        self._list.add_css_class("boxed-list")
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        self._list.set_margin_bottom(12)
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.connect("row-activated", self._on_row_activated)
        scroll.set_child(self._list)
        self._stack.add_named(scroll, "list")

        empty = Adw.StatusPage(
            icon_name="document-open-recent-symbolic",
            title="No Prior Sessions",
            description="No Claude Code session history was found in ~/.claude/projects",
        )
        self._stack.add_named(empty, "empty")

        toolbar.set_content(self._stack)
        self.set_child(toolbar)

        self._populate(self._sessions)

    def _populate(self, sessions: list[PriorSession]):
        child = self._list.get_row_at_index(0)
        while child is not None:
            self._list.remove(child)
            child = self._list.get_row_at_index(0)

        for prior in sessions:
            row = Adw.ActionRow(
                title=prior.title,
                subtitle=f"{prior.cwd}  ·  {_relative_time(prior.mtime)}",
                activatable=True,
            )
            row.add_prefix(Gtk.Image(icon_name="document-open-recent-symbolic"))
            row._prior_session = prior
            self._list.append(row)

        self._stack.set_visible_child_name("list" if sessions else "empty")

    def _on_search_changed(self, entry: Gtk.SearchEntry):
        query = entry.get_text().strip().lower()
        if not query:
            self._populate(self._sessions)
            return
        filtered = [
            s for s in self._sessions
            if query in s.title.lower() or query in s.cwd.lower()
        ]
        self._populate(filtered)

    def _on_row_activated(self, _list, row):
        prior: PriorSession = row._prior_session
        name = Path(prior.cwd).name or prior.cwd
        kind = self._kind_values[self._kind.get_selected()]
        # Without this the Session dataclass default applied, so every resumed
        # session came back on "ask each time" -- including one that had spent
        # the last hour running unattended in `auto`. The transcript records
        # what it was on, and that beats any default here.
        mode = templates.resolve_permission_mode(
            config=self._config,
            workdir=prior.cwd,
            prior_mode=last_permission_mode(prior.id),
        )
        session = Session(name=name, workdir=prior.cwd,
                          resume_session_id=prior.id, kind=kind,
                          permission_mode=mode)
        self.emit("session-ready", session)
        self.close()
