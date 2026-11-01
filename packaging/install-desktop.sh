#!/bin/sh
# Ajoute OBDTool au menu des applications de l'utilisateur (aucun droit administrateur requis).
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cp "$DIR/obdtool/ui/resources/obdtool.svg" "$ICONS/obdtool.svg"
sed "s|^Exec=.*|Exec=\"$DIR/obdtool.sh\"|" "$DIR/packaging/obdtool.desktop" > "$APPS/obdtool.desktop"
chmod +x "$DIR/obdtool.sh"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
echo "OBDTool ajouté au menu des applications ($APPS/obdtool.desktop)."
