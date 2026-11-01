"""Tests des séries temporelles et du simulateur de conduite."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from obdtool.core.demo_sim import CYCLE_S, DrivingSimulator
from obdtool.core.samples import Sample, SampleBatch
from obdtool.core.timeseries import TimeSeries, TimeSeriesStore


def test_ring_buffer_wraps() -> None:
	series = TimeSeries(capacity=5)
	for i in range(8):
		series.append(float(i), float(i * 10))
	t, v = series.arrays()
	assert list(t) == [3, 4, 5, 6, 7]
	assert list(v) == [30, 40, 50, 60, 70]
	assert series.last_time == 7.0
	t, _ = series.window(4.5, 5.5)
	assert list(t) == [4, 5, 6]


def test_value_at_interpolates_and_respects_gaps() -> None:
	series = TimeSeries()
	for t, v in ((0, 0), (1, 10), (2, 20), (20, 0)):
		series.append(float(t), float(v))
	assert series.value_at(0.5) == pytest.approx(5.0)
	assert series.value_at(2.0) == pytest.approx(20.0)
	assert series.value_at(10.0) is None  # trou de 18 s
	assert series.value_at(-1.0) is None


def test_store_ignores_text_and_errors(tmp_path: Path) -> None:
	store = TimeSeriesStore()
	store.add_batch(
		SampleBatch(
			[
				Sample("RPM", 100.0, 800.0),
				Sample("RPM", 101.0, 900.0),
				Sample("SPEED", 100.5, 10.0),
				Sample("FUEL_STATUS", 100.0, "Boucle fermée"),
				Sample("MAF", 100.0, None, "pas de réponse"),
			]
		)
	)
	assert set(store.keys()) == {"RPM", "SPEED"}
	assert store.time_range() == (100.0, 101.0)
	path = tmp_path / "export.csv"
	rows = store.export_csv(path, ["RPM", "SPEED", "ABSENT"], 100.0, 101.0)
	assert rows == 3
	with path.open(encoding="utf-8") as fh:
		lines = list(csv.reader(fh, delimiter=";"))
	assert lines[0] == ["horodatage", "heure", "RPM", "SPEED"]
	assert lines[2][2] == "850,0000"  # interpolé à t = 100,5
	assert lines[1][3] == ""  # SPEED pas encore mesurée à t = 100
	assert store.export_csv(tmp_path / "pas.csv", ["RPM"], 100.0, 101.0, step=0.25) == 5


def test_simulator_is_continuous_and_coherent() -> None:
	sim = DrivingSimulator(start=0.0)
	speeds, rpms = [], []
	for i in range(int(CYCLE_S * 10)):
		state = sim.state(i / 10)
		speeds.append(state.speed)
		rpms.append(state.rpm)
		if state.gear == 0:
			assert state.speed < 3.5
		assert 0 <= state.load <= 100
		assert 0 <= state.o2_up <= 1.0
	assert max(speeds) > 100
	assert np.max(np.abs(np.diff(speeds))) < 1.5  # pas de saut de vitesse
	assert max(rpms) < 4500
	assert sim.pid_bytes(0x0C, CYCLE_S * 10 + 1) is not None
	assert sim.pid_bytes(0x99) is None
