"""Analyses à partir des données mémorisées : sondes lambda, corrections de richesse, batterie / charge.

Ces analyses donnent des indications, pas un diagnostic : elles supposent un moteur chaud en boucle fermée.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from obdtool.core.timeseries import TimeSeriesStore

SWITCH_THRESHOLD_V = 0.45


@dataclass
class LambdaAnalysis:
	"""Activité des sondes à bande étroite amont / aval."""

	duration_s: float = 0.0
	upstream_hz: float | None = None
	downstream_hz: float | None = None
	upstream_min: float | None = None
	upstream_max: float | None = None
	ratio: float | None = None
	verdicts: list[str] = field(default_factory=list)


def _switches(values: np.ndarray, threshold: float = SWITCH_THRESHOLD_V, hysteresis: float = 0.05) -> int:
	"""Nombre de passages riche ↔ pauvre (avec hystérésis pour ignorer le bruit)."""
	state = None
	count = 0
	for v in values:
		if v > threshold + hysteresis:
			new = "riche"
		elif v < threshold - hysteresis:
			new = "pauvre"
		else:
			continue
		if state is not None and new != state:
			count += 1
		state = new
	return count


def analyse_lambda(
	store: TimeSeriesStore, window_s: float = 60.0, up: str = "O2_B1S1", down: str = "O2_B1S2"
) -> LambdaAnalysis:
	"""Fréquence de bascule de la sonde amont et activité relative de la sonde aval sur la dernière minute."""
	result = LambdaAnalysis()
	upstream = store.get(up)
	if upstream is None or len(upstream) < 10:
		result.verdicts.append("Pas assez de mesures de la sonde amont (cochez-la dans Valeurs ou Graphiques).")
		return result
	end = upstream.last_time or 0.0
	t, v = upstream.window(end - window_s, end)
	result.duration_s = float(t[-1] - t[0]) if t.size > 1 else 0.0
	if result.duration_s < 10:
		result.verdicts.append("Analyse sur moins de 10 s : laissez tourner le moteur chaud un peu plus longtemps.")
		return result
	coolant = store.get("COOLANT_TEMP")
	if coolant is not None and len(coolant) and coolant.value_at(coolant.last_time or 0.0, max_gap=1e9) is not None:
		temperature = coolant.value_at(coolant.last_time or 0.0, max_gap=1e9) or 0.0
		if temperature < 70:
			result.verdicts.append(
				f"Moteur froid ({temperature:.0f} °C) : régulation en boucle ouverte, analyse non significative. "
				"Recommencez moteur chaud (plus de 80 °C)."
			)
			return result
	result.upstream_min, result.upstream_max = float(v.min()), float(v.max())
	result.upstream_hz = _switches(v) / 2 / result.duration_s
	downstream = store.get(down)
	if downstream is not None and len(downstream) >= 10:
		_td, vd = downstream.window(end - window_s, end)
		result.downstream_hz = _switches(vd) / 2 / result.duration_s
		if result.upstream_hz:
			result.ratio = result.downstream_hz / result.upstream_hz
	if result.upstream_max < 0.6 or result.upstream_min > 0.3:
		result.verdicts.append(
			"La sonde amont n'oscille pas sur toute sa plage (0,1 à 0,9 V) : boucle ouverte, moteur froid ou sonde fatiguée."
		)
	elif result.upstream_hz < 0.3:
		result.verdicts.append("Sonde amont lente (moins de 0,3 bascule/s au ralenti chaud) : sonde vieillissante ?")
	else:
		result.verdicts.append(f"Sonde amont active ({result.upstream_hz:.2f} cycle/s).")
	if result.ratio is not None:
		if result.ratio > 0.7:
			result.verdicts.append(
				"La sonde aval copie la sonde amont : le catalyseur ne stocke plus l'oxygène (efficacité faible, cf. P0420)."
			)
		elif result.ratio > 0.3:
			result.verdicts.append("Sonde aval assez active : catalyseur à surveiller.")
		else:
			result.verdicts.append("Sonde aval stable : catalyseur efficace.")
	return result


@dataclass
class TrimCell:
	"""Corrections moyennes dans une plage de fonctionnement."""

	label: str
	samples: int = 0
	short_mean: float | None = None
	long_mean: float | None = None

	@property
	def total(self) -> float | None:
		if self.short_mean is None and self.long_mean is None:
			return None
		return (self.short_mean or 0.0) + (self.long_mean or 0.0)


TRIM_BANDS = (("Ralenti", 0, 1000), ("Charge partielle", 1000, 3000), ("Haut régime", 3000, 9000))


def analyse_fuel_trims(store: TimeSeriesStore) -> tuple[list[TrimCell], list[str]]:
	"""Correction totale (court + long terme) par plage de régime, et indications."""
	rpm = store.get("RPM")
	short = store.get("SHORT_FUEL_TRIM_1")
	long_ = store.get("LONG_FUEL_TRIM_1")
	cells = [TrimCell(label) for label, _lo, _hi in TRIM_BANDS]
	if rpm is None or (short is None and long_ is None) or len(rpm) < 10:
		return cells, ["Pas assez de mesures (régime et corrections de richesse nécessaires)."]
	t_rpm, v_rpm = rpm.arrays(copy=False)
	for cell, (_label, low, high) in zip(cells, TRIM_BANDS, strict=True):
		for attribute, series in (("short_mean", short), ("long_mean", long_)):
			if series is None or len(series) == 0:
				continue
			t_s, v_s = series.arrays(copy=False)
			rpm_at = np.interp(t_s, t_rpm, v_rpm, left=np.nan, right=np.nan)
			mask = (rpm_at >= low) & (rpm_at < high)
			if mask.any():
				setattr(cell, attribute, float(v_s[mask].mean()))
				cell.samples = max(cell.samples, int(mask.sum()))
	hints = []
	idle, part, high_rpm = (c.total for c in cells)
	if idle is not None and part is not None and idle > 10 and idle - part > 6:
		hints.append(
			"Mélange pauvre surtout au ralenti, qui se corrige en charge : typique d'une prise d'air "
			"(durite, joint de collecteur, valve de purge)."
		)
	if all(x is not None and x > 10 for x in (idle, part)):
		hints.append(
			"Mélange pauvre à tous les régimes : pression d'essence faible, injecteurs encrassés ou capteur MAP."
		)
	if all(x is not None and x < -10 for x in (idle, part)):
		hints.append("Mélange riche : injecteur qui fuit, pression d'essence trop élevée, capteur de température ?")
	if high_rpm is not None and high_rpm > 10 and (part is None or part < 8):
		hints.append("Pauvre seulement à haut régime : débit de la pompe à essence ou filtre à carburant ?")
	if not hints:
		hints.append("Corrections dans la plage normale (± 10 %)." if any(c.total is not None for c in cells) else "")
	return cells, [h for h in hints if h]


@dataclass
class BatteryCheck:
	"""Relevés de tension pour le test batterie / charge."""

	rest: float | None = None  # moteur arrêté, contact mis
	crank_min: float | None = None  # minimum pendant le démarrage
	idle: float | None = None  # moteur au ralenti
	load: float | None = None  # ralenti avec consommateurs (phares, dégivrage, ventilation)

	def verdicts(self) -> list[str]:
		out = []
		if self.rest is not None:
			if self.rest >= 12.5:
				out.append(f"Repos {self.rest:.2f} V : batterie bien chargée.")
			elif self.rest >= 12.2:
				out.append(f"Repos {self.rest:.2f} V : batterie partiellement déchargée.")
			else:
				out.append(f"Repos {self.rest:.2f} V : batterie déchargée ou en fin de vie.")
		if self.crank_min is not None:
			if self.crank_min >= 10.0:
				out.append(f"Démarrage {self.crank_min:.2f} V minimum : bonne tenue de la batterie.")
			elif self.crank_min >= 9.6:
				out.append(f"Démarrage {self.crank_min:.2f} V minimum : batterie juste.")
			else:
				out.append(f"Démarrage {self.crank_min:.2f} V minimum : batterie faible (ou démarreur gourmand).")
		for label, value in (("Ralenti", self.idle), ("Ralenti avec consommateurs", self.load)):
			if value is None:
				continue
			if value < 13.2:
				out.append(f"{label} {value:.2f} V : charge insuffisante (alternateur, courroie, régulateur ?).")
			elif value > 14.8:
				out.append(f"{label} {value:.2f} V : surcharge (régulateur de l'alternateur ?).")
			else:
				out.append(f"{label} {value:.2f} V : charge correcte.")
		return out
