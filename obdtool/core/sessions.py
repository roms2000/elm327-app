"""Enregistrement, liste et relecture des sessions (CSV avec métadonnées en en-tête).

Format : lignes d'en-tête commençant par `#` (dont `# obdtool-session: {json}`), puis un CSV « long »
séparé par des points-virgules : horodatage Unix ; clé ; valeur ; unité ; erreur.
"""

from __future__ import annotations

import bisect
import csv
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from obdtool import __version__
from obdtool.core import paths, pids
from obdtool.core.samples import Sample, SampleBatch
from obdtool.core.timeseries import TimeSeriesStore

log = logging.getLogger(__name__)

HEADER_TAG = "# obdtool-session: "
COLUMNS = ["horodatage", "cle", "valeur", "unite", "erreur"]
_SAFE_NAME = re.compile(r"[^\w\- .àâäéèêëîïôöùûüç]+", re.IGNORECASE)


@dataclass
class SessionInfo:
	"""Résumé d'une session enregistrée."""

	path: Path
	metadata: dict[str, Any] = field(default_factory=dict)
	start: float | None = None
	end: float | None = None
	size: int = 0

	@property
	def name(self) -> str:
		return self.path.stem

	@property
	def duration(self) -> float:
		return (self.end - self.start) if self.start is not None and self.end is not None else 0.0

	@property
	def keys(self) -> list[str]:
		return list(self.metadata.get("keys", []))


class SessionRecorder:
	"""Écrit les lots reçus dans un fichier de session."""

	def __init__(self, directory: Path | None = None) -> None:
		self.directory = directory or paths.sessions_dir()
		self.path: Path | None = None
		self._file: TextIO | None = None
		self._writer: Any = None
		self.samples = 0
		self.started: float | None = None

	@property
	def active(self) -> bool:
		return self._file is not None

	def start(self, metadata: dict[str, Any], name: str | None = None) -> Path:
		"""Ouvre un nouveau fichier et écrit l'en-tête."""
		self.stop()
		self.directory.mkdir(parents=True, exist_ok=True)
		now = datetime.now()
		stem = sanitize_name(name) if name else f"session_{now:%Y%m%d_%H%M%S}"
		path = self.directory / f"{stem}.csv"
		suffix = 1
		while path.exists():
			path = self.directory / f"{stem}_{suffix}.csv"
			suffix += 1
		meta = {"version": __version__, "created": now.isoformat(timespec="seconds"), "units": "metric", **metadata}
		self._file = path.open("w", encoding="utf-8", newline="")
		self._file.write(f"# Session OBDTool du {now:%d/%m/%Y %H:%M:%S}\n")
		for label in ("vehicle", "protocol", "adapter", "vin"):
			if meta.get(label):
				self._file.write(f"# {label} : {meta[label]}\n")
		self._file.write(HEADER_TAG + json.dumps(meta, ensure_ascii=False) + "\n")
		self._writer = csv.writer(self._file, delimiter=";")
		self._writer.writerow(COLUMNS)
		self.path = path
		self.samples = 0
		self.started = now.timestamp()
		log.info("Enregistrement démarré : %s", path)
		return path

	def write(self, batch: SampleBatch) -> None:
		"""Ajoute un lot (ignoré si aucun enregistrement n'est en cours)."""
		if self._writer is None:
			return
		for sample in batch.samples:
			definition = pids.get(sample.key)
			value = (
				""
				if sample.value is None
				else (f"{sample.value:.4f}" if isinstance(sample.value, float) else str(sample.value))
			)
			self._writer.writerow(
				[f"{sample.t:.3f}", sample.key, value, definition.unit if definition else "", sample.error]
			)
			self.samples += 1

	def flush(self) -> None:
		if self._file is not None:
			self._file.flush()

	def stop(self) -> Path | None:
		"""Ferme le fichier ; retourne son chemin."""
		if self._file is None:
			return None
		path = self.path
		try:
			self._file.close()
		except OSError as exc:
			log.error("Fermeture de la session : %s", exc)
		self._file = None
		self._writer = None
		log.info("Enregistrement arrêté : %s (%d échantillons)", path, self.samples)
		return path


def sanitize_name(name: str) -> str:
	"""Nom de fichier sûr à partir d'un nom saisi par l'utilisateur."""
	cleaned = _SAFE_NAME.sub("_", name.strip()).strip(" ._")
	return cleaned[:80] or "session"


def _read_header(fh: TextIO) -> dict[str, Any]:
	metadata: dict[str, Any] = {}
	position = fh.tell()
	line = fh.readline()
	while line.startswith("#"):
		if line.startswith(HEADER_TAG):
			try:
				metadata = json.loads(line[len(HEADER_TAG) :])
			except json.JSONDecodeError:
				log.warning("Métadonnées de session illisibles")
		position = fh.tell()
		line = fh.readline()
	fh.seek(position)
	return metadata


def _last_timestamp(path: Path) -> float | None:
	try:
		with path.open("rb") as fh:
			fh.seek(0, os.SEEK_END)
			size = fh.tell()
			fh.seek(max(0, size - 4096))
			tail = fh.read().decode("utf-8", errors="ignore").splitlines()
	except OSError:
		return None
	stamps = []
	for line in tail:
		try:
			stamps.append(float(line.split(";", 1)[0]))
		except ValueError:
			continue
	return max(stamps) if stamps else None


def session_info(path: Path) -> SessionInfo:
	"""Lit l'en-tête et les bornes temporelles d'une session sans charger les données."""
	info = SessionInfo(path)
	try:
		info.size = path.stat().st_size
		with path.open(encoding="utf-8") as fh:
			info.metadata = _read_header(fh)
			fh.readline()  # ligne des colonnes
			first = fh.readline().split(";", 1)[0]
			info.start = float(first) if first else None
	except (OSError, ValueError):
		return info
	info.end = _last_timestamp(path)
	return info


def list_sessions(directory: Path | None = None) -> list[SessionInfo]:
	"""Sessions enregistrées, de la plus récente à la plus ancienne."""
	directory = directory or paths.sessions_dir()
	if not directory.is_dir():
		return []
	infos = [session_info(p) for p in directory.glob("*.csv")]
	infos.sort(key=lambda i: i.start or i.path.stat().st_mtime, reverse=True)
	return infos


def rename_session(path: Path, new_name: str) -> Path:
	"""Renomme une session ; lève FileExistsError si le nom est pris."""
	target = path.with_name(sanitize_name(new_name) + ".csv")
	if target == path:
		return path
	if target.exists():
		raise FileExistsError(f"Une session nommée « {target.stem} » existe déjà.")
	path.rename(target)
	return target


def delete_session(path: Path) -> None:
	path.unlink(missing_ok=True)


def load_samples(path: Path) -> tuple[dict[str, Any], list[Sample]]:
	"""Charge toutes les mesures d'une session, triées par horodatage."""
	samples: list[Sample] = []
	with path.open(encoding="utf-8") as fh:
		metadata = _read_header(fh)
		reader = csv.reader(fh, delimiter=";")
		header = next(reader, None)
		if header is None:
			return metadata, samples
		for row in reader:
			if len(row) < 3:
				continue
			try:
				t = float(row[0])
			except ValueError:
				continue
			raw = row[2]
			value: float | str | None
			if raw == "":
				value = None
			else:
				try:
					value = float(raw)
				except ValueError:
					value = raw
			samples.append(Sample(row[1], t, value, row[4] if len(row) > 4 else ""))
	samples.sort(key=lambda s: s.t)
	return metadata, samples


def export_wide(path: Path, target: Path, step: float | None = None) -> int:
	"""Exporte une session en CSV « large » (une colonne par valeur) pour les tableurs."""
	_meta, samples = load_samples(path)
	store = TimeSeriesStore(capacity=max(len(samples), 1))
	store.add_batch(SampleBatch(samples))
	bounds = store.time_range()
	if bounds is None:
		target.write_text("", encoding="utf-8")
		return 0
	return store.export_csv(target, store.keys(), bounds[0], bounds[1], step=step)


class SessionReplay:
	"""Relecture d'une session à vitesse réglable (pilotée par des appels réguliers à `advance`)."""

	def __init__(self, path: Path) -> None:
		self.path = path
		self.metadata, self.samples = load_samples(path)
		self._times = [s.t for s in self.samples]
		self.start = self._times[0] if self._times else 0.0
		self.end = self._times[-1] if self._times else 0.0
		self.position = self.start
		self._index = 0
		self.speed = 1.0

	@property
	def duration(self) -> float:
		return self.end - self.start

	@property
	def finished(self) -> bool:
		return self._index >= len(self.samples)

	def seek(self, t: float) -> None:
		"""Place la tête de lecture à l'instant t."""
		self.position = min(max(t, self.start), self.end)
		self._index = bisect.bisect_left(self._times, self.position)

	def advance(self, wall_dt: float) -> SampleBatch:
		"""Avance de `wall_dt` secondes réelles (× vitesse) et retourne les mesures franchies."""
		self.position = min(self.position + wall_dt * self.speed, self.end)
		stop = bisect.bisect_right(self._times, self.position, lo=self._index)
		batch = SampleBatch(self.samples[self._index : stop], source="replay")
		self._index = stop
		return batch

	def history_until(self, t: float) -> SampleBatch:
		"""Mesures depuis le début jusqu'à t (pour remplir les graphiques après un déplacement)."""
		stop = bisect.bisect_right(self._times, t)
		return SampleBatch(self.samples[:stop], source="replay")
