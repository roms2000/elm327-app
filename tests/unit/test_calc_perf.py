"""Tests des valeurs calculées, du profil véhicule et des chronomètres."""

from __future__ import annotations

from pathlib import Path

import pytest

from obdtool.core import calc, profile
from obdtool.core.demo_sim import CYCLE_S, DrivingSimulator
from obdtool.core.perf import PerfHistory, PerformanceTracker, PerfResult
from obdtool.core.samples import Sample, SampleBatch

CLIO = profile.VehicleProfile()


def test_speed_density_k4m() -> None:
	idle = calc.airflow_speed_density(30, 30, 800, 1.598, 0.85)
	full = calc.airflow_speed_density(100, 25, 6000, 1.598, 0.85)
	assert 2.5 < idle < 3.6
	assert 75 < full < 85
	assert calc.airflow_speed_density(30, 30, 0, 1.598, 0.85) == 0.0


def test_fuel_and_economy() -> None:
	rate = calc.fuel_rate_lph(3.2, 14.7, 0.745)
	assert 0.9 < rate < 1.2
	assert calc.fuel_rate_lph(10, 14.7, 0.745, correction_pct=10) == pytest.approx(
		calc.fuel_rate_lph(10, 14.7, 0.745) * 1.1
	)
	assert calc.economy_l100(6.0, 100.0) == pytest.approx(6.0)
	assert calc.economy_l100(1.0, 1.0) is None


def test_gear_estimation() -> None:
	circumference = CLIO.tyre_circumference_m()
	assert 1.80 < circumference < 1.90
	for gear, ratio in enumerate(CLIO.gear_ratios, start=1):
		speed = 50.0
		rpm = speed / 3.6 / circumference * 60 * ratio * CLIO.final_drive
		assert calc.estimate_gear(rpm, speed, CLIO) == gear
	assert calc.estimate_gear(800, 50, CLIO) is None  # débrayé
	assert calc.estimate_gear(3000, 2, CLIO) is None


def test_road_power() -> None:
	cruise = calc.road_power_kw(120, 0.0, CLIO)
	assert 15 < cruise < 25
	assert calc.road_power_kw(50, 2.0, CLIO) > calc.road_power_kw(50, 0.0, CLIO)


def test_profile_roundtrip(isolated_dirs: Path) -> None:
	profiles = profile.list_profiles()
	assert profiles[0].name.startswith("Renault Clio III")
	custom = profile.VehicleProfile(name="Test / essai", fuel="e85", gear_ratios=[3.5, 2.0])
	profile.save_profile(custom)
	loaded = profile.find_profile("Test / essai")
	assert loaded.afr == pytest.approx(9.8) and loaded.gear_ratios == [3.5, 2.0]
	profile.delete_profile(loaded)
	assert all(p.name != "Test / essai" for p in profile.list_profiles())
	assert profile.tyre_circumference_m("illisible") == 1.87
	bad = profile.VehicleProfile.from_dict({"mass_kg": "lourd", "year": 2005})
	assert bad.mass_kg == 1150.0 and bad.year == 2005


def _speed_batches(points: list[tuple[float, float]]) -> list[SampleBatch]:
	return [SampleBatch([Sample("SPEED", t, v)]) for t, v in points]


def test_zero_to_hundred_interpolated() -> None:
	tracker = PerformanceTracker()
	points = [(0.0, 0.0), (0.5, 0.0)] + [(1.0 + i * 0.5, i * 5.0) for i in range(1, 23)]
	results = [r for b in _speed_batches(points) for r in tracker.process(b)]
	keys = {r.test: r.seconds for r in results}
	# Départ au dernier point arrêté (t = 0,5), 50 km/h à t = 6,0, 100 km/h à t = 11,0.
	assert keys["0-50"] == pytest.approx(5.5)
	assert keys["0-100"] == pytest.approx(10.5)
	assert "80-120" not in keys


def test_rolling_test_aborts_on_deceleration() -> None:
	tracker = PerformanceTracker()
	# Monte à 105, redescend à 90 (abandon), passe sous 77, puis vraie reprise 75 → 125.
	ramp = [(i * 0.5, min(i * 4.0, 105.0)) for i in range(40)]
	down = [(20 + i * 0.5, 105 - i * 3.0) for i in range(11)]
	reprise = [(30 + i * 0.5, 75 + i * 3.5) for i in range(16)]
	results = [r for b in _speed_batches(ramp + down + reprise) for r in tracker.process(b)]
	rolling = [r for r in results if r.test == "80-120"]
	assert len(rolling) == 1
	assert rolling[0].seconds == pytest.approx((120 - 80) / 3.5 * 0.5, abs=0.01)


def test_simulated_cycle_gives_plausible_times() -> None:
	sim = DrivingSimulator(start=0.0)
	tracker = PerformanceTracker()
	results = []
	for i in range(int(CYCLE_S * 20)):
		t = i / 20
		results += tracker.process(SampleBatch([Sample("SPEED", t, sim.state(t).speed)]))
	times = {r.test: r.seconds for r in results}
	assert 5 < times["0-50"] < 9
	assert 11 < times["0-100"] < 16
	assert 5 < times["80-120"] < 8


def test_derived_engine_speed_density_and_totals() -> None:
	engine = calc.DerivedEngine(CLIO)
	out = []
	for i in range(11):
		t = 100.0 + i
		batch = SampleBatch(
			[
				Sample("RPM", t, 3000.0),
				Sample("SPEED", t, 72.0),
				Sample("INTAKE_PRESSURE", t, 50.0),
				Sample("INTAKE_TEMP", t, 25.0),
				Sample("THROTTLE_POS", t, 20.0),
			]
		)
		out = engine.process(batch)
	values = {s.key: s.value for s in out}
	assert values["AIRFLOW_DERIVED"] == pytest.approx(calc.airflow_speed_density(50, 25, 3000, 1.598, 0.85), rel=1e-3)
	assert 2 < values["FUEL_ECONOMY_DERIVED"] < 10
	assert values["DISTANCE_DERIVED"] == pytest.approx(0.2, rel=1e-3)  # 72 km/h pendant 10 s
	assert values["GEAR_DERIVED"] == 4.0
	# Coupure d'injection : pied levé à haut régime.
	cut = engine.process(
		SampleBatch([Sample("THROTTLE_POS", 112.0, 0.0), Sample("RPM", 112.0, 2500.0), Sample("SPEED", 112.0, 72.0)])
	)
	assert {s.key: s.value for s in cut}["FUEL_RATE_DERIVED"] == 0.0


def test_perf_history(tmp_path: Path) -> None:
	history = PerfHistory(tmp_path / "perf.json")
	history.add(PerfResult("0-100", 12.0, 1.0))
	history.add(PerfResult("0-100", 11.5, 2.0))
	history.add(PerfResult("0-100", 9.0, 3.0, source="replay"))
	assert history.best("0-100").seconds == 11.5
	history.clear()
	assert history.load() == []
