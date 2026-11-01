"""Historique local des lectures de codes défaut (SQLite), pour suivre la réapparition d'un défaut."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from obdtool.core import paths

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	timestamp REAL NOT NULL,
	event TEXT NOT NULL DEFAULT 'read',
	vin TEXT NOT NULL DEFAULT '',
	mil INTEGER,
	distance_since_clear REAL,
	distance_with_mil REAL,
	odometer REAL,
	stored TEXT NOT NULL DEFAULT '[]',
	pending TEXT NOT NULL DEFAULT '[]',
	permanent TEXT NOT NULL DEFAULT '[]',
	note TEXT NOT NULL DEFAULT ''
);
"""


@dataclass
class HistoryRecord:
	"""Une lecture (ou un effacement) enregistrée."""

	timestamp: float
	event: str = "read"  # read | clear
	vin: str = ""
	mil: bool | None = None
	distance_since_clear: float | None = None
	distance_with_mil: float | None = None
	odometer: float | None = None
	stored: list[str] = field(default_factory=list)
	pending: list[str] = field(default_factory=list)
	permanent: list[str] = field(default_factory=list)
	note: str = ""
	id: int | None = None


class DtcHistory:
	"""Accès à la base d'historique."""

	def __init__(self, path: Path | None = None) -> None:
		self.path = path or paths.data_dir() / "dtc_history.sqlite"
		self.path.parent.mkdir(parents=True, exist_ok=True)
		with self._connect() as db:
			db.executescript(_SCHEMA)

	def _connect(self) -> sqlite3.Connection:
		return sqlite3.connect(self.path)

	def add(self, record: HistoryRecord) -> int:
		"""Ajoute un enregistrement et retourne son identifiant."""
		with self._connect() as db:
			cursor = db.execute(
				"INSERT INTO readings (timestamp, event, vin, mil, distance_since_clear, distance_with_mil, odometer,"
				" stored, pending, permanent, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
				(
					record.timestamp or time.time(),
					record.event,
					record.vin,
					None if record.mil is None else int(record.mil),
					record.distance_since_clear,
					record.distance_with_mil,
					record.odometer,
					json.dumps(record.stored),
					json.dumps(record.pending),
					json.dumps(record.permanent),
					record.note,
				),
			)
			return int(cursor.lastrowid or 0)

	def records(self, vin: str | None = None, limit: int = 500) -> list[HistoryRecord]:
		"""Enregistrements du plus récent au plus ancien (filtrés par VIN si fourni)."""
		query = "SELECT id, timestamp, event, vin, mil, distance_since_clear, distance_with_mil, odometer, stored,"
		query += " pending, permanent, note FROM readings"
		params: tuple = ()
		if vin:
			query += " WHERE vin = ?"
			params = (vin,)
		query += " ORDER BY timestamp DESC, id DESC LIMIT ?"
		with self._connect() as db:
			rows = db.execute(query, (*params, limit)).fetchall()
		return [
			HistoryRecord(
				id=row[0],
				timestamp=row[1],
				event=row[2],
				vin=row[3],
				mil=None if row[4] is None else bool(row[4]),
				distance_since_clear=row[5],
				distance_with_mil=row[6],
				odometer=row[7],
				stored=json.loads(row[8]),
				pending=json.loads(row[9]),
				permanent=json.loads(row[10]),
				note=row[11],
			)
			for row in rows
		]

	def delete(self, record_id: int) -> None:
		with self._connect() as db:
			db.execute("DELETE FROM readings WHERE id = ?", (record_id,))

	def clear_all(self) -> None:
		with self._connect() as db:
			db.execute("DELETE FROM readings")

	def recurring_codes(self, vin: str | None = None) -> dict[str, int]:
		"""Codes revenus après au moins un effacement : code → nombre d'effacements suivis d'une réapparition."""
		records = list(reversed(self.records(vin)))
		counts: dict[str, int] = {}
		cleared: set[str] = set()
		for record in records:
			codes = set(record.stored) | set(record.pending) | set(record.permanent)
			if record.event == "clear":
				cleared |= set(record.stored) | set(record.pending)
				continue
			for code in codes & cleared:
				counts[code] = counts.get(code, 0) + 1
			cleared -= codes
		return counts
