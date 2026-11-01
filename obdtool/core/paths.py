"""Emplacements des fichiers utilisateur (convention XDG)."""

from __future__ import annotations

import os
from pathlib import Path

from obdtool import APP_NAME


def _xdg(var: str, default: str) -> Path:
	value = os.environ.get(var, "").strip()
	base = Path(value) if value and os.path.isabs(value) else Path.home() / default
	return base / APP_NAME


def config_dir() -> Path:
	"""Dossier de configuration (~/.config/obdtool)."""
	return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
	"""Dossier de données (~/.local/share/obdtool) : sessions, historique."""
	return _xdg("XDG_DATA_HOME", ".local/share")


def log_dir() -> Path:
	"""Dossier des journaux, dans le dossier de configuration."""
	return config_dir() / "logs"


def sessions_dir() -> Path:
	"""Dossier des sessions enregistrées."""
	return data_dir() / "sessions"


def ensure_dirs() -> None:
	"""Crée les dossiers nécessaires s'ils n'existent pas."""
	for path in (config_dir(), data_dir(), log_dir(), sessions_dir()):
		path.mkdir(parents=True, exist_ok=True)
