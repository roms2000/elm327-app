"""Journalisation : un fichier par session de l'application, avec rotation et purge."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from obdtool.core import paths

LOG_PREFIX = "obdtool_"
LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s : %(message)s"
_SESSION_RE = re.compile(r"^obdtool_(\d{8}_\d{6})_(\d+)\.log(?:\.(\d+))?$")

# Taille d'un fichier avant rotation à l'intérieur d'une même session.
SESSION_FILE_MAX_BYTES = 5 * 1024 * 1024
SESSION_FILE_BACKUPS = 2

_current_file: Path | None = None
_file_handler: logging.Handler | None = None


@dataclass(frozen=True)
class LogSession:
	"""Ensemble des fichiers de journal d'une exécution de l'application."""

	key: str
	started: datetime
	files: tuple[Path, ...]
	size: int
	is_current: bool


def current_log_file() -> Path | None:
	"""Fichier de journal de la session en cours."""
	return _current_file


def setup_logging(
	level: str = "INFO",
	keep_sessions: int = 10,
	max_total_mb: float = 50.0,
	directory: Path | None = None,
	console: bool = True,
) -> Path:
	"""Configure le logging racine et ouvre un nouveau fichier pour cette session."""
	global _current_file, _file_handler
	directory = directory or paths.log_dir()
	directory.mkdir(parents=True, exist_ok=True)
	purge_logs(keep_sessions=max(keep_sessions - 1, 0), max_total_mb=max_total_mb, directory=directory)

	stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
	log_file = directory / f"{LOG_PREFIX}{stamp}_{os.getpid()}.log"
	root = logging.getLogger()
	if _file_handler is not None:
		root.removeHandler(_file_handler)
		_file_handler.close()
	handler = RotatingFileHandler(
		log_file, maxBytes=SESSION_FILE_MAX_BYTES, backupCount=SESSION_FILE_BACKUPS, encoding="utf-8"
	)
	handler.setFormatter(logging.Formatter(LOG_FORMAT))
	root.addHandler(handler)
	if console and not any(type(h) is logging.StreamHandler for h in root.handlers):
		stream = logging.StreamHandler()
		stream.setFormatter(logging.Formatter("%(levelname)s %(name)s : %(message)s"))
		stream.setLevel(logging.WARNING)
		# Les messages de python-OBD restent dans les fichiers ; en console ils feraient doublon avec nos messages.
		stream.addFilter(lambda record: not record.name.startswith("obd"))
		root.addHandler(stream)
	set_level(level)
	_current_file = log_file
	_file_handler = handler
	logging.getLogger(__name__).info("Nouvelle session de journal : %s", log_file.name)
	return log_file


def set_level(level: str) -> None:
	"""Change le niveau de journalisation (DEBUG active aussi les trames brutes de python-OBD)."""
	numeric = getattr(logging, level.upper(), logging.INFO)
	logging.getLogger().setLevel(numeric)
	# python-OBD installe son propre affichage console : on le retire, ses messages passent par nos fichiers.
	obd_logger = logging.getLogger("obd")
	for handler in list(obd_logger.handlers):
		obd_logger.removeHandler(handler)
	obd_logger.propagate = True
	# Ses messages DEBUG contiennent les échanges bruts avec l'adaptateur.
	obd_logger.setLevel(numeric if numeric <= logging.DEBUG else logging.WARNING)


def list_sessions(directory: Path | None = None) -> list[LogSession]:
	"""Liste les sessions de journal, de la plus récente à la plus ancienne."""
	directory = directory or paths.log_dir()
	if not directory.is_dir():
		return []
	groups: dict[str, list[Path]] = {}
	for path in directory.iterdir():
		match = _SESSION_RE.match(path.name)
		if match and path.is_file():
			groups.setdefault(f"{match.group(1)}_{match.group(2)}", []).append(path)
	current_key = _session_key(_current_file) if _current_file else None
	sessions = []
	for key, files in groups.items():
		files.sort(key=lambda p: p.name)
		try:
			started = datetime.strptime(key[:15], "%Y%m%d_%H%M%S")
		except ValueError:
			continue
		size = sum(_safe_size(p) for p in files)
		sessions.append(LogSession(key, started, tuple(files), size, key == current_key))
	sessions.sort(key=lambda s: (s.started, s.key), reverse=True)
	return sessions


def total_size(directory: Path | None = None) -> int:
	"""Taille totale des journaux, en octets."""
	return sum(s.size for s in list_sessions(directory))


def delete_sessions(sessions: list[LogSession]) -> int:
	"""Supprime les sessions données (jamais la session en cours). Retourne le nombre de fichiers supprimés."""
	removed = 0
	for session in sessions:
		if session.is_current:
			continue
		for path in session.files:
			try:
				path.unlink()
				removed += 1
			except OSError as exc:
				logging.getLogger(__name__).warning("Suppression impossible de %s : %s", path, exc)
	return removed


def delete_old_logs(directory: Path | None = None) -> int:
	"""Supprime tous les journaux sauf ceux de la session en cours."""
	return delete_sessions([s for s in list_sessions(directory) if not s.is_current])


def purge_logs(keep_sessions: int, max_total_mb: float, directory: Path | None = None) -> int:
	"""Supprime les sessions les plus anciennes au-delà du nombre ou de la taille autorisés."""
	sessions = [s for s in list_sessions(directory) if not s.is_current]
	budget = int(max_total_mb * 1024 * 1024)
	kept_size = 0
	to_delete = []
	for index, session in enumerate(sessions):
		if index >= keep_sessions or kept_size + session.size > budget:
			to_delete.append(session)
		else:
			kept_size += session.size
	return delete_sessions(to_delete)


def read_log(path: Path, max_bytes: int = 2 * 1024 * 1024) -> str:
	"""Lit la fin d'un fichier de journal (au plus `max_bytes`)."""
	try:
		with path.open("rb") as fh:
			fh.seek(0, os.SEEK_END)
			size = fh.tell()
			fh.seek(max(size - max_bytes, 0))
			data = fh.read()
	except OSError as exc:
		return f"Impossible de lire {path} : {exc}"
	text = data.decode("utf-8", errors="replace")
	if size > max_bytes:
		text = "[… début tronqué …]\n" + text.split("\n", 1)[-1]
	return text


def _session_key(path: Path) -> str | None:
	match = _SESSION_RE.match(path.name)
	return f"{match.group(1)}_{match.group(2)}" if match else None


def _safe_size(path: Path) -> int:
	try:
		return path.stat().st_size
	except OSError:
		return 0


def format_size(size: int) -> str:
	"""Formate une taille en octets de façon lisible."""
	value = float(size)
	for unit in ("o", "Ko", "Mo", "Go"):
		if value < 1024 or unit == "Go":
			return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
		value /= 1024
	return f"{size} o"
