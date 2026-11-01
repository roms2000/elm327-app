"""Échantillons de mesures et statistiques de session."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sample:
	"""Mesure d'une valeur à un instant donné (horodatage Unix, valeur en unité métrique)."""

	key: str
	t: float
	value: float | str | None
	error: str = ""


@dataclass
class SampleBatch:
	"""Lot d'échantillons diffusé aux écrans (temps réel ou relecture)."""

	samples: list[Sample]
	query_rate: float = 0.0  # requêtes par seconde
	cycle_rate: float = 0.0  # cycles complets par seconde
	source: str = "live"  # live | replay


@dataclass
class ValueStats:
	"""Statistiques d'une valeur numérique sur la session."""

	count: int = 0
	minimum: float = math.inf
	maximum: float = -math.inf
	total: float = 0.0
	last: float | str | None = None
	last_t: float = 0.0
	error: str = ""

	@property
	def mean(self) -> float | None:
		return self.total / self.count if self.count else None

	def add(self, sample: Sample) -> None:
		self.last_t = sample.t
		self.error = sample.error
		if sample.error:
			return
		self.last = sample.value
		if isinstance(sample.value, int | float) and not isinstance(sample.value, bool):
			value = float(sample.value)
			if math.isfinite(value):
				self.count += 1
				self.total += value
				self.minimum = min(self.minimum, value)
				self.maximum = max(self.maximum, value)


@dataclass
class SessionStats:
	"""Statistiques de toutes les valeurs reçues depuis la dernière remise à zéro."""

	values: dict[str, ValueStats] = field(default_factory=dict)

	def add_batch(self, batch: SampleBatch) -> None:
		for sample in batch.samples:
			self.values.setdefault(sample.key, ValueStats()).add(sample)

	def get(self, key: str) -> ValueStats | None:
		return self.values.get(key)

	def reset(self) -> None:
		self.values.clear()
