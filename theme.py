"""The one theme choice, applied to both halves of the interface (ISSUE-030).

Codinian draws itself twice. The shell is GTK and libadwaita; the transcript and
project panes are WebKitGTK pages styled by `remote/static/styles.css`. They
follow the system independently, so a preference set on one alone produces a
light transcript inside a dark window.

This module owns the choice, writes it to the same config file as the rest of
the global settings, and hands each half what it needs: a colour scheme for
`Adw.StyleManager`, and a `theme=` value for the pane URLs that the pages turn
into a `data-theme` attribute.

Kept free of `gi` imports below `apply_to_shell` so the value mapping can be
checked without a display.
"""

from __future__ import annotations

import config as config_module

# What the user picks. "system" is the default and the only one that leaves both
# halves following the desktop.
THEMES = ("system", "light", "dark")
DEFAULT_THEME = "system"


def normalize(value) -> str:
    """A stored or supplied theme value, or the default if it is not one we
    know. Config files get hand-edited; an unrecognised value should fall back
    rather than propagate into a URL or a StyleManager call."""
    return value if value in THEMES else DEFAULT_THEME


def current(config: dict) -> str:
    return normalize(config.get("theme"))


def save(config: dict, value: str) -> str:
    """Persist the choice and return what was actually stored."""
    value = normalize(value)
    config["theme"] = value
    config_module.save(config)
    return value


def query_value(config: dict) -> str | None:
    """The `theme=` parameter for a pane URL, or None for "system".

    None rather than "system" on purpose: with no parameter the page has no
    `data-theme` attribute and its `prefers-color-scheme` media query decides,
    which is exactly what "system" means. It also keeps the URL of the common
    case unchanged.
    """
    value = current(config)
    return None if value == DEFAULT_THEME else value


def apply_to_shell(config: dict) -> None:
    """Point libadwaita at the stored choice.

    FORCE_LIGHT and FORCE_DARK rather than PREFER_*: the preference is a choice
    the user made here, not a hint to be overridden by what the desktop happens
    to be set to."""
    from gi.repository import Adw

    value = current(config)
    scheme = {
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(value, Adw.ColorScheme.DEFAULT)
    Adw.StyleManager.get_default().set_color_scheme(scheme)
