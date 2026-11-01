"""Tests de l'enregistrement, de la liste et de la relecture des sessions."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from obdtool.core import sessions
from obdtool.core.samples import Sample, SampleBatch


def _record(directory: Path, name: str | None = None) -> Path:
	recorder = sessions.SessionRecorder(directory)
	path = recorder.start({"vehicle": "Clio III 1.6 16v", "protocol": "CAN 11/500", "keys": ["RPM", "SPEED"]}, name)
	assert recorder.active
	for i in range(11):
		t = 1000.0 + i
		recorder.write(SampleBatch([Sample("RPM", t, 800.0 + 100 * i), Sample("SPEED", t + 0.5, float(i))]))
	recorder.write(
		SampleBatch([Sample("FUEL_STATUS", 1001.0, "Boucle fermée"), Sample("MAF", 1002.0, None, "pas de réponse")])
	)
	assert recorder.stop() == path
	assert not recorder.active
	return path


def test_record_and_info(tmp_path: Path) -> None:
	path = _record(tmp_path)
	text = path.read_text(encoding="utf-8")
	assert text.startswith("# Session OBDTool")
	assert "# vehicle : Clio III 1.6 16v" in text
	info = sessions.session_info(path)
	assert info.metadata["protocol"] == "CAN 11/500"
	assert info.keys == ["RPM", "SPEED"]
	assert (info.start == 1000.0 and info.end == 1002.0) or info.end == pytest.approx(1010.5)
	assert [i.path for i in sessions.list_sessions(tmp_path)] == [path]


def test_load_samples_types(tmp_path: Path) -> None:
	metadata, samples = sessions.load_samples(_record(tmp_path))
	assert metadata["vehicle"].startswith("Clio")
	assert samples == sorted(samples, key=lambda s: s.t)
	fuel = next(s for s in samples if s.key == "FUEL_STATUS")
	assert fuel.value == "Boucle fermée"
	maf = next(s for s in samples if s.key == "MAF")
	assert maf.value is None and maf.error == "pas de réponse"
	assert next(s for s in samples if s.key == "RPM").value == 800.0


def test_rename_delete_and_names(tmp_path: Path) -> None:
	path = _record(tmp_path, "Essai route/autoroute")
	assert path.name == "Essai route_autoroute.csv"
	other = _record(tmp_path, "Essai route/autoroute")
	assert other.name == "Essai route_autoroute_1.csv"
	renamed = sessions.rename_session(path, "Trajet du matin")
	assert renamed.name == "Trajet du matin.csv" and renamed.exists()
	with pytest.raises(FileExistsError):
		sessions.rename_session(other, "Trajet du matin")
	sessions.delete_session(renamed)
	assert not renamed.exists()
	assert sessions.sanitize_name("../../etc") == "etc"


def test_replay_speed_and_seek(tmp_path: Path) -> None:
	replay = sessions.SessionReplay(_record(tmp_path))
	assert replay.duration == pytest.approx(10.5)
	batch = replay.advance(1.0)
	assert {s.key for s in batch.samples} >= {"RPM"} and batch.source == "replay"
	replay.speed = 4.0
	batch = replay.advance(1.0)
	assert all(1001.0 < s.t <= 1005.0 for s in batch.samples)
	replay.seek(1009.0)
	rest = replay.advance(100.0)
	assert all(s.t >= 1009.0 for s in rest.samples)
	assert replay.finished
	assert len(replay.history_until(1002.0).samples) > 0


def test_export_wide(tmp_path: Path) -> None:
	target = tmp_path / "large.csv"
	rows = sessions.export_wide(_record(tmp_path), target)
	assert rows > 10
	with target.open(encoding="utf-8") as fh:
		header = next(csv.reader(fh, delimiter=";"))
	assert header[:2] == ["horodatage", "heure"] and "RPM" in header and "SPEED" in header
