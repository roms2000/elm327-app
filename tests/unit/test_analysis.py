"""Tests des analyses (sondes lambda, corrections de richesse, batterie)."""

from __future__ import annotations

import math

from obdtool.core.analysis import BatteryCheck, analyse_fuel_trims, analyse_lambda
from obdtool.core.samples import Sample, SampleBatch
from obdtool.core.timeseries import TimeSeriesStore


def _store(*series: tuple[str, list[tuple[float, float]]]) -> TimeSeriesStore:
	store = TimeSeriesStore(capacity=10_000)
	samples = [Sample(key, t, v) for key, points in series for t, v in points]
	store.add_batch(SampleBatch(sorted(samples, key=lambda s: s.t)))
	return store


def _sine(freq: float, amp: float, mean: float, seconds: float = 30, rate: float = 10) -> list[tuple[float, float]]:
	return [(i / rate, mean + amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(int(seconds * rate))]


def test_lambda_good_catalyst() -> None:
	store = _store(("O2_B1S1", _sine(1.0, 0.38, 0.45)), ("O2_B1S2", [(i / 10, 0.68) for i in range(300)]))
	result = analyse_lambda(store)
	assert 0.8 < result.upstream_hz < 1.2
	assert result.ratio == 0.0
	assert any("efficace" in v for v in result.verdicts)


def test_lambda_worn_catalyst_and_lazy_sensor() -> None:
	store = _store(("O2_B1S1", _sine(1.0, 0.38, 0.45)), ("O2_B1S2", _sine(1.0, 0.35, 0.45)))
	assert any("ne stocke plus" in v for v in analyse_lambda(store).verdicts)
	flat = _store(("O2_B1S1", [(i / 10, 0.8) for i in range(300)]))
	assert any("n'oscille pas" in v for v in analyse_lambda(flat).verdicts)
	assert "Pas assez" in analyse_lambda(TimeSeriesStore()).verdicts[0]


def test_fuel_trims_vacuum_leak_pattern() -> None:
	rpm = [(t, 800.0) for t in range(0, 60)] + [(t, 2500.0) for t in range(60, 120)]
	short = [(t + 0.5, 12.0 if t < 60 else 2.0) for t in range(120)]
	long_ = [(t + 0.5, 6.0 if t < 60 else 1.0) for t in range(120)]
	cells, hints = analyse_fuel_trims(_store(("RPM", rpm), ("SHORT_FUEL_TRIM_1", short), ("LONG_FUEL_TRIM_1", long_)))
	assert cells[0].total and cells[0].total > 15
	assert cells[1].total and cells[1].total < 5
	assert any("prise d'air" in h for h in hints)


def test_battery_verdicts() -> None:
	check = BatteryCheck(rest=12.6, crank_min=9.2, idle=14.1, load=12.9)
	text = " ".join(check.verdicts())
	assert "bien chargée" in text and "batterie faible" in text and "charge insuffisante" in text
	assert BatteryCheck().verdicts() == []
