import os
import shutil
import signal
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Vte, WebKit

import config as config_module
import project
import theme as theme_module
from events import SessionStatus
from session import Session, SessionManager, Status
from version import __version__

IDLE_SECS = 300    # 5 min without output → idle
STUCK_SECS = 1800  # 30 min without output → stuck

# Retry budget for a transcript pane whose server is not listening yet.
PANE_LOAD_RETRIES = 10
PANE_RETRY_MS = 500

STATUS_CSS = {
    Status.RUNNING: "success",
    Status.IDLE:    "warning",
    Status.STUCK:   "error",
    Status.DONE:    "dim-label",
    Status.ERROR:   "error",
}

# Sidebar dot colour for the event-derived status of an sdk session.
SDK_STATUS_CSS = {
    SessionStatus.INITIALIZING.value:      "dim-label",
    SessionStatus.WORKING.value:           "success",
    SessionStatus.AWAITING_APPROVAL.value: "warning",
    SessionStatus.AWAITING_INPUT.value:    "accent",
    SessionStatus.DONE.value:              "dim-label",
    SessionStatus.ERROR.value:             "error",
}


# The app's own mark, used for the welcome screen the window opens on
# (ISSUE-021). `docs/art/codinian.svg` is the source of it; the copy under
# `remote/static/` is the same file, for the pages the browser gets.
APP_MARK = Path(__file__).parent / "docs" / "art" / "codinian.svg"

_mark_cache: list = []


def _positive_int(value, fallback: int) -> int:
    """A stored dimension, or the fallback if the file has been hand-edited
    into something that is not a usable size."""
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value if value > 0 else fallback


def _app_mark() -> Gdk.Texture | None:
    """The mark as a texture, or None if the art is not in the checkout.

    Cached because `Adw.StatusPage` is built once per window but the file is
    350 KB of path data to rasterise. Returns a texture rather than an icon
    name so the welcome screen does not depend on the desktop icon having been
    installed; the two are the same drawing either way.
    """
    if _mark_cache:
        return _mark_cache[0]
    if not APP_MARK.is_file():
        return None
    try:
        texture = Gdk.Texture.new_from_file(Gio.File.new_for_path(str(APP_MARK)))
    except GLib.Error:
        return None
    _mark_cache.append(texture)
    return texture


def _short_path(path: str) -> str:
    """A working directory as a sidebar line: the home directory as `~`."""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


class CodinianWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, manager: SessionManager,
                 runtime=None, config: dict | None = None):
        super().__init__(application=app)
        self._manager = manager
        self._runtime = runtime
        self._config = config or {}
        self._terminals: dict[str, Vte.Terminal] = {}
        self._webviews: dict[str, WebKit.WebView] = {}
        self._rows: dict[str, Adw.ActionRow] = {}
        self._row_dots: dict[str, Gtk.Label] = {}
        self._kinds: dict[str, str] = {}
        # Terminal sessions only: the pty child's pid, kept here as well as on
        # the Session so a session closed from a browser -- which takes the
        # manager entry away before this window hears about it -- can still be
        # hung up on.
        self._pids: dict[str, int] = {}
        self._dirty_outputs: set[str] = set()
        # Project sidebar section (M4): rows, panes and cached metadata, keyed
        # by project id.
        self._project_rows: dict[str, Adw.ActionRow] = {}
        self._project_webviews: dict[str, WebKit.WebView] = {}
        self._project_meta: dict[str, dict] = {}
        # Notification bookkeeping: the last status we saw per session (so a
        # repeated status is not announced twice) and the tool named by the
        # approval request that put a session into awaiting_approval.
        self._last_sdk_status: dict[str, str] = {}
        self._pending_tool: dict[str, str] = {}
        # Settings pane (ISSUE-030), built on first use.
        self._settings_view = None
        self._history_webview = None
        self._selection_settling = False

        self.set_title("Codinian")
        self._restore_window_state()
        self._build_ui()
        self._load_projects()

        # sdk sessions report status through the event bus; reflect it on the
        # sidebar dot. Events arrive off the GTK thread, so marshal with idle_add.
        self._manager.subscribe_events(self._on_bus_event)
        # Sessions can also be created from the browser; pick those up too.
        self._manager.on_change(self._on_sessions_changed)

        # The window opens on the welcome screen with nothing selected
        # (ISSUE-021). A `Gtk.ListBox` in SINGLE selection mode selects its
        # first row the moment focus reaches it, and the sidebar takes focus at
        # startup, so the bottom list selected History and its handler switched
        # the content pane to the history page before the user had done
        # anything. Deferred to an idle so it runs after that has happened.
        GLib.idle_add(self._open_on_welcome)

        GLib.timeout_add_seconds(60, self._check_health)
        GLib.timeout_add(500, self._drain_inject_queue)
        GLib.timeout_add(1000, self._sync_outputs)

    # ---------------------------------------------------------- window state

    def _restore_window_state(self) -> None:
        """Come back the size the window was left, and maximised if it was.

        Size only, never position: a Wayland client does not place its own
        window, and GTK4 has no `set_position`. Restoring position is the
        compositor's job through the session-management protocol, which is not
        yet usable here; `config.py` records what is missing and where.

        The unmaximised size is worth keeping even when starting maximised,
        because it is what unmaximising restores to.
        """
        width = _positive_int(self._config.get("window_width"), 1280)
        height = _positive_int(self._config.get("window_height"), 800)
        self.set_default_size(width, height)
        if self._config.get("window_maximized", True):
            self.maximize()
        # Saved on close rather than on every resize: a drag emits a great many
        # notifies and each one would rewrite the config file.
        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, *_args) -> bool:
        self._save_window_state()
        return False  # carry on closing

    def _save_window_state(self) -> None:
        maximized = self.is_maximized()
        self._config["window_maximized"] = maximized
        if not maximized:
            # get_default_size reports the unmaximised size, which is the one
            # worth storing: reading the allocation while maximised would save
            # the screen and never let the window be small again.
            width, height = self.get_default_size()
            if width > 0 and height > 0:
                self._config["window_width"] = width
                self._config["window_height"] = height
        try:
            config_module.save(self._config)
        except OSError:
            pass  # a window closing is no place to raise about a config write

    def _open_on_welcome(self) -> bool:
        """Clear the selection GTK made on our behalf, and show the mark."""
        for listbox in (self._session_list, self._project_list, self._settings_list):
            listbox.unselect_all()
        self._stack.set_visible_child_name("_empty")
        # Blank, not "Codinian": the sidebar header already carries the app name
        # and the welcome screen says it again in large type, so a third copy
        # across the top bar is noise. An empty WindowTitle rather than None,
        # because a header with no title widget falls back to the window's own
        # title, which is exactly the word being avoided. This is only undoing
        # what the history handler wrote on its way through at startup.
        self._content_header.set_title_widget(Adw.WindowTitle(title="", subtitle=""))
        return False

    # ------------------------------------------------------------------ build

    def _build_ui(self):
        split = Adw.NavigationSplitView()
        split.set_max_sidebar_width(300)
        split.set_min_sidebar_width(200)

        split.set_sidebar(self._build_sidebar())
        split.set_content(self._build_content())

        # Window-level, so a toast from the Settings pane survives switching
        # tabs or selecting a session (ISSUE-030).
        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(split)
        self.set_content(self._toasts)

    def _build_sidebar(self) -> Adw.NavigationPage:
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        toolbar.add_top_bar(header)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # The action that adds to a list sits on that list's heading rather than
        # in the window header, so it reads as belonging to the section it fills.
        add_project_btn = self._heading_button("folder-new-symbolic", "Add project",
                                               self._on_add_project_clicked)
        sidebar_box.append(self._section_heading("Projects", [add_project_btn]))
        self._project_list = Gtk.ListBox()
        self._project_list.add_css_class("navigation-sidebar")
        self._project_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._project_list.connect("row-selected", self._on_project_row_selected)
        sidebar_box.append(self._project_list)

        resume_btn = self._heading_button("document-open-recent-symbolic", "Resume session",
                                          self._on_resume_clicked)
        new_btn = self._heading_button("list-add-symbolic", "New session",
                                       self._on_new_clicked)
        sidebar_box.append(self._section_heading("Sessions", [resume_btn, new_btn]))
        self._session_list = Gtk.ListBox()
        self._session_list.add_css_class("navigation-sidebar")
        self._session_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._session_list.connect("row-selected", self._on_row_selected)
        sidebar_box.append(self._session_list)

        scroll.set_child(sidebar_box)
        scroll.set_vexpand(True)

        # Name, version and cogwheel, pinned below the scrolling lists
        # (ISSUE-030). A one-row ListBox rather than a plain Box: it inherits
        # the same navigation-sidebar selection styling as the rows above, so
        # "selected" looks the same here as it does for a project.
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(scroll)
        outer.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Two app-level destinations, in one list so they share the selection
        # highlight with each other and cross-deselect the lists above.
        self._settings_list = Gtk.ListBox()
        self._settings_list.add_css_class("navigation-sidebar")
        self._settings_list.set_selection_mode(Gtk.SelectionMode.SINGLE)

        self._history_row = Adw.ActionRow(
            title="History",
            subtitle="Search past conversations",
            activatable=True,
        )
        self._history_row.add_prefix(Gtk.Image(icon_name="edit-find-symbolic"))
        self._history_row._nav = "history"
        self._settings_list.append(self._history_row)

        self._settings_row = Adw.ActionRow(
            title="Codinian",
            subtitle=__version__,
            activatable=True,
            tooltip_text="Settings",
        )
        self._settings_row.add_suffix(
            Gtk.Image(icon_name="emblem-system-symbolic", valign=Gtk.Align.CENTER)
        )
        self._settings_row._nav = "settings"
        self._settings_list.append(self._settings_row)

        self._settings_list.connect("row-selected", self._on_settings_row_selected)
        outer.append(self._settings_list)

        toolbar.set_content(outer)

        page = Adw.NavigationPage(title="Codinian", tag="sidebar")
        page.set_child(toolbar)
        return page

    @staticmethod
    def _heading_button(icon: str, tooltip: str, on_clicked) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
        btn.add_css_class("flat")
        btn.connect("clicked", on_clicked)
        return btn

    @staticmethod
    def _section_heading(text: str, buttons: list[Gtk.Button] | None = None) -> Gtk.Widget:
        label = Gtk.Label(label=text, xalign=0, hexpand=True, valign=Gtk.Align.CENTER)
        label.add_css_class("dim-label")
        label.add_css_class("caption-heading")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.set_margin_start(12)
        # Less at the end than at the start: a flat button carries its own
        # padding, so matching 12 either side would push the icons too far in.
        row.set_margin_end(6)
        row.set_margin_top(8)
        row.set_margin_bottom(2)
        row.append(label)
        for btn in buttons or ():
            row.append(btn)
        return row

    def _build_content(self) -> Adw.NavigationPage:
        toolbar = Adw.ToolbarView()

        self._content_header = Adw.HeaderBar()
        toolbar.add_top_bar(self._content_header)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        empty = Adw.StatusPage(
            title="Welcome to Codinian",
            description="Select a project or session",
        )
        mark = _app_mark()
        if mark is not None:
            empty.set_paintable(mark)
        else:
            # Only when the art is missing from the checkout, which would make
            # the first screen of the app an empty box.
            empty.set_icon_name("utilities-terminal-symbolic")
        self._stack.add_named(empty, "_empty")

        toolbar.set_content(self._stack)

        page = Adw.NavigationPage(title="Session", tag="content")
        page.set_child(toolbar)
        return page

    # ---------------------------------------------------------------- session

    def add_session(self, session: Session):
        self._kinds[session.id] = session.kind
        if session.kind == "sdk":
            # The pane is built when the session is first selected, not here.
            # A WebKitGTK view costs a set of processes, and a long sidebar
            # used to pay for all of them at startup. The session itself runs
            # either way: the runtime drives it, and the page fetches the
            # transcript when it loads, which is the same path that lets a
            # browser open a session that has been running for an hour.
            if self._runtime is not None:
                self._runtime.create_threadsafe(
                    session.id, session.workdir, session.permission_mode,
                    resume=session.resume_session_id,
                )
            dot_css = SDK_STATUS_CSS[SessionStatus.INITIALIZING.value]
        else:
            view = self._build_session_view(session)
            self._stack.add_named(view, session.id)
            self._spawn(session)
            dot_css = STATUS_CSS[session.status]

        self._add_row(session, dot_css)
        self._session_list.select_row(self._rows[session.id])
        self._show_session(session.id)

    def _add_row(self, session: Session, dot_css: str) -> None:
        dot = Gtk.Label(label="●")
        dot.add_css_class(dot_css)
        self._row_dots[session.id] = dot

        # Titles are written by the model once a conversation names itself,
        # and subtitles are paths. Neither is markup, and an ampersand in
        # either would otherwise come out as a parse warning and an empty
        # label. Set before the text, not with it: a constructor keyword is
        # applied too late to spare the first parse.
        row = Adw.ActionRow(activatable=True)
        row.set_use_markup(False)
        row.set_title(session.name)
        row.set_subtitle(self._session_subtitle(session))
        row.add_prefix(dot)

        # The same menu a project row carries, for the same reason: there is
        # more than one thing to do to a session, and one of them ends it.
        menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic",
                                  valign=Gtk.Align.CENTER,
                                  tooltip_text="Session actions")
        menu_btn.add_css_class("flat")
        popover = Gtk.Popover()
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        rename_btn = Gtk.Button(label="Rename\u2026")
        rename_btn.add_css_class("flat")
        rename_btn.connect("clicked", self._on_rename_menu_clicked, popover, session.id)
        close_btn = Gtk.Button(label="Close Session\u2026")
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", self._on_close_menu_clicked, popover, session.id)
        popover_box.append(rename_btn)
        popover_box.append(close_btn)
        popover.set_child(popover_box)
        menu_btn.set_popover(popover)
        row.add_suffix(menu_btn)

        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_session_row_right_clicked, popover)
        row.add_controller(right_click)

        row._session_id = session.id
        self._rows[session.id] = row
        self._session_list.append(row)

    @staticmethod
    def _session_subtitle(session: Session) -> str:
        """A session row's second line: where the session is running.

        The project that owns its working directory once one is registered,
        and the directory itself otherwise. This line used to hold a goal typed
        at the new-session dialog, which never reached the model and told the
        reader less than the path does."""
        project_id = session.project_id()
        if project_id:
            owning = project.get_project(project_id)
            if owning is not None:
                return owning.name
        return _short_path(session.workdir)

    def _refresh_session_subtitles(self) -> None:
        """Recompute every session row's project name. Registering a folder
        claims the sessions already running inside it, so the rows that were
        built before it was registered would otherwise keep saying nothing."""
        for session in self._manager.all():
            row = self._rows.get(session.id)
            if row is not None:
                row.set_subtitle(self._session_subtitle(session))

    def _adopt_session(self, session: Session) -> None:
        """Build the sidebar row and pane for a session this window did not
        create, such as one started from the browser. The runtime already
        drives it, so this only catches the UI up, and it deliberately does
        not steal the current selection."""
        self._kinds[session.id] = session.kind
        self._add_row(session, SDK_STATUS_CSS.get(session.wire_status(), "dim-label"))

    def _on_sessions_changed(self) -> None:
        """SessionManager change listener. Fires on whichever thread changed the
        session list, so the scan itself runs on the GTK loop."""
        GLib.idle_add(self._sync_session_rows)

    def _sync_session_rows(self) -> bool:
        sessions = self._manager.all()
        live = {session.id for session in sessions}
        for session_id in [sid for sid in self._rows if sid not in live]:
            # Closed somewhere else -- a browser, or another window. Whoever
            # closed it has already taken the subprocess down; what is left is
            # this window's copy of the row and pane.
            self._remove_session_row(session_id)
        for session in sessions:
            row = self._rows.get(session.id)
            if row is None:
                if session.kind != "sdk":
                    # A terminal session this window did not spawn has no pty
                    # here, so there is nothing it could usefully show.
                    continue
                self._adopt_session(session)
                continue
            # A session is renamed after it is built -- by hand, or by adopting
            # the title the CLI generates for the conversation -- so the row
            # follows the session rather than keeping what it opened with.
            if row.get_title() != session.name:
                row.set_title(session.name)
                if self._stack.get_visible_child_name() == session.id:
                    self._refresh_header(session)
            subtitle = self._session_subtitle(session)
            if row.get_subtitle() != subtitle:
                row.set_subtitle(subtitle)
        return False  # one-shot idle callback

    def _refresh_header(self, session: Session) -> None:
        """Put the content header back in step with a session's name."""
        status = session.wire_status() if session.kind == "sdk" else session.status.value
        self._content_header.set_title_widget(
            Adw.WindowTitle(title=session.name, subtitle=status)
        )

    def _on_rename_menu_clicked(self, _btn, popover: Gtk.Popover, session_id: str) -> None:
        popover.popdown()
        self._prompt_rename(session_id)

    def _on_close_menu_clicked(self, _btn, popover: Gtk.Popover, session_id: str) -> None:
        popover.popdown()
        self._confirm_close_session(session_id)

    def _on_session_row_right_clicked(self, _gesture, _n_press, _x, _y,
                                      popover: Gtk.Popover) -> None:
        popover.popup()

    def _prompt_rename(self, session_id: str) -> None:
        session = self._manager.get(session_id)
        if session is None:
            return
        entry = Adw.EntryRow(title="Name")
        entry.set_text(session.name)
        group = Adw.PreferencesGroup()
        group.add(entry)

        dialog = Adw.AlertDialog(
            heading="Rename session",
            body="Sessions name themselves from the conversation. A name set "
                 "here replaces that one and stays put.",
            extra_child=group,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_rename_response, session_id, entry)

        def confirm(_entry) -> None:
            # Enter in the one field this dialog has means Rename. Closing
            # emits the close response after it, which the handler ignores.
            dialog.emit("response", "rename")
            dialog.close()

        entry.connect("entry-activated", confirm)
        dialog.present(self)

    def _on_rename_response(self, _dialog, response: str, session_id: str,
                            entry: Adw.EntryRow) -> None:
        if response != "rename":
            return
        if self._manager.rename(session_id, entry.get_text()):
            # The manager's change listener lands on the GTK loop through an
            # idle callback, so the row catches up on its own.
            self._toasts.add_toast(Adw.Toast(title="Session renamed"))

    # ------------------------------------------------------------ closing

    # What a session is doing, in the words the confirmation uses. A status
    # missing from here is one where the conversation is over already, and
    # those close without asking.
    _CLOSE_WARNINGS = {
        SessionStatus.WORKING.value:
            "Claude is working on a turn right now. Closing stops it part-way.",
        SessionStatus.AWAITING_APPROVAL.value:
            "This session is waiting for you to approve a tool call. Closing "
            "cancels that request.",
        SessionStatus.AWAITING_INPUT.value:
            "This session is connected and waiting for a message.",
        SessionStatus.INITIALIZING.value:
            "This session is still starting up.",
    }

    def _confirm_close_session(self, session_id: str) -> None:
        """Ask before ending a session that has not ended by itself.

        A finished session has nothing left to interrupt, so it closes on the
        click. Everything else gets a dialog naming what is being stopped: the
        button that closes a session sits in the same menu as the one that
        renames it, and a mis-click there would otherwise take a running turn
        with it."""
        session = self._manager.get(session_id)
        if session is None:
            return
        warning = self._close_warning(session)
        if warning is None:
            self._close_session(session_id)
            return

        # The second line is what makes this reversible, so it is said rather
        # than assumed: the CLI writes its own transcript either way, and the
        # resume picker reads it back.
        tail = ("The conversation stays on disk. It can be picked up again from "
                "History, or from the project's Sessions tab."
                if session.kind == "sdk" else
                "Whatever the shell was in the middle of stops with it.")
        dialog = Adw.AlertDialog(
            heading=f"Close \u201c{session.name}\u201d?",
            body=f"{warning}\n\n{tail}",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("close", "Close Session")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_close_response, session_id)
        dialog.present(self)

    def _close_warning(self, session: Session) -> str | None:
        """What the confirmation should say this close is stopping, or None
        when the session has already finished and there is nothing to stop."""
        status = session.wire_status()
        if status in (SessionStatus.DONE.value, SessionStatus.ERROR.value):
            return None
        if session.kind != "sdk":
            # A terminal session's status is derived from how long it has been
            # quiet, which says nothing about what the shell is doing.
            return ("This terminal session is still open. Closing hangs up on "
                    "it, the way closing a terminal window does.")
        return self._CLOSE_WARNINGS.get(status, "This session is still running.")

    def _on_close_response(self, _dialog, response: str, session_id: str) -> None:
        if response == "close":
            self._close_session(session_id)

    def _close_session(self, session_id: str) -> None:
        """End a session and take it out of the window.

        The subprocess goes first, then the row and the pane, then the manager
        entry, whose change broadcast is what tells every browser client the
        session is gone."""
        if self._kinds.get(session_id) == "sdk" and self._runtime is not None:
            self._runtime.close_threadsafe(session_id)
        # A terminal session's child is hung up on inside _remove_session_row,
        # which is also the path a close from a browser arrives by.
        self._remove_session_row(session_id)
        self._manager.remove(session_id)
        app = self.get_application()
        if app is not None:
            # An approval notification for a session that no longer exists
            # would open a pane that is not there any more.
            app.withdraw_notification(f"session-{session_id}")

    def _end_terminal(self, session_id: str) -> None:
        """Hang up on a terminal session's child process, if it has one.

        SIGHUP rather than SIGTERM, and to the group rather than the process:
        it is what a closed terminal sends, `claude` and any shell under it
        already handle it, and the group is where a child of the child is.
        Does nothing for an sdk session, which has no pid here."""
        pid = self._pids.pop(session_id, None)
        if not pid:
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGHUP)
        except OSError:
            pass  # already gone, or never ours to signal

    def _remove_session_row(self, session_id: str) -> None:
        """Drop everything the window holds for a session, and land somewhere
        sensible if its pane was the one on screen."""
        self._end_terminal(session_id)
        row = self._rows.pop(session_id, None)
        index = row.get_index() if row is not None else -1
        if row is not None:
            self._session_list.remove(row)
        self._row_dots.pop(session_id, None)
        self._kinds.pop(session_id, None)
        self._webviews.pop(session_id, None)
        self._terminals.pop(session_id, None)
        self._last_sdk_status.pop(session_id, None)
        self._pending_tool.pop(session_id, None)
        self._dirty_outputs.discard(session_id)

        was_visible = self._stack.get_visible_child_name() == session_id
        child = self._stack.get_child_by_name(session_id)
        if child is not None:
            self._stack.remove(child)
        if not was_visible:
            return
        # The row that took its place, or the one above if it was the last.
        # Falling back to the welcome screen rather than to another session's
        # transcript, because with no sessions left there is nothing to show.
        following = None
        if index >= 0:
            following = (self._session_list.get_row_at_index(index)
                         or self._session_list.get_row_at_index(index - 1))
        if following is not None:
            self._session_list.select_row(following)
            self._show_session(following._session_id)
        else:
            self._open_on_welcome()

    def _build_sdk_view(self, session: Session) -> Gtk.Widget:
        """The transcript pane for an sdk session: the shared web renderer in
        embedded single-session mode, loaded in a WebKitGTK view. Message input
        and tool approvals happen inside the page, over the same WebSocket the
        browser uses, so nothing here needs GTK-side wiring."""
        webview = self._build_webview_pane(self._pane_url(session.id))
        self._webviews[session.id] = webview
        return webview

    def _build_webview_pane(self, url: str) -> WebKit.WebView:
        """A WebKitGTK view pointed at one of our own server's pages, with the
        shared retry-on-load-failure behaviour: the server comes up a moment
        after the window does, so a pane opened in that gap must not be left
        sitting on a WebKit error page."""
        webview = WebKit.WebView()
        webview.set_vexpand(True)
        webview.set_hexpand(True)
        webview._load_attempts = 0
        webview.connect("load-failed", self._on_pane_load_failed)
        # A link in a rendered message goes to the desktop's browser. Without
        # this the pane answers `create` with nothing and the click does
        # nothing, which reads as a broken link.
        webview.connect("create", self._on_pane_create)
        webview.load_uri(url)
        return webview

    def _on_pane_create(self, webview, navigation_action):
        """Hand a link that asked for a new window to the system browser. The
        pane itself stays on the transcript: it has no chrome to get back with."""
        request = navigation_action.get_request()
        uri = request.get_uri() if request is not None else None
        if uri:
            Gtk.UriLauncher(uri=uri).launch(self, None, None)
        return None

    def _pane_url(self, session_id: str) -> str:
        """The pane always talks to the loopback address, whatever the server is
        bound to, and carries the token like any other client."""
        port = self._config.get("port", 8787)
        token = quote(self._config.get("token", ""))
        return (f"http://127.0.0.1:{port}/?embed=1&session={session_id}&token={token}"
                f"{self._theme_param()}")

    def _project_pane_url(self, project_id: str) -> str:
        """The project pane, same loopback/token rules as _pane_url."""
        port = self._config.get("port", 8787)
        token = quote(self._config.get("token", ""))
        return (f"http://127.0.0.1:{port}/project.html?project={project_id}"
                f"&embed=1&token={token}{self._theme_param()}")

    def _theme_param(self) -> str:
        """`&theme=` for a pane URL, or nothing when the choice is to follow the
        desktop. Empty rather than `&theme=system` so the common case produces
        the URL it always did, and the page falls through to its
        prefers-color-scheme query."""
        value = theme_module.query_value(self._config)
        return f"&theme={value}" if value else ""

    def _on_pane_load_failed(self, webview, _event, failing_uri, _error) -> bool:
        """The pane is served by our own server, which comes up a moment after
        the window does. A session created in that gap would otherwise sit on a
        WebKit error page looking hung, so retry a few times before giving up."""
        webview._load_attempts += 1
        if webview._load_attempts > PANE_LOAD_RETRIES:
            return False  # out of patience: let WebKit show its error page

        def retry() -> bool:
            webview.load_uri(failing_uri)
            return False

        GLib.timeout_add(PANE_RETRY_MS, retry)
        return True  # handled; suppress the error page while we retry

    def _build_session_view(self, session: Session) -> Gtk.Box:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        terminal = Vte.Terminal()
        terminal.set_vexpand(True)
        terminal.set_hexpand(True)
        self._terminals[session.id] = terminal

        terminal.connect("contents-changed", lambda _t: self._on_contents_changed(session.id))
        terminal.connect("child-exited", lambda _t, code: self._on_exit(session.id, code))

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(terminal)
        root.append(scroll)

        root.append(Gtk.Separator())

        inject = Gtk.Box(spacing=6)
        inject.set_margin_start(6)
        inject.set_margin_end(6)
        inject.set_margin_top(4)
        inject.set_margin_bottom(4)

        entry = Gtk.Entry(hexpand=True, placeholder_text="Inject a prompt…")
        entry.connect("activate", lambda e: self._do_inject(session.id, e.get_text(), e))

        send = Gtk.Button(label="Send")
        send.add_css_class("suggested-action")
        send.connect("clicked", lambda _b: self._do_inject(session.id, entry.get_text(), entry))

        inject.append(entry)
        inject.append(send)
        root.append(inject)

        return root

    def _spawn(self, session: Session):
        terminal = self._terminals.get(session.id)
        if terminal is None:
            return

        claude_bin = shutil.which("claude")
        cmd = claude_bin or shutil.which("bash") or "/bin/sh"
        if session.resume_session_id and claude_bin:
            argv = [cmd, "--resume", session.resume_session_id]
        else:
            argv = [cmd]

        def on_spawn(_terminal, pid, error, _data):
            if error:
                print(f"[{session.name}] spawn failed: {error}")
            else:
                self._manager.set_pid(session.id, pid)
                self._pids[session.id] = pid

        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            session.workdir,
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            on_spawn,
            None,
        )

    # ---------------------------------------------------------------- actions

    def _do_inject(self, session_id: str, text: str, entry: Gtk.Entry | None = None):
        if not text.strip():
            return
        terminal = self._terminals.get(session_id)
        if terminal:
            terminal.feed_child((text + "\n").encode())
        if entry:
            entry.set_text("")

    def _show_session(self, session_id: str):
        if self._stack.get_child_by_name(session_id) is None:
            session = self._manager.get(session_id)
            if session is not None and session.kind == "sdk":
                self._stack.add_named(self._build_sdk_view(session), session_id)
        self._stack.set_visible_child_name(session_id)
        app = self.get_application()
        if app is not None:
            app.withdraw_notification(f"session-{session_id}")
        session = self._manager.get(session_id)
        if session:
            subtitle = session.wire_status() if session.kind == "sdk" else session.status.value
            title = Adw.WindowTitle(title=session.name, subtitle=subtitle)
            self._content_header.set_title_widget(title)

    # ---------------------------------------------------------------- projects

    def _load_projects(self) -> None:
        """Populate the Projects section from the on-disk registry at
        startup. Mirrors session adoption: rows and panes are built up front,
        but nothing is selected out from under the user."""
        for proj in project.load_registry():
            self._add_project(proj.meta())

    def _add_project(self, meta: dict, select: bool = False) -> None:
        """Add a project's row and pane if not already present, or just
        select the existing one. Safe to call for a project already in the
        sidebar, since add_project() itself is idempotent and can hand back
        an already-registered folder."""
        pid = meta["id"]
        if pid not in self._project_rows:
            # Pane built on first selection; see add_session for why.
            self._add_project_row(meta)
        else:
            self._project_meta[pid] = meta

        self._refresh_session_subtitles()

        if select:
            self._project_list.select_row(self._project_rows[pid])
            self._session_list.unselect_all()
            self._show_project(pid)

    def _build_project_pane(self, project_id: str) -> Gtk.Widget:
        """The project pane: same shell as a transcript pane, pointed at
        project.html instead of the transcript root."""
        webview = self._build_webview_pane(self._project_pane_url(project_id))
        self._project_webviews[project_id] = webview
        return webview

    def _add_project_row(self, meta: dict) -> None:
        pid = meta["id"]
        self._project_meta[pid] = meta

        row = Adw.ActionRow(
            title=meta["name"],
            subtitle=meta["path"] if meta["exists"] else f"Missing — {meta['path']}",
            activatable=True,
        )
        row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        if not meta["exists"]:
            row.add_css_class("dim-label")

        menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER)
        menu_btn.add_css_class("flat")
        popover = Gtk.Popover()
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        remove_btn = Gtk.Button(label="Remove Project…")
        remove_btn.add_css_class("flat")
        remove_btn.connect("clicked", self._on_remove_project_menu_clicked, popover, pid)
        popover_box.append(remove_btn)
        popover.set_child(popover_box)
        menu_btn.set_popover(popover)
        row.add_suffix(menu_btn)

        # Right-click opens the same menu the button does, rather than going
        # straight to the removal dialog: a secondary click is how you ask what
        # the options are, not how you ask for the destructive one.
        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_project_row_right_clicked, popover)
        row.add_controller(right_click)

        row._project_id = pid
        self._project_rows[pid] = row
        self._project_list.append(row)

    def _show_project(self, project_id: str) -> None:
        stack_name = f"project:{project_id}"
        if self._stack.get_child_by_name(stack_name) is None:
            self._stack.add_named(self._build_project_pane(project_id), stack_name)
        else:
            # Selecting a pane that is already built is the desktop's version
            # of the window regaining focus, and the page cannot see it: the
            # WebView never lost focus in the browser sense. Without this, a
            # file changed outside the app stays stale until a reload.
            webview = self._project_webviews.get(project_id)
            if webview is not None:
                webview.evaluate_javascript(
                    "window.codinianRefresh && window.codinianRefresh();",
                    -1, None, None, None, None, None)
        self._stack.set_visible_child_name(stack_name)
        meta = self._project_meta.get(project_id, {})
        title = Adw.WindowTitle(title=meta.get("name", project_id), subtitle="Project")
        self._content_header.set_title_widget(title)

    def _on_remove_project_menu_clicked(self, _btn, popover: Gtk.Popover, project_id: str) -> None:
        popover.popdown()
        self._confirm_remove_project(project_id)

    def _on_project_row_right_clicked(self, _gesture, _n_press, _x, _y,
                                      popover: Gtk.Popover) -> None:
        popover.popup()

    def _confirm_remove_project(self, project_id: str) -> None:
        meta = self._project_meta.get(project_id, {})
        name = meta.get("name", project_id)
        path = meta.get("path", "")

        dialog = Adw.AlertDialog(
            heading=f"Remove “{name}”?",
            body=(f"This removes {path} from Codinian's project list only. "
                  "Nothing on disk is deleted."),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_project_response, project_id)
        dialog.present(self)

    def _on_remove_project_response(self, _dialog, response: str, project_id: str) -> None:
        if response != "remove":
            return
        project.remove_project(project_id)
        self._remove_project_row(project_id)
        self._refresh_session_subtitles()

    def _remove_project_row(self, project_id: str) -> None:
        row = self._project_rows.pop(project_id, None)
        if row is not None:
            self._project_list.remove(row)
        webview = self._project_webviews.pop(project_id, None)
        stack_name = f"project:{project_id}"
        if webview is not None:
            self._stack.remove(webview)
        self._project_meta.pop(project_id, None)
        if self._stack.get_visible_child_name() == stack_name:
            self._stack.set_visible_child_name("_empty")

    def _on_add_project_clicked(self, _btn) -> None:
        chooser = Gtk.FileDialog()
        chooser.select_folder(self, None, self._on_project_folder_chosen)

    def _on_project_folder_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except Exception:
            return  # cancelled, or the picker failed; nothing to add
        if folder is None:
            return
        path = folder.get_path()
        if not path:
            return
        try:
            proj = project.add_project(path)
        except FileNotFoundError:
            return
        self._add_project(proj.meta(), select=True)

    # ---------------------------------------------------------------- signals

    def _on_new_clicked(self, _btn):
        from session_dialog import NewSessionDialog
        dlg = NewSessionDialog(self)
        dlg.connect("session-ready", self._on_session_ready)
        dlg.present(self)

    def _on_session_ready(self, _dlg, session: Session):
        self._manager.add(session)
        self.add_session(session)

    # ---------------------------------------------------------------- settings

    def _show_settings(self, tab: str | None = None) -> None:
        """Bring up the Settings pane, building it on first use.

        Built lazily, unlike the session and project panes: it is one widget
        that most runs never open, and constructing it reaches out to
        `tailscale serve status`."""
        if self._settings_view is None:
            from settings_view import SettingsView
            self._settings_view = SettingsView(self._config)
            self._settings_view.connect("toast", self._on_settings_toast)
            self._settings_view.connect("token-rotated", self._on_token_rotated)
            self._settings_view.connect("theme-changed", self._on_theme_changed)
            self._stack.add_named(self._settings_view, "settings")
        else:
            # Cheap, and it is how a tailnet link set up since the last visit
            # gets picked up without a restart.
            self._settings_view.refresh_remote()

        if tab:
            self._settings_view.show_tab(tab)
        self._stack.set_visible_child_name("settings")
        self._content_header.set_title_widget(
            Adw.WindowTitle(title="Settings", subtitle=f"Codinian {__version__}")
        )
        if self._settings_list.get_selected_row() is None:
            self._settings_list.select_row(self._settings_row)

    def _show_history(self) -> None:
        """The transcript search (ISSUE-015), in a pane like the others.

        Built on first use and then kept: it is a web page, so rebuilding it
        would throw away whatever the user had searched for."""
        if self._history_webview is None:
            self._history_webview = self._build_webview_pane(self._history_pane_url())
            self._stack.add_named(self._history_webview, "history")
        self._stack.set_visible_child_name("history")
        self._content_header.set_title_widget(
            Adw.WindowTitle(title="History", subtitle="Every conversation on this machine")
        )

    def _history_pane_url(self) -> str:
        port = self._config.get("port", 8787)
        token = quote(self._config.get("token", ""), safe="")
        return (f"http://127.0.0.1:{port}/history.html?embed=1&token={token}"
                f"{self._theme_param()}")

    def _on_settings_toast(self, _view, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message))

    def _on_theme_changed(self, _view) -> None:
        # A WebKitGTK pane takes its palette from a data-theme attribute stamped
        # at load time from the URL, so it cannot be restyled in place. Reload
        # every pane with the new theme= parameter.
        self._reload_panes()

    def _on_token_rotated(self, _source):
        # Every open pane is holding the old token in its URL, so its next
        # request would be rejected. Reload them with the new one.
        self._reload_panes()

    def _reload_panes(self) -> None:
        """Reload every WebKitGTK pane from a freshly built URL. Both callers
        changed something the pane can only pick up at load: the token it
        authenticates with, or the theme it paints with."""
        for session_id, webview in self._webviews.items():
            webview._load_attempts = 0
            webview.load_uri(self._pane_url(session_id))
        for project_id, webview in self._project_webviews.items():
            webview._load_attempts = 0
            webview.load_uri(self._project_pane_url(project_id))
        if self._history_webview is not None:
            self._history_webview._load_attempts = 0
            self._history_webview.load_uri(self._history_pane_url())

    def _on_resume_clicked(self, _btn):
        from resume_dialog import ResumeSessionDialog
        dlg = ResumeSessionDialog(self, self._config)
        dlg.connect("session-ready", self._on_session_ready)
        dlg.present(self)

    def _clear_other_selections(self, keep) -> None:
        """Leave exactly one of the three sidebar lists with a selection.

        Guarded, because unselect_all fires row-selected with None and would
        otherwise re-enter through the handler that called this (ISSUE-030)."""
        if self._selection_settling:
            return
        self._selection_settling = True
        try:
            for listbox in (self._session_list, self._project_list, self._settings_list):
                if listbox is not keep:
                    listbox.unselect_all()
        finally:
            self._selection_settling = False

    def _on_row_selected(self, _lb, row):
        if row and hasattr(row, "_session_id"):
            self._clear_other_selections(self._session_list)
            self._show_session(row._session_id)

    def _on_project_row_selected(self, _lb, row):
        if row and hasattr(row, "_project_id"):
            self._clear_other_selections(self._project_list)
            self._show_project(row._project_id)

    def _on_settings_row_selected(self, _lb, row):
        if row is None:
            return
        self._clear_other_selections(self._settings_list)
        if getattr(row, "_nav", None) == "history":
            self._show_history()
        else:
            self._show_settings()

    def _on_exit(self, session_id: str, _code: int):
        # Forget the pid with the process. Signalling a pid the kernel has
        # since handed to something else is the one way this could reach
        # outside the app.
        self._pids.pop(session_id, None)
        self._manager.update_status(session_id, Status.DONE)
        self._refresh_dot(session_id, Status.DONE)

    def _on_contents_changed(self, session_id: str):
        self._manager.record_output(session_id)
        self._dirty_outputs.add(session_id)

    # ---------------------------------------------------------------- monitor

    def _check_health(self) -> bool:
        now = datetime.now()
        for s in self._manager.all():
            if s.kind == "sdk":
                continue  # sdk sessions carry event-derived status, not timing
            if s.status in (Status.DONE, Status.ERROR) or s.last_output_at is None:
                continue
            elapsed = (now - s.last_output_at).total_seconds()
            new = Status.STUCK if elapsed > STUCK_SECS else (Status.IDLE if elapsed > IDLE_SECS else Status.RUNNING)
            if new != s.status:
                self._manager.update_status(s.id, new)
                self._refresh_dot(s.id, new)
        return True

    def _drain_inject_queue(self) -> bool:
        for session_id, text in self._manager.drain_injects():
            self._do_inject(session_id, text)
        return True

    def _sync_outputs(self) -> bool:
        pending, self._dirty_outputs = self._dirty_outputs, set()
        for session_id in pending:
            terminal = self._terminals.get(session_id)
            if terminal is None:
                continue
            html = terminal.get_text_format(Vte.Format.HTML) or ""
            if html.endswith("</pre>"):
                html = html[: -len("</pre>")].rstrip("\n") + "</pre>"
            self._manager.set_output(session_id, html)
        return True

    def _refresh_dot(self, session_id: str, status: Status):
        dot = self._row_dots.get(session_id)
        if dot is None:
            return
        for cls in STATUS_CSS.values():
            dot.remove_css_class(cls)
        dot.add_css_class(STATUS_CSS[status])

    # ------------------------------------------------------------- event bus

    def _on_bus_event(self, session_id: str, event: dict) -> None:
        """Called off the GTK thread by the SessionManager for every event.
        Only status changes touch the widgets, so marshal those to the main
        loop and ignore the rest here."""
        etype = event.get("type")
        if etype == "approval_request":
            # Remembered so the notification can name the tool; the status
            # event that follows carries only the status string.
            self._pending_tool[session_id] = event.get("name") or ""
        elif etype == "status":
            GLib.idle_add(self._on_sdk_status, session_id, event.get("status"))

    def _on_sdk_status(self, session_id: str, status_value: str) -> bool:
        self._refresh_sdk_dot(session_id, status_value)
        self._notify_status(session_id, status_value)
        return False  # one-shot idle callback

    def _refresh_sdk_dot(self, session_id: str, status_value: str) -> None:
        dot = self._row_dots.get(session_id)
        if dot is not None:
            for cls in set(SDK_STATUS_CSS.values()) | set(STATUS_CSS.values()):
                dot.remove_css_class(cls)
            dot.add_css_class(SDK_STATUS_CSS.get(status_value, "dim-label"))
        session = self._manager.get(session_id)
        if session and self._stack.get_visible_child_name() == session_id:
            title = Adw.WindowTitle(title=session.name, subtitle=status_value)
            self._content_header.set_title_widget(title)

    # -------------------------------------------------------- notifications

    def _has_user_attention(self, session_id: str) -> bool:
        """True when the user is already looking at this session, in which case
        a desktop notification would only repeat what is on screen."""
        return self.is_active() and self._stack.get_visible_child_name() == session_id

    def _notify_status(self, session_id: str, status_value: str) -> None:
        previous = self._last_sdk_status.get(session_id)
        self._last_sdk_status[session_id] = status_value
        if status_value == previous:
            return

        # Whatever is on screen for this session describes the state it has
        # just left, so it goes now, before anything is decided about a new
        # one. "Needs approval" outliving the approval is the case that
        # prompted this: the answer can come from the pane, from a browser or
        # from a phone, and none of those routes passed through the code that
        # used to be the only thing that cleared it (selecting the session
        # here). Withdrawing a notification that is not showing is a no-op.
        app = self.get_application()
        if app is not None:
            app.withdraw_notification(f"session-{session_id}")

        # Recorded above before this check, so turning notifications back on
        # does not then announce a status change that happened while they were
        # off (ISSUE-030). Deliberately after the withdrawal above: a
        # notification sent while they were on should still be cleared once it
        # stops being true.
        from settings_view import notifications_enabled
        if not notifications_enabled(self._config):
            return

        session = self._manager.get(session_id)
        if session is None:
            return

        name = session.name or session_id
        if status_value == SessionStatus.AWAITING_APPROVAL.value:
            tool = self._pending_tool.pop(session_id, "")
            title = f"{name} needs approval"
            body = f"Claude wants to run {tool}." if tool else "Claude is waiting on a tool call."
        elif status_value == SessionStatus.ERROR.value:
            title = f"{name} hit an error"
            body = "The session stopped. Open it to see what happened."
        elif (status_value == SessionStatus.AWAITING_INPUT.value
              and previous in (SessionStatus.WORKING.value,
                               SessionStatus.AWAITING_APPROVAL.value)):
            # Only after real work. The awaiting_input a session emits the
            # moment it connects is not worth interrupting anyone about.
            title = f"{name} finished"
            body = "The turn is done and the session is waiting for you."
        else:
            return

        if self._has_user_attention(session_id):
            return

        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_default_action_and_target("app.focus-session",
                                                   GLib.Variant("s", session_id))
        if status_value == SessionStatus.AWAITING_APPROVAL.value:
            notification.set_priority(Gio.NotificationPriority.URGENT)
        # Keyed by session, so a session never stacks up notifications: the
        # newest replaces whatever it had pending.
        self.get_application().send_notification(f"session-{session_id}", notification)

    def focus_session(self, session_id: str) -> None:
        """Bring a session to the front. The entry point for a notification
        click (the app's focus-session action)."""
        row = self._rows.get(session_id)
        if row is not None:
            self._session_list.select_row(row)
        self._show_session(session_id)
        self.present()
