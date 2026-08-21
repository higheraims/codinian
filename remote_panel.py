"""The Remote access page (ISSUE-007), a tab in Settings since ISSUE-030.

Shows the URL a browser or phone needs, token included, because a token the
user cannot see is a token they cannot use. Also the controls that change what
the server exposes: the LAN opt-in, the Tailscale identity setting, and token
rotation.

The QR code (ISSUE-029) encodes whichever link would actually work from a
phone, which is not always the first one on the page. See `_phone_url`.

This is deliberately native GTK rather than a page the browser client could
render. What it holds is the token and the bind: as a web page, anyone holding
the token could open this machine's port to the whole local network, which is
an escalation that does not exist while the control stays on the machine it
affects (ISSUE-030).
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GObject, Gtk

import config as config_module
import qr
import tailscale


class QrCodeArea(Gtk.DrawingArea):
    """Draws a QR module grid with Cairo.

    Not an image: the grid is drawn as rectangles snapped to whole pixels, so
    the code stays crisp at whatever size the dialog gives it. Scaling a small
    bitmap would blur the module edges, which is the one thing a scanner needs
    sharp.

    Always dark-on-white, in both themes. Scanners expect dark modules on a
    light background, and a QR code inverted for a dark theme fails on some
    readers.
    """

    # 300px gives a 49-module code (a tailnet URL with a 43-character token)
    # six whole pixels per module, which is comfortable to scan off a laptop
    # screen. Smaller codes simply get larger modules.
    def __init__(self, size: int = 300):
        super().__init__()
        self._matrix: list[list[bool]] | None = None
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_matrix(self, matrix: list[list[bool]] | None) -> None:
        self._matrix = matrix
        self.set_visible(matrix is not None)
        self.queue_draw()

    def _draw(self, _area, cr, width, height) -> None:
        matrix = self._matrix
        if not matrix:
            return
        modules = len(matrix)

        # Whole pixels per module, so no module lands on a half pixel and gets
        # drawn as two grey ones. The leftover is spent centring the code.
        scale = max(1, int(min(width, height) // modules))
        drawn = scale * modules
        left = (width - drawn) / 2
        top = (height - drawn) / 2

        # The white ground covers the quiet zone too, which is part of the code
        # rather than decoration around it.
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(left, top, drawn, drawn)
        cr.fill()

        cr.set_source_rgb(0, 0, 0)
        for y, row in enumerate(matrix):
            # Runs of dark modules become one rectangle, which keeps a version
            # 6 code at a few hundred fills instead of a few thousand.
            x = 0
            while x < modules:
                if not row[x]:
                    x += 1
                    continue
                run = x
                while run < modules and row[run]:
                    run += 1
                cr.rectangle(left + x * scale, top + y * scale,
                             (run - x) * scale, scale)
                x = run
        cr.fill()


class RemoteAccessPage(Adw.PreferencesPage):
    """The Remote access tab. An Adw.PreferencesPage scrolls its own groups and
    gives them the standard clamped width, so the code no longer sizes itself
    for a 520px dialog the way ISSUE-029 had to."""

    __gsignals__ = {
        # Emitted after the token changes, so open transcript panes can reload
        # with the new one instead of failing their next request.
        "token-rotated": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # A message for the window's toast overlay. A page inside a stack has
        # nowhere of its own to put one, and a toast belongs to the window in
        # any case: it should survive switching to another tab.
        "toast": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config: dict):
        super().__init__()
        self._config = config

        link_group = Adw.PreferencesGroup(
            title="Open in a browser",
            description="These links carry the access token. Anyone who has one can read "
                        "your sessions and approve tool calls, which runs code on this "
                        "machine. The Tailscale link is the one to use from a phone: it "
                        "is HTTPS and needs no change to the bind.",
        )
        self._url_row = Adw.ActionRow(subtitle_selectable=True)
        self._url_row.set_title("On this machine")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Copy link")
        copy.add_css_class("flat")
        copy.connect("clicked", self._on_copy)
        self._url_row.add_suffix(copy)
        link_group.add(self._url_row)

        # The tailnet link, when `tailscale serve` is proxying this port. It is
        # HTTPS at the machine's MagicDNS name, so it works from a phone without
        # opening the bind, and browser notifications work there because the
        # page is a secure context (ISSUE-022).
        self._tailnet_row = Adw.ActionRow(subtitle_selectable=True)
        self._tailnet_row.set_title("Over Tailscale")
        tailnet_copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                                  tooltip_text="Copy tailnet link")
        tailnet_copy.add_css_class("flat")
        tailnet_copy.connect("clicked", self._on_copy_tailnet)
        self._tailnet_row.add_suffix(tailnet_copy)
        link_group.add(self._tailnet_row)
        self.add(link_group)

        # The QR sits under the links rather than above them: it is the thing
        # you use once, from the phone, and the links are what you come back to.
        self._qr_group = Adw.PreferencesGroup(
            title="Scan from a phone",
            description="Points the phone's browser at the link below, token and all. "
                        "Treat it like the link: a photograph of it is a working "
                        "credential for this machine.",
        )
        qr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._qr_area = QrCodeArea()
        qr_box.append(self._qr_area)
        self._qr_caption = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._qr_caption.add_css_class("dim-label")
        self._qr_caption.add_css_class("caption")
        qr_box.append(self._qr_caption)
        self._qr_group.add(qr_box)
        self.add(self._qr_group)

        network_group = Adw.PreferencesGroup(title="Network")
        self._lan = Adw.SwitchRow(
            title="Allow access from the local network",
            subtitle="Off means only this machine can connect. On exposes the port to "
                     "every device on your network; an SSH tunnel is the safer way in. "
                     "Takes effect when Codinian restarts.",
        )
        self._lan.set_active(config["bind"] != config_module.LOOPBACK)
        self._lan.connect("notify::active", self._on_lan_toggled)
        network_group.add(self._lan)

        # Until ISSUE-030 this key could only be set by editing config.json,
        # which is a poor place for a security decision to hide. The subtitle
        # is the short version of the argument in docs/remote-access.md.
        self._trust_identity = Adw.SwitchRow(
            title="Accept a Tailscale identity instead of the token",
            subtitle="Off is the right answer almost always. A request proxied by "
                     "Tailscale and one forged by a local process both arrive from "
                     "127.0.0.1 and look alike, so this trades a secret only you can "
                     "read for a header any local process can set.",
        )
        self._trust_identity.set_active(bool(config.get("trust_tailscale_identity")))
        self._trust_identity.connect("notify::active", self._on_trust_identity_toggled)
        network_group.add(self._trust_identity)
        self.add(network_group)

        token_group = Adw.PreferencesGroup(
            title="Token",
            description="Rotating the token disconnects every browser using the old link.",
        )
        rotate_row = Adw.ActionRow(title="Rotate token")
        rotate = Gtk.Button(label="Rotate", valign=Gtk.Align.CENTER)
        rotate.add_css_class("destructive-action")
        rotate.connect("clicked", self._on_rotate)
        rotate_row.add_suffix(rotate)
        token_group.add(rotate_row)
        self.add(token_group)

        self._refresh_url()

    def refresh(self) -> None:
        """Re-read everything shown here. The tailnet link comes from
        `tailscale serve status` behind a short cache, so someone who sets up
        serving while the app is open needs a way to pick it up short of a
        restart; the window calls this when the tab is opened."""
        self._refresh_url()

    def _refresh_url(self) -> None:
        self._url_row.set_subtitle(config_module.url_for(self._config))
        self._tailnet_row.set_subtitle(self._tailnet_subtitle())
        self._refresh_qr()

    def _phone_url(self) -> tuple[str | None, str]:
        """The link worth scanning, and a caption saying why it is that one.

        Not simply the first link in the dialog: that one is on 127.0.0.1,
        which resolves to the phone itself and reaches nothing. In order of
        preference, the tailnet link (HTTPS, no change to the bind, and a
        secure context so browser notifications work), then the LAN address if
        the bind has been opened, then nothing scannable.
        """
        tailnet = self._tailnet_url()
        if tailnet:
            return tailnet, "Over Tailscale. Works from anywhere on the tailnet."

        if self._config["bind"] != config_module.LOOPBACK:
            host = config_module.local_ip()
            if host != config_module.LOOPBACK:
                url = f"http://{host}:{self._config['port']}/?token={self._config['token']}"
                return url, ("On the local network. The phone has to be on this "
                             "network, and this link is plain HTTP.")

        return None, ("Nothing to scan yet. The only link right now is on "
                      "127.0.0.1, which on a phone means the phone. Set up "
                      "`tailscale serve`, or allow access from the local "
                      "network below.")

    def _refresh_qr(self) -> None:
        url, caption = self._phone_url()
        matrix = qr.matrix_for(url) if url else None
        if url and matrix is None:
            # A link worth scanning, and no way to draw it.
            caption = ("Install python3-qrcode to show a code here. The link is "
                       "above in the meantime.")
        self._qr_area.set_matrix(matrix)
        self._qr_caption.set_label(caption)

    def _tailnet_url(self) -> str | None:
        target = tailscale.serve_target(self._config.get("port", 8787))
        if not target:
            return None
        return f"{target['url'].rstrip('/')}/?token={self._config['token']}"

    def _tailnet_subtitle(self) -> str:
        url = self._tailnet_url()
        if url:
            return url
        state = tailscale.status()
        if not state.get("running"):
            return "Tailscale is not running on this machine."
        port = self._config.get("port", 8787)
        return (f"Not serving yet. Run: tailscale serve --bg --https=443 {port}")

    def _on_copy(self, _button) -> None:
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(config_module.url_for(self._config))
        self._toast("Link copied")

    def _on_copy_tailnet(self, _button) -> None:
        url = self._tailnet_url()
        if not url:
            self._toast("No tailnet link to copy yet")
            return
        Gdk.Display.get_default().get_clipboard().set(url)
        self._toast("Tailnet link copied")

    def _on_lan_toggled(self, switch, _param) -> None:
        self._config["bind"] = (config_module.ALL_INTERFACES if switch.get_active()
                                else config_module.LOOPBACK)
        config_module.save(self._config)
        self._refresh_url()
        self._toast("Restart Codinian to apply the new bind")

    def _toast(self, message: str) -> None:
        self.emit("toast", message)

    def _on_trust_identity_toggled(self, switch, _param) -> None:
        self._config["trust_tailscale_identity"] = switch.get_active()
        config_module.save(self._config)
        # The middleware reads the config dict the server was handed, which is
        # this same object, so the change applies to the next request. Nothing
        # to restart.
        self._toast("Tailscale identity accepted" if switch.get_active()
                    else "Tailscale identity no longer accepted")

    def _on_rotate(self, _button) -> None:
        config_module.rotate_token(self._config)
        self._refresh_url()
        self.emit("token-rotated")
        self._toast("Token rotated")
