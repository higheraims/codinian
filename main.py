import asyncio
import sys
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

import config as config_module
import project as project_module
import session as session_module
import theme as theme_module
from db import load_sessions, open_db, save_session
from remote.server import run_server
from sdk_session import SdkRuntime
from session import SessionManager
from window import CodinianWindow

# Where libadwaita keeps the icons its own widgets ask for by name. Every
# `Adw.EntryRow` draws one; see `_repair_libadwaita_icons` for why we have to
# say this out loud.
ADW_ICON_RESOURCE = "/org/gnome/Adwaita/icons/scalable/actions"


def _repair_libadwaita_icons() -> None:
    """Make libadwaita's own icons findable again (ISSUE-034).

    Every text field in the app was drawing a red broken-image square. The
    rows ask for `adw-entry-edit-symbolic` and `adw-entry-apply-symbolic`,
    which are private to libadwaita and shipped inside its GResource. It
    registers that resource at `/org/gnome/Adwaita/icons`, with the icons a
    further `scalable/actions/` down, which is the layout the GTK docs
    describe for a resource path.

    GTK 4.22 does not look there. It finds resource icons only when they sit
    directly at the registered path, which is how GTK's own icon resource is
    laid out; a nested `scalable/actions/` is not found, with or without an
    index.theme beside it. So none of libadwaita's private icons resolve and
    every one of them renders as image-missing.

    Naming the inner directory as a resource path of its own is enough, and it
    costs nothing: the resource is already registered in this process, and the
    icons are the ones libadwaita intended rather than substitutes.

    The obvious alternative is worse and was tried first. Adding a search path
    with the two icons in a `hicolor` tree does not work, because
    `add_search_path` on the display's icon theme has no effect on lookups at
    all, and `set_search_path` only works if our directory comes first, at
    which point our minimal `hicolor` shadows the real one and the app loses
    its own icon along with every other app icon on the machine.

    Harmless once the bug is fixed upstream: it names a directory that either
    exists, in which case the icons were already correct, or does not, in
    which case a resource path that resolves nothing costs a failed lookup.
    """
    display = Gdk.Display.get_default()
    if display is None:
        return  # no display: nothing renders, nothing to repair
    Gtk.IconTheme.get_for_display(display).add_resource_path(ADW_ICON_RESOURCE)


class CodinianApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=config_module.APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._manager = SessionManager()
        self._runtime = SdkRuntime(self._manager)
        self._config = config_module.load()
        self._db = None
        self._win = None
        self.connect("activate", self._on_activate)
        # Every live session's `claude` subprocess is disconnected here rather
        # than left to the daemon thread being cut off at exit (ISSUE-037).
        self.connect("shutdown", self._on_shutdown)

    def _on_activate(self, _app):
        # Before the window is built, so it never paints in the wrong palette
        # and then corrects itself (ISSUE-030).
        theme_module.apply_to_shell(self._config)

        _repair_libadwaita_icons()

        self._db = open_db()
        self._manager.on_change(self._persist)

        # Sessions report which registered project they belong to (ISSUE-020).
        # Wired here rather than imported by session.py, so the session model
        # stays independent of the project registry.
        session_module.set_project_resolver(project_module.cached_resolver())

        self._win = CodinianWindow(self, self._manager, self._runtime, self._config)
        self._win.present()

        # Clicking a session notification jumps to that session.
        focus = Gio.SimpleAction.new("focus-session", GLib.VariantType.new("s"))
        focus.connect("activate", self._on_focus_session)
        self.add_action(focus)

        self._withdraw_stale_notifications()

        threading.Thread(target=self._remote_thread, daemon=True).start()

    def _withdraw_stale_notifications(self) -> None:
        """Clear anything left on screen by the last run.

        A notification outlives the process that sent it, and sessions do not
        survive a restart, so "needs approval" from the previous run is an
        approval nobody can answer any more: its session no longer exists and
        clicking it would jump to nothing. The ids are the sessions we have
        ever had, which the database still holds; withdrawing one that is not
        showing costs nothing. Bounded to the most recent, because only the
        last run's sessions can have anything pending and the table is never
        pruned.
        """
        if self._db is None:
            return
        try:
            sessions = load_sessions(self._db)
        except Exception:
            return  # a startup nicety is no reason to fail to start
        for session in sessions[-50:]:
            self.withdraw_notification(f"session-{session.id}")

    def _on_shutdown(self, _app):
        """Tell every live session to disconnect before the process goes.

        The asyncio loop runs in a daemon thread, so without this the thread is
        simply abandoned when the GTK main loop returns and each session's
        `claude` subprocess finds out by having its pipe closed under it. This
        is bounded (see `close_all_threadsafe`): a quit that hangs would be a
        worse bug than the one it fixes."""
        self._runtime.close_all_threadsafe()

    def _on_focus_session(self, _action, param):
        if self._win is not None:
            self._win.focus_session(param.get_string())

    def _persist(self):
        if self._db:
            for s in self._manager.all():
                save_session(self._db, s)

    def _remote_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_server(self._manager, self._runtime, self._config))


def main():
    # Without this, notifications and the window's own reported name come out
    # as "main.py", the script name GLib falls back to.
    GLib.set_application_name("Codinian")
    app = CodinianApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
