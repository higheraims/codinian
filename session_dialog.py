from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

import config as config_module
import project
import templates
from session import Session


class NewSessionDialog(Adw.Dialog):
    __gsignals__ = {
        "session-ready": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, parent):
        super().__init__(title="New Session", content_width=440)

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _: self.close())
        header.pack_start(cancel)

        create = Gtk.Button(label="Create")
        create.add_css_class("suggested-action")
        create.connect("clicked", self._on_create)
        header.pack_end(create)
        toolbar.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_margin_start(18)
        body.set_margin_end(18)
        body.set_margin_top(12)
        body.set_margin_bottom(18)

        details = Adw.PreferencesGroup(title="Session Details")

        # Left empty on purpose. Every session used to open as "Session", so a
        # sidebar of them said nothing about which was which; left blank, the
        # session takes the title the CLI generates for the conversation as
        # soon as there is one, and a name typed here overrides that for good.
        self._name = Adw.EntryRow(title="Name (optional)")
        details.add(self._name)

        # Picking a project sets the working directory and the permission
        # mode from that project's defaults, as a starting point only: typing
        # a working directory afterwards is not overwritten, since nothing
        # re-applies the project's path once the user has edited it by hand.
        self._projects = project.load_registry()
        project_names = ["No project"] + [p.name for p in self._projects]
        self._project_combo = Adw.ComboRow(
            title="Project",
            model=Gtk.StringList.new(project_names),
        )
        self._project_combo.connect("notify::selected", self._on_project_selected)
        details.add(self._project_combo)

        self._workdir = Adw.EntryRow(title="Working Directory")
        self._workdir.set_text(str(Path.home()))

        browse = Gtk.Button(icon_name="folder-symbolic", valign=Gtk.Align.CENTER)
        browse.add_css_class("flat")
        browse.connect("clicked", self._on_browse)
        self._workdir.add_suffix(browse)
        details.add(self._workdir)

        # Session kind: the SDK-driven structured transcript is the default;
        # "Terminal" keeps the raw VTE session for dropping to a shell/CLI.
        self._kind_values = ["sdk", "terminal"]
        self._kind = Adw.ComboRow(
            title="Kind",
            model=Gtk.StringList.new(["Agent SDK (structured)", "Terminal (raw)"]),
        )
        details.add(self._kind)

        # Permission mode for sdk sessions, switchable mid-session from the
        # transcript header (ISSUE-012). Ignored by terminal sessions. The list
        # matches session.PERMISSION_MODES and the browser client's dropdown, so
        # a mode can be picked here and changed there without the vocabulary
        # shifting under the user (ISSUE-027).
        self._perm_values = ["default", "acceptEdits", "plan", "bypassPermissions",
                             "dontAsk", "auto"]
        self._perm = Adw.ComboRow(
            title="Permission Mode",
            model=Gtk.StringList.new(["Ask on each tool", "Auto-accept edits",
                                      "Plan mode", "Bypass all prompts",
                                      "Don't ask (allow rules decide)",
                                      "Auto (a classifier decides)"]),
        )
        details.add(self._perm)

        body.append(details)

        # Templates (ISSUE-014): pick a saved one to fill in the working
        # directory and permission mode above, or save the current values as a
        # new one. A template is applied to these fields once,
        # at pick time, and then forgotten -- nothing here keeps hold of a
        # template's name, so deleting one later cannot affect a session
        # already started from it.
        self._current_template = None  # the picked template dict, or None
        template_group = Adw.PreferencesGroup(
            title="Template",
            description="Start from a saved template, or save these settings as a new one.",
        )

        self._template_combo = Adw.ComboRow(title="Template")
        delete_template_btn = Gtk.Button(icon_name="user-trash-symbolic",
                                         valign=Gtk.Align.CENTER, tooltip_text="Delete template")
        delete_template_btn.add_css_class("flat")
        delete_template_btn.connect("clicked", self._on_delete_template)
        self._template_combo.add_suffix(delete_template_btn)
        template_group.add(self._template_combo)

        self._template_name = Adw.EntryRow(title="Save as Template")
        save_template_btn = Gtk.Button(icon_name="document-save-symbolic",
                                       valign=Gtk.Align.CENTER, tooltip_text="Save current settings as a template")
        save_template_btn.add_css_class("flat")
        save_template_btn.connect("clicked", self._on_save_template)
        self._template_name.add_suffix(save_template_btn)
        template_group.add(self._template_name)

        self._refresh_template_combo()
        self._template_combo.connect("notify::selected", self._on_template_selected)

        body.append(template_group)

        toolbar.set_content(body)
        self.set_child(toolbar)

        # The dialog's starting permission mode is decided by the same
        # precedence the project and template pickers fall back on below;
        # see templates.resolve_permission_mode.
        self._config = config_module.load()
        self._apply_permission_precedence()

    def _refresh_template_combo(self, select_name=None):
        """Rebuild the template combo's list from disk. Called at startup
        and after any save or delete, so the dialog never shows a template
        that no longer exists."""
        self._templates = templates.list_templates()
        names = ["None"] + [t["name"] for t in self._templates]
        self._template_combo.set_model(Gtk.StringList.new(names))

        index = 0
        if select_name is not None:
            for i, t in enumerate(self._templates):
                if t["name"] == select_name:
                    index = i + 1
                    break
        self._template_combo.set_selected(index)

    def _apply_permission_precedence(self):
        """Set the Permission Mode row from templates.resolve_permission_mode,
        the single place this precedence is decided (ISSUE-014)."""
        workdir = self._workdir.get_text().strip() or str(Path.home())
        mode = templates.resolve_permission_mode(
            config=self._config, workdir=workdir, template=self._current_template)
        if mode in self._perm_values:
            self._perm.set_selected(self._perm_values.index(mode))

    def _on_project_selected(self, combo, _pspec):
        index = combo.get_selected()
        if index <= 0:
            return  # "No project"
        chosen = self._projects[index - 1]
        self._workdir.set_text(chosen.path)
        self._apply_permission_precedence()

    def _on_template_selected(self, combo, _pspec):
        index = combo.get_selected()
        if index <= 0:
            self._current_template = None
        else:
            self._current_template = self._templates[index - 1]
            if self._current_template["workdir"]:
                self._workdir.set_text(self._current_template["workdir"])
        self._apply_permission_precedence()

    def _on_save_template(self, _btn):
        name = self._template_name.get_text().strip()
        if not name:
            return
        workdir = self._workdir.get_text().strip() or str(Path.home())
        permission_mode = self._perm_values[self._perm.get_selected()]
        templates.save_template(name, workdir=workdir, permission_mode=permission_mode)
        self._template_name.set_text("")
        self._refresh_template_combo(select_name=name)

    def _on_delete_template(self, _btn):
        index = self._template_combo.get_selected()
        if index <= 0:
            return  # "None" has nothing to delete
        name = self._templates[index - 1]["name"]
        templates.delete_template(name)
        self._current_template = None
        self._refresh_template_combo()

    def _on_browse(self, _btn):
        chooser = Gtk.FileDialog()
        chooser.select_folder(self.get_root(), None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self._workdir.set_text(folder.get_path())
        except Exception:
            pass

    def _on_create(self, _btn):
        typed = self._name.get_text().strip()
        workdir = self._workdir.get_text().strip() or str(Path.home())
        # With no name typed, the folder is a placeholder until the session
        # names itself; a typed one is the user's and is never replaced.
        name = typed or Path(workdir).name or "Session"

        kind = self._kind_values[self._kind.get_selected()]
        permission_mode = self._perm_values[self._perm.get_selected()]

        session = Session(name=name, name_is_custom=bool(typed),
                          workdir=workdir, kind=kind,
                          permission_mode=permission_mode)
        templates.record_folder_mode(workdir, permission_mode)
        self.emit("session-ready", session)
        self.close()
