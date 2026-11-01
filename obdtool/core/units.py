"""Conversion des unités métriques vers les unités impériales pour l'affichage."""

from __future__ import annotations

from collections.abc import Callable

_IMPERIAL: dict[str, tuple[str, Callable[[float], float]]] = {
	"km/h": ("mph", lambda v: v / 1.609344),
	"km": ("mi", lambda v: v / 1.609344),
	"°C": ("°F", lambda v: v * 9 / 5 + 32),
	"kPa": ("psi", lambda v: v * 0.145037738),
	"Pa": ("inH₂O", lambda v: v / 249.08891),
	"g/s": ("lb/min", lambda v: v * 0.132277357),
	"L/h": ("gal/h", lambda v: v / 3.785411784),
	"L/100 km": ("mpg", lambda v: 235.214583 / v if v > 0 else 0.0),
	"kW": ("ch", lambda v: v * 1.35962162),
	"N·m": ("lb·ft", lambda v: v * 0.737562149),
	"kg": ("lb", lambda v: v * 2.20462262),
}


def display_unit(unit: str, system: str) -> str:
	"""Unité affichée selon le système choisi (`metric` ou `imperial`)."""
	if system == "imperial" and unit in _IMPERIAL:
		return _IMPERIAL[unit][0]
	return unit


def convert(value: float | str | None, unit: str, system: str) -> float | str | None:
	"""Convertit une valeur métrique vers le système d'unités choisi."""
	if system != "imperial" or not isinstance(value, int | float) or unit not in _IMPERIAL:
		return value
	return _IMPERIAL[unit][1](float(value))
