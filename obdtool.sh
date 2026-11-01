#!/bin/sh
# Lance OBDTool depuis le dossier du projet (environnement virtuel .venv).
# Options : --demo (émulateur), --debug (journal détaillé).
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$DIR/.venv/bin/python" ]; then
	echo "Environnement virtuel absent : python3 -m venv .venv && .venv/bin/pip install -e '.[demo]'" >&2
	exit 1
fi
exec "$DIR/.venv/bin/python" -m obdtool "$@"
