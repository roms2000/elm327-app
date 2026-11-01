"""Mémoire des séries temporelles de la session (tampons circulaires numpy)."""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import numpy as np

from obdtool.core.samples import SampleBatch

DEFAULT_CAPACITY = 200_000  # points par valeur (≈ 1 h à 50 Hz)


class TimeSeries:
	"""Série (horodatage, valeur) de capacité fixe ; les plus anciens points sont écrasés."""

	def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
		self.capacity = capacity
		self._t = np.empty(capacity, dtype=np.float64)
		self._v = np.empty(capacity, dtype=np.float64)
		self._start = 0
		self._size = 0

	def __len__(self) -> int:
		return self._size

	def append(self, t: float, value: float) -> None:
		index = (self._start + self._size) % self.capacity
		self._t[index] = t
		self._v[index] = value
		if self._size < self.capacity:
			self._size += 1
		else:
			self._start = (self._start + 1) % self.capacity

	def arrays(self, copy: bool = True) -> tuple[np.ndarray, np.ndarray]:
		"""Données ordonnées (vues sans copie si `copy` est faux et que le tampon n'a pas encore bouclé)."""
		if self._size == 0:
			return np.empty(0), np.empty(0)
		end = self._start + self._size
		if end <= self.capacity:
			t, v = self._t[self._start : end], self._v[self._start : end]
			return (t.copy(), v.copy()) if copy else (t, v)
		first = self.capacity - self._start
		return (
			np.concatenate((self._t[self._start :], self._t[: self._size - first])),
			np.concatenate((self._v[self._start :], self._v[: self._size - first])),
		)

	def window(self, t0: float, t1: float) -> tuple[np.ndarray, np.ndarray]:
		"""Points dont l'horodatage est compris entre t0 et t1 (plus un point de part et d'autre)."""
		t, v = self.arrays(copy=False)
		if t.size == 0:
			return t, v
		lo = max(int(np.searchsorted(t, t0, side="left")) - 1, 0)
		hi = min(int(np.searchsorted(t, t1, side="right")) + 1, t.size)
		return t[lo:hi].copy(), v[lo:hi].copy()

	def value_at(self, t: float, max_gap: float = 5.0) -> float | None:
		"""Valeur interpolée linéairement à l'instant t (None hors données ou trou trop long)."""
		times, values = self.arrays(copy=False)
		if times.size == 0 or t < times[0] or t > times[-1]:
			return None
		index = int(np.searchsorted(times, t, side="left"))
		if index < times.size and times[index] == t:
			return float(values[index])
		before, after = index - 1, index
		if after >= times.size or before < 0:
			return None
		dt = times[after] - times[before]
		if dt > max_gap:
			return None
		ratio = (t - times[before]) / dt if dt > 0 else 0.0
		value = values[before] + ratio * (values[after] - values[before])
		return float(value) if math.isfinite(value) else None

	@property
	def last_time(self) -> float | None:
		if self._size == 0:
			return None
		return float(self._t[(self._start + self._size - 1) % self.capacity])

	def clear(self) -> None:
		self._start = 0
		self._size = 0


class TimeSeriesStore:
	"""Ensemble des séries numériques reçues pendant la session."""

	def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
		self.capacity = capacity
		self.series: dict[str, TimeSeries] = {}

	def add_batch(self, batch: SampleBatch) -> None:
		for sample in batch.samples:
			value = sample.value
			if sample.error or not isinstance(value, int | float) or isinstance(value, bool):
				continue
			series = self.series.get(sample.key)
			if series is None:
				series = self.series[sample.key] = TimeSeries(self.capacity)
			series.append(sample.t, float(value))

	def get(self, key: str) -> TimeSeries | None:
		return self.series.get(key)

	def keys(self) -> list[str]:
		return list(self.series)

	def time_range(self) -> tuple[float, float] | None:
		"""Premier et dernier horodatage toutes séries confondues."""
		bounds = [s.arrays()[0] for s in self.series.values() if len(s)]
		if not bounds:
			return None
		return float(min(b[0] for b in bounds)), float(max(b[-1] for b in bounds))

	def clear(self) -> None:
		self.series.clear()

	def export_csv(self, path: Path, keys: Iterable[str], t0: float, t1: float, step: float | None = None) -> int:
		"""Exporte la plage [t0, t1] en CSV (une colonne par valeur, interpolation sur une base de temps commune).

		Retourne le nombre de lignes écrites.
		"""
		keys = [k for k in keys if k in self.series]
		times: np.ndarray
		if step:
			times = np.arange(t0, t1 + step / 2, step)
		else:
			collected = [self.series[k].window(t0, t1)[0] for k in keys]
			merged = np.unique(np.concatenate(collected)) if collected else np.empty(0)
			times = merged[(merged >= t0) & (merged <= t1)]
		columns = []
		for key in keys:
			t_k, v_k = self.series[key].window(t0, t1)
			if t_k.size == 0:
				columns.append(np.full(times.size, np.nan))
				continue
			values = np.interp(times, t_k, v_k, left=np.nan, right=np.nan)
			# Pas d'interpolation à travers un trou de plus de 5 s (valeur en pause, liaison perdue…).
			index = np.clip(np.searchsorted(t_k, times), 1, max(t_k.size - 1, 1))
			gaps = (t_k[index] - t_k[index - 1]) > 5.0 if t_k.size > 1 else np.zeros(times.size, dtype=bool)
			exact = np.isin(times, t_k)
			values[gaps & ~exact] = np.nan
			columns.append(values)
		with path.open("w", newline="", encoding="utf-8") as fh:
			writer = csv.writer(fh, delimiter=";")
			writer.writerow(["horodatage", "heure", *keys])
			for row_index, t in enumerate(times):
				row = [f"{t:.3f}", datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]]
				for column in columns:
					value = column[row_index]
					row.append("" if math.isnan(value) else f"{value:.4f}".replace(".", ","))
				writer.writerow(row)
		return int(times.size)
