#!/usr/bin/env bash
# Install the desktop entry and icon for the current user, pointing at this
# checkout. Run it again after moving the checkout; run with --uninstall to
# remove what it installed. The app itself still runs from the checkout --
# nothing is copied but the launcher and the icon.
set -euo pipefail

app_id="net.higheraims.codinian"
here="$(cd "$(dirname "$0")" && pwd)"
checkout="$(dirname "$here")"
apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$apps_dir/$app_id.desktop" "$icon_dir/$app_id.svg"
    echo "Removed $app_id.desktop and its icon."
    exit 0
fi

mkdir -p "$apps_dir" "$icon_dir"
sed "s|@CHECKOUT@|$checkout|" "$here/$app_id.desktop" > "$apps_dir/$app_id.desktop"
cp "$checkout/remote/static/codinian.svg" "$icon_dir/$app_id.svg"
command -v update-desktop-database >/dev/null && update-desktop-database "$apps_dir" || true
echo "Installed $app_id.desktop pointing at $checkout."
