"""Chronomètres de performance (0-50, 0-100, 80-120 km/h) avec historique."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from obdtool.core import paths
from obdtool.core.samples import SampleBatch

log = logging.getLogger(__name__)

MAX_RUN_S = 60.0


class RunState(Enum):
	WAITING = "En attente des conditions de départ"
	ARMED = "Prêt : départ dès le franchissement de la vitesse initiale"
	RUNNING = "Chronométrage en cours"


@dataclass(frozen=True)
class PerfTest:
	"""Essai : de `start` à `end` km/h (start = 0 : départ arrêté)."""

	key: str
	label: str
	start: float
	end: float


TESTS = (
	PerfTest("0-50", "0 à 50 km/h", 0.0, 50.0),
	PerfTest("0-100", "0 à 100 km/h", 0.0, 100.0),
	PerfTest("80-120", "80 à 120 km/h (reprise)", 80.0, 120.0),
)


@dataclass
class PerfResult:
	"""Résultat enregistré."""

	test: str
	seconds: float
	timestamp: float
	profile: str = ""
	sample_rate_hz: float = 0.0
	source: str = "live"


@dataclass
class _Timer:
	test: PerfTest
	state: RunState = RunState.WAITING
	t0: float | None = None
	last: tuple[float, float] | None = None
	elapsed: float = 0.0
	peak: float = 0.0


def _cross(prev: tuple[float, float], cur: tuple[float, float], threshold: float) -> float:
	"""Instant (interpolé linéairement) où la vitesse franchit `threshold` entre deux mesures."""
	(t0, v0), (t1, v1) = prev, cur
	if v1 == v0:
		return t1
	return t0 + (threshold - v0) * (t1 - t0) / (v1 - v0)


@dataclass
class PerformanceTracker:
	"""Suit la vitesse et produit des résultats quand un essai est réussi."""

	timers: list[_Timer] = field(default_factory=lambda: [_Timer(t) for t in TESTS])
	_samples: int = 0
	_first_t: float | None = None

	def reset(self) -> None:
		self.timers = [_Timer(t) for t in TESTS]

	def state(self, key: str) -> tuple[RunState, float]:
		timer = next(t for t in self.timers if t.test.key == key)
		return timer.state, timer.elapsed

	def process(self, batch: SampleBatch) -> list[PerfResult]:
		results: list[PerfResult] = []
		for sample in batch.samples:
			if sample.key != "SPEED" or sample.error or not isinstance(sample.value, int | float):
				continue
			self._samples += 1
			self._first_t = self._first_t or sample.t
			point = (sample.t, float(sample.value))
			for timer in self.timers:
				result = self._step(timer, point)
				if result is not None:
					span = sample.t - (self._first_t or sample.t)
					result.sample_rate_hz = round(self._samples / span, 1) if span > 0 else 0.0
					result.source = batch.source
					results.append(result)
		return results

	def _step(self, timer: _Timer, point: tuple[float, float]) -> PerfResult | None:
		t, v = point
		test = timer.test
		prev = timer.last
		timer.last = point
		if timer.state == RunState.WAITING:
			ready = v < 1.0 if test.start == 0 else v < test.start - 3
			if ready:
				timer.state = RunState.ARMED
			return None
		if timer.state == RunState.ARMED:
			threshold = 0.5 if test.start == 0 else test.start
			if v > threshold and prev is not None:
				timer.state = RunState.RUNNING
				timer.t0 = prev[0] if test.start == 0 else _cross(prev, point, test.start)
				timer.peak = v
			elif test.start > 0 and v < test.start - 30:
				timer.state = RunState.WAITING
			return None
		# Chronométrage en cours.
		assert timer.t0 is not None
		timer.elapsed = t - timer.t0
		if v >= test.end and prev is not None:
			end = _cross(prev, point, test.end)
			seconds = round(end - timer.t0, 2)
			timer.state = RunState.WAITING
			timer.elapsed = seconds
			log.info("Performance %s : %.2f s", test.key, seconds)
			return PerfResult(test.key, seconds, time.time())
		timer.peak = max(timer.peak, v)
		# Essai valable seulement en accélération continue : abandon si la vitesse redescend.
		aborted = v < timer.peak - 3 or (v < 1.0 and test.start == 0 and timer.elapsed > 1.0)
		if aborted or timer.elapsed > MAX_RUN_S:
			timer.state = RunState.WAITING
			timer.elapsed = 0.0
		return None


class PerfHistory:
	"""Historique des résultats (JSON dans le dossier de données)."""

	def __init__(self, path: Path | None = None) -> None:
		self.path = path or paths.data_dir() / "performances.json"

	def load(self) -> list[PerfResult]:
		try:
			data = json.loads(self.path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError):
			return []
		results = []
		for item in data if isinstance(data, list) else []:
			try:
				results.append(PerfResult(**item))
			except TypeError:
				continue
		return results

	def add(self, result: PerfResult) -> None:
		results = self.load()
		results.append(result)
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self.path.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=1), encoding="utf-8")

	def clear(self) -> None:
		self.path.unlink(missing_ok=True)

	def best(self, test: str) -> PerfResult | None:
		candidates = [r for r in self.load() if r.test == test and r.source == "live"]
		return min(candidates, key=lambda r: r.seconds) if candidates else None
