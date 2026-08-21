"""The Settings pane (ISSUE-030).

A tabbed area reached from the bottom of the sidebar, sitting in the content
stack beside the session transcripts and the project workspaces. Its tabs use
`Adw.InlineViewSwitcher`, which is the flat underlined strip the project
workspace draws in CSS, so the two read as the same interface.

Scope is the global config in `~/.config/codinian/config.json`. Each repo's own
`.codinian/settings.json` is edited beside the repo it belongs to, in the
project workspace, not here.

Native rather than a page the browser client renders: see the note at the top of
`remote_panel.py` for why the token and the bind stay on this machine.
"""

from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk

import agent_options
import config as config_module
import db
import project
import theme as theme_module
from agent_options import default_permission_mode
from version import __version__

# Same order and wording as the browser client's dropdown and the new-session
# dialog, so a mode means one thing wherever it is chosen (ISSUE-027).
PERMISSION_MODE_ORDER = ["default", "acceptEdits", "plan", "bypassPermissions",
                         "dontAsk", "auto"]
PERMISSION_MODE_LABELS = {
    "default": "Ask on each tool",
    "acceptEdits": "Auto-accept edits",
    "plan": "Plan mode",
    "bypassPermissions": "Bypass all prompts",
    "dontAsk": "Don't ask (allow rules decide)",
    "auto": "Auto (a classifier decides)",
}

THEME_LABELS = {"system": "Follow the desktop", "light": "Light", "dark": "Dark"}


class GeneralPage(Adw.PreferencesPage):
    """The app's own chrome: theme and notifications.

    Anything that shapes how a session talks to Claude Code lives on the Claude
    tab instead, including the permission mode this page used to carry
    (ISSUE-032)."""

    __gsignals__ = {
        "toast": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # The theme changed. The window reloads its WebKitGTK panes, which
        # cannot be restyled in place: their palette comes from a `data-theme`
        # attribute set at load from the URL.
        "theme-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        # Set while a control is being populated from config, so the handler
        # can tell a programmatic change from a click and not write back a
        # value it just read.
        self._loading = True

        appearance = Adw.PreferencesGroup(
            title="Appearance",
            description="Applies to the window and to the transcript and project "
                        "panes inside it, which are web views with a palette of "
                        "their own.",
        )
        self._theme = Adw.ComboRow(
            title="Theme",
            model=Gtk.StringList.new([THEME_LABELS[t] for t in theme_module.THEMES]),
        )
        self._theme.set_selected(theme_module.THEMES.index(theme_module.current(config)))
        self._theme.connect("notify::selected", self._on_theme_changed)
        appearance.add(self._theme)
        self.add(appearance)

        notifications = Adw.PreferencesGroup(title="Notifications")
        self._notify = Adw.SwitchRow(
            title="Desktop notifications",
            subtitle="Raised when a session needs an approval, hits an error, or "
                     "finishes a turn, and never for the session you are already "
                     "looking at.",
        )
        self._notify.set_active(notifications_enabled(config))
        self._notify.connect("notify::active", self._on_notify_toggled)
        notifications.add(self._notify)
        self.add(notifications)

        self._loading = False

    def _on_theme_changed(self, combo, _param) -> None:
        if self._loading:
            return
        value = theme_module.THEMES[combo.get_selected()]
        theme_module.save(self._config, value)
        theme_module.apply_to_shell(self._config)
        self.emit("theme-changed")
        self.emit("toast", f"Theme set to {THEME_LABELS[value].lower()}")

    def _on_notify_toggled(self, switch, _param) -> None:
        if self._loading:
            return
        self._config["notifications"] = switch.get_active()
        config_module.save(self._config)


EFFORT_LABELS = {
    agent_options.UNSET: "Default (the CLI decides)",
    "low": "Low — fastest, least thinking",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra high — best for coding and agentic work",
    "max": "Max — deepest reasoning, slowest",
}

THINKING_LABELS = {
    agent_options.UNSET: "Default (the CLI decides)",
    "summarized": "Show a summary of the reasoning",
    "omitted": "Think, but return no reasoning text",
    "disabled": "No extended thinking",
}


class ClaudePage(Adw.PreferencesPage):
    """Everything that shapes how a session talks to Claude Code (ISSUE-032).

    These are session-creation options: each is read when a session starts, so
    a change here applies to the next session and leaves running ones alone.
    """

    __gsignals__ = {
        "toast": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        self._loading = True

        prompt = Adw.PreferencesGroup(
            title="System prompt",
            description="Claude Code's own system prompt is what makes it say "
                        "what it is about to do and summarise what it did. "
                        "Without it a session has no system prompt at all, and "
                        "the transcript reads as a list of tool calls with "
                        "nothing between them.",
        )
        self._preset = Adw.SwitchRow(
            title="Use Claude Code's system prompt",
            subtitle="Off means no system prompt whatsoever, not a shorter one.",
        )
        self._preset.set_active(agent_options.use_preset(config))
        self._preset.connect("notify::active", self._on_preset_toggled)
        prompt.add(self._preset)

        self._append = Adw.EntryRow(title="Add to it")
        self._append.set_text(agent_options.system_prompt_append(config))
        self._append.set_show_apply_button(True)
        self._append.connect("apply", self._on_append_applied)
        prompt.add(self._append)
        self.add(prompt)

        streaming = Adw.PreferencesGroup(
            title="Streaming",
            description="Only assistant text streams. Thinking and tool "
                        "arguments are almost all of what the model generates "
                        "and neither is legible half-written, so forwarding "
                        "them would cost roughly ninety times the traffic for "
                        "nothing a reader can use.",
        )
        self._stream = Adw.SwitchRow(
            title="Show text as it is written",
            subtitle="Off means each reply appears whole when it is finished.",
        )
        self._stream.set_active(agent_options.stream_partial_text(config))
        self._stream.connect("notify::active", self._on_stream_toggled)
        streaming.add(self._stream)
        self.add(streaming)

        subagents = Adw.PreferencesGroup(
            title="Subagents",
            description="A subagent's tool calls always reach the transcript. "
                        "This decides whether its prose and reasoning come too, "
                        "which is the difference between watching one work and "
                        "seeing only what it reported at the end.",
        )
        self._forward = Adw.SwitchRow(
            title="Show what subagents say",
            subtitle="Nested inside the Agent call that spawned them.",
        )
        self._forward.set_active(agent_options.forward_subagent_text(config))
        self._forward.connect("notify::active", self._on_forward_toggled)
        subagents.add(self._forward)
        self.add(subagents)

        footer = Adw.PreferencesGroup(
            title="Transcript footer",
            description="What the strip along the bottom of a transcript shows "
                        "beside the token counts. On a subscription the dollar "
                        "figure is not a bill -- the usage is included -- so "
                        "what constrains the work is the plan's windows. On an "
                        "API key it is the other way round.",
        )
        self._plan_usage = Adw.SwitchRow(
            title="Show plan usage",
            subtitle="The limit window in force and its reset time, plus "
                     "percentages whenever you run /usage in a session.",
        )
        self._plan_usage.set_active(agent_options.display_prefs(config)["show_plan_usage"])
        self._plan_usage.connect("notify::active", self._on_plan_usage_toggled)
        footer.add(self._plan_usage)

        self._cost = Adw.SwitchRow(
            title="Show cost",
            subtitle="The running dollar total for the session.",
        )
        self._cost.set_active(agent_options.display_prefs(config)["show_cost"])
        self._cost.connect("notify::active", self._on_cost_toggled)
        footer.add(self._cost)

        self._cache_tokens = Adw.SwitchRow(
            title="Show cache token counts",
            subtitle="Cache create and cache read. Prompt caching is automatic, "
                     "so these are the widest numbers in the row and the least "
                     "often acted on.",
        )
        self._cache_tokens.set_active(agent_options.display_prefs(config)["show_cache_tokens"])
        self._cache_tokens.connect("notify::active", self._on_cache_tokens_toggled)
        footer.add(self._cache_tokens)
        self.add(footer)

        model_group = Adw.PreferencesGroup(
            title="Model",
            description="Leave blank to let the CLI choose. Free text rather "
                        "than a list because model names change faster than "
                        "this app ships; a name that does not exist fails when "
                        "the session starts, with the API's own message.",
        )
        self._model = Adw.EntryRow(title="Model")
        self._model.set_text(agent_options.model(config))
        self._model.set_show_apply_button(True)
        self._model.connect("apply", self._on_model_applied)
        model_group.add(self._model)
        self.add(model_group)

        reasoning = Adw.PreferencesGroup(
            title="Reasoning",
            description="Effort is the largest single lever on cost, latency "
                        "and answer quality. Thinking is billed the same "
                        "whether or not its text comes back, so showing the "
                        "summary costs nothing.",
        )
        self._effort = Adw.ComboRow(
            title="Effort",
            model=Gtk.StringList.new([EFFORT_LABELS[e] for e in agent_options.EFFORT_LEVELS]),
        )
        self._effort.set_selected(agent_options.EFFORT_LEVELS.index(agent_options.effort(config)))
        self._effort.connect("notify::selected", self._on_effort_changed)
        reasoning.add(self._effort)

        self._thinking = Adw.ComboRow(
            title="Thinking",
            model=Gtk.StringList.new([THINKING_LABELS[t] for t in agent_options.THINKING_MODES]),
        )
        self._thinking.set_selected(agent_options.THINKING_MODES.index(agent_options.thinking(config)))
        self._thinking.connect("notify::selected", self._on_thinking_changed)
        reasoning.add(self._thinking)

        # The one combination current models reject outright. Shown here rather
        # than left to fail at session start, where the API names the parameter
        # but not which of the two controls above to change.
        self._conflict = Adw.ActionRow(title="")
        self._conflict.add_css_class("error")
        self._conflict.set_visible(False)
        reasoning.add(self._conflict)
        self.add(reasoning)

        sessions = Adw.PreferencesGroup(
            title="New sessions",
            description="A registered project's own default_permission_mode "
                        "wins over this. It applies to folders that are not "
                        "projects.",
        )
        self._mode = Adw.ComboRow(
            title="Permission mode",
            model=Gtk.StringList.new(
                [PERMISSION_MODE_LABELS[m] for m in PERMISSION_MODE_ORDER]
            ),
        )
        self._mode.set_selected(PERMISSION_MODE_ORDER.index(default_permission_mode(config)))
        self._mode.connect("notify::selected", self._on_mode_changed)
        sessions.add(self._mode)
        self.add(sessions)

        self._loading = False
        self._refresh_conflict()

    # ------------------------------------------------------------- handlers

    def _save(self, key: str, value) -> None:
        self._config[key] = value
        config_module.save(self._config)

    def _refresh_conflict(self) -> None:
        message = agent_options.conflicts(self._config)
        self._conflict.set_title(message or "")
        self._conflict.set_visible(message is not None)

    def _on_preset_toggled(self, switch, _param) -> None:
        if self._loading:
            return
        self._save("system_prompt_preset", switch.get_active())
        self.emit("toast", "Applies to the next session you start")

    def _on_append_applied(self, entry) -> None:
        if self._loading:
            return
        self._save("system_prompt_append", entry.get_text().strip())
        self.emit("toast", "Applies to the next session you start")

    def _on_stream_toggled(self, switch, _param) -> None:
        if self._loading:
            return
        self._save("stream_partial_text", switch.get_active())
        self.emit("toast", "Applies to the next session you start")

    def _on_forward_toggled(self, switch, _param) -> None:
        if self._loading:
            return
        self._save("forward_subagent_text", switch.get_active())
        self.emit("toast", "Applies to the next session you start")

    # Unlike everything else on this page, these two change what an open
    # transcript draws rather than how the next session runs, so the toast says
    # reload rather than "applies to the next session you start".
    def _on_plan_usage_toggled(self, switch, _param) -> None:
        if self._loading:
            return
        self._save("show_plan_usage", switch.get_active())
        self.emit("toast", "Reload a transcript to see the change")

    def _on_cost_toggled(self, switch, _param) -> None:
        if self._loading:
            return
        self._save("show_cost", switch.get_active())
        self.emit("toast", "Reload a transcript to see the change")

    def _on_cache_tokens_toggled(self, switch, _param) -> None:
        if self._loading:
            return
        self._save("show_cache_tokens", switch.get_active())
        self.emit("toast", "Reload a transcript to see the change")

    def _on_model_applied(self, entry) -> None:
        if self._loading:
            return
        name = entry.get_text().strip()
        self._save("model", name)
        self.emit("toast", f"New sessions will use {name}" if name
                  else "New sessions will use the CLI's default model")

    def _on_effort_changed(self, combo, _param) -> None:
        if self._loading:
            return
        self._save("effort", agent_options.EFFORT_LEVELS[combo.get_selected()])
        self._refresh_conflict()

    def _on_thinking_changed(self, combo, _param) -> None:
        if self._loading:
            return
        self._save("thinking", agent_options.THINKING_MODES[combo.get_selected()])
        self._refresh_conflict()

    def _on_mode_changed(self, combo, _param) -> None:
        if self._loading:
            return
        self._save("default_permission_mode", PERMISSION_MODE_ORDER[combo.get_selected()])


class AboutPage(Adw.PreferencesPage):
    """Version, application id, and where the files are.

    These are the questions asked when something is wrong, and hunting for the
    config path in a docstring is a poor answer.
    """

    def __init__(self, config: dict):
        super().__init__()

        group = Adw.PreferencesGroup(title="Codinian")
        group.add(_value_row("Version", __version__))
        group.add(_value_row("Application id", config_module.APP_ID))
        self.add(group)

        files = Adw.PreferencesGroup(
            title="Files",
            description="Selectable, so a path can be copied into a terminal.",
        )
        files.add(_value_row("Settings", str(config_module.CONFIG_PATH)))
        files.add(_value_row("Projects", str(project.REGISTRY_PATH)))
        files.add(_value_row("Sessions database", str(db.DB_PATH)))
        self.add(files)


def _value_row(title: str, value: str) -> Adw.ActionRow:
    return Adw.ActionRow(title=title, subtitle=value, subtitle_selectable=True)


def notifications_enabled(config: dict) -> bool:
    """Desktop notifications, on unless turned off. Absent from a config file
    written before ISSUE-030, which should keep the behaviour it had."""
    return config.get("notifications", True) is not False


class SettingsView(Gtk.Box):
    """The whole pane: a tab strip over an Adw.ViewStack.

    Signals are forwarded from the pages rather than re-emitted per page, so the
    window connects once and does not care which tab raised what.
    """

    __gsignals__ = {
        "toast": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "token-rotated": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "theme-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, config: dict):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        from remote_panel import RemoteAccessPage

        self._stack = Adw.ViewStack()

        general = GeneralPage(config)
        general.connect("toast", lambda _p, m: self.emit("toast", m))
        general.connect("theme-changed", lambda _p: self.emit("theme-changed"))
        self._stack.add_titled(general, "general", "General")

        claude = ClaudePage(config)
        claude.connect("toast", lambda _p, m: self.emit("toast", m))
        self._stack.add_titled(claude, "claude", "Claude")

        remote = RemoteAccessPage(config)
        remote.connect("toast", lambda _p, m: self.emit("toast", m))
        remote.connect("token-rotated", lambda _p: self.emit("token-rotated"))
        self._stack.add_titled(remote, "remote", "Remote access")
        self._remote = remote

        self._stack.add_titled(AboutPage(config), "about", "About")

        # Adw.InlineViewSwitcher (libadwaita 1.7+) is the flat underlined strip,
        # which is what project.css draws for Files / Issues / Git / Sessions.
        # The pill-shaped Adw.ViewSwitcher would not match.
        switcher = Adw.InlineViewSwitcher(stack=self._stack)
        switcher.set_halign(Gtk.Align.CENTER)
        switcher.set_margin_top(12)
        switcher.set_margin_bottom(6)

        self.append(switcher)
        self.append(self._stack)
        self._stack.set_vexpand(True)

    def show_tab(self, name: str) -> None:
        if self._stack.get_child_by_name(name) is not None:
            self._stack.set_visible_child_name(name)

    def refresh_remote(self) -> None:
        """Re-read what the Remote access tab shows. The tailnet link comes from
        `tailscale serve status` behind a short cache, so a user who sets up
        serving while the app is open sees it on the next visit rather than only
        after a restart."""
        self._remote.refresh()
