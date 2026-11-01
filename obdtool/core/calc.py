"""Valeurs calculées : débit d'air (speed-density), consommation, rapport engagé, puissance estimée.

Toutes les fonctions travaillent en unités métriques. Les résultats dépendent de la justesse du profil
véhicule (rendement volumétrique surtout) : ce sont des estimations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from obdtool.core.profile import VehicleProfile
from obdtool.core.samples import Sample, SampleBatch

R_AIR = 287.05  # J/(kg·K)
AIR_DENSITY = 1.204  # kg/m³ à 20 °C
G = 9.81

# Entrées nécessaires à chaque valeur calculée (les PID non supportés sont ignorés).
DERIVED_INPUTS: dict[str, tuple[str, ...]] = {
	"AIRFLOW_DERIVED": ("RPM", "INTAKE_PRESSURE", "INTAKE_TEMP", "MAF"),
	"FUEL_RATE_DERIVED": (
		"RPM",
		"SPEED",
		"INTAKE_PRESSURE",
		"INTAKE_TEMP",
		"MAF",
		"FUEL_RATE",
		"SHORT_FUEL_TRIM_1",
		"LONG_FUEL_TRIM_1",
		"THROTTLE_POS",
		"COMMANDED_EQUIV_RATIO",
	),
	"GEAR_DERIVED": ("RPM", "SPEED"),
	"POWER_DERIVED": ("SPEED",),
}
for _key in ("FUEL_ECONOMY_DERIVED", "FUEL_AVG_DERIVED", "FUEL_USED_DERIVED"):
	DERIVED_INPUTS[_key] = DERIVED_INPUTS["FUEL_RATE_DERIVED"]
DERIVED_INPUTS["DISTANCE_DERIVED"] = ("SPEED",)


def airflow_speed_density(map_kpa: float, iat_c: float, rpm: float, displacement_l: float, ve: float) -> float:
	"""Débit d'air (g/s) d'un moteur 4 temps sans débitmètre : loi des gaz parfaits.

	masse = P·V·VE / (R·T) par cycle complet (2 tours), soit rpm/120 cycles par seconde.
	"""
	temperature = iat_c + 273.15
	if temperature <= 0 or rpm <= 0:
		return 0.0
	mass_per_cycle = map_kpa * 1000 * (displacement_l / 1000) * ve / (R_AIR * temperature)
	return mass_per_cycle * rpm / 120 * 1000


def fuel_rate_lph(
	airflow_gs: float, afr: float, density_kg_l: float, correction_pct: float = 0.0, lam: float = 1.0
) -> float:
	"""Consommation horaire (L/h) à partir du débit d'air, du rapport stœchiométrique et des corrections."""
	if airflow_gs <= 0 or afr <= 0 or density_kg_l <= 0:
		return 0.0
	fuel_gs = airflow_gs / (afr * max(lam, 0.5)) * (1 + correction_pct / 100)
	return fuel_gs * 3600 / 1000 / density_kg_l


def economy_l100(fuel_lph: float, speed_kmh: float) -> float | None:
	"""Consommation aux 100 km ; None à l'arrêt ou à très basse vitesse."""
	if speed_kmh < 3:
		return None
	return fuel_lph / speed_kmh * 100


def estimate_gear(rpm: float, speed_kmh: float, profile: VehicleProfile, tolerance: float = 0.12) -> int | None:
	"""Rapport engagé (1…n) d'après régime et vitesse ; None si embrayage débrayé ou point mort."""
	if speed_kmh < 4 or rpm < 500 or not profile.gear_ratios:
		return None
	wheel_rpm = speed_kmh / 3.6 / profile.tyre_circumference_m() * 60
	overall = rpm / wheel_rpm
	best, best_error = None, tolerance
	for index, ratio in enumerate(profile.gear_ratios, start=1):
		error = abs(overall - ratio * profile.final_drive) / (ratio * profile.final_drive)
		if error < best_error:
			best, best_error = index, error
	return best


def road_power_kw(speed_kmh: float, accel_ms2: float, profile: VehicleProfile) -> float:
	"""Puissance à la roue estimée : inertie + traînée aérodynamique + résistance au roulement."""
	v = speed_kmh / 3.6
	inertia = profile.mass_kg * accel_ms2 * v
	drag = 0.5 * AIR_DENSITY * profile.drag_coefficient * profile.frontal_area_m2 * v**3
	rolling = profile.rolling_resistance * profile.mass_kg * G * v
	return (inertia + drag + rolling) / 1000


@dataclass
class DerivedEngine:
	"""Calcule les valeurs dérivées au fil des lots et cumule les totaux de session."""

	profile: VehicleProfile = field(default_factory=VehicleProfile)
	has_maf: bool = False
	has_fuel_rate: bool = False
	latest: dict[str, tuple[float, float | str]] = field(default_factory=dict)
	distance_km: float = 0.0
	fuel_l: float = 0.0
	_last_t: float | None = None
	_speed_hist: list[tuple[float, float]] = field(default_factory=list)

	def reset(self) -> None:
		self.latest.clear()
		self.distance_km = 0.0
		self.fuel_l = 0.0
		self._last_t = None
		self._speed_hist.clear()

	def _value(self, key: str, max_age: float = 3.0, now: float | None = None) -> float | None:
		item = self.latest.get(key)
		if item is None or not isinstance(item[1], int | float):
			return None
		if now is not None and now - item[0] > max_age:
			return None
		return float(item[1])

	def _fuel_cut(self, now: float) -> bool:
		throttle = self._value("THROTTLE_POS", now=now)
		rpm = self._value("RPM", now=now)
		speed = self._value("SPEED", now=now)
		status = self.latest.get("FUEL_STATUS")
		if status and isinstance(status[1], str) and "coupure" in status[1] and throttle is not None and throttle < 3:
			return True
		return (
			throttle is not None
			and rpm is not None
			and speed is not None
			and throttle < 1.5
			and rpm > 1400
			and speed > 10
		)

	def airflow(self, now: float) -> float | None:
		if self.has_maf:
			maf = self._value("MAF", now=now)
			if maf is not None:
				return maf
		map_kpa = self._value("INTAKE_PRESSURE", now=now)
		rpm = self._value("RPM", now=now)
		iat = self._value("INTAKE_TEMP", max_age=60.0, now=now)
		if map_kpa is None or rpm is None:
			return None
		return airflow_speed_density(
			map_kpa,
			iat if iat is not None else 25.0,
			rpm,
			self.profile.displacement_l,
			self.profile.volumetric_efficiency,
		)

	def fuel_rate(self, now: float) -> float | None:
		if self.has_fuel_rate:
			direct = self._value("FUEL_RATE", now=now)
			if direct is not None:
				return direct
		air = self.airflow(now)
		if air is None:
			return None
		if self._fuel_cut(now):
			return 0.0
		trims = (self._value("SHORT_FUEL_TRIM_1", now=now) or 0.0) + (
			self._value("LONG_FUEL_TRIM_1", max_age=60, now=now) or 0.0
		)
		lam = self._value("COMMANDED_EQUIV_RATIO", now=now) or 1.0
		return fuel_rate_lph(air, self.profile.afr, self.profile.fuel_density, trims, lam)

	def process(self, batch: SampleBatch) -> list[Sample]:
		"""Met à jour l'état avec un lot et retourne les échantillons calculés."""
		if not batch.samples:
			return []
		for sample in batch.samples:
			if not sample.error and sample.value is not None:
				self.latest[sample.key] = (sample.t, sample.value)
		now = max(s.t for s in batch.samples)
		out: list[Sample] = []
		air = self.airflow(now)
		if air is not None:
			out.append(Sample("AIRFLOW_DERIVED", now, round(air, 2)))
		rate = self.fuel_rate(now)
		speed = self._value("SPEED", now=now)
		if self._last_t is not None:
			dt = min(max(now - self._last_t, 0.0), 2.0)
			if speed is not None:
				self.distance_km += speed * dt / 3600
			if rate is not None:
				self.fuel_l += rate * dt / 3600
		self._last_t = now
		if rate is not None:
			out.append(Sample("FUEL_RATE_DERIVED", now, round(rate, 3)))
			if speed is not None:
				economy = economy_l100(rate, speed)
				if economy is not None:
					out.append(Sample("FUEL_ECONOMY_DERIVED", now, round(min(economy, 99.9), 2)))
			out.append(Sample("FUEL_USED_DERIVED", now, round(self.fuel_l, 4)))
			if self.distance_km > 0.1:
				out.append(Sample("FUEL_AVG_DERIVED", now, round(self.fuel_l / self.distance_km * 100, 2)))
		out.append(Sample("DISTANCE_DERIVED", now, round(self.distance_km, 3)))
		rpm = self._value("RPM", now=now)
		if rpm is not None and speed is not None:
			gear = estimate_gear(rpm, speed, self.profile)
			out.append(Sample("GEAR_DERIVED", now, float(gear) if gear else None))
		if speed is not None:
			power = self._power(now, speed)
			if power is not None:
				out.append(Sample("POWER_DERIVED", now, round(power, 1)))
		return out

	def _power(self, now: float, speed: float) -> float | None:
		item = self.latest.get("SPEED")
		if item is None:
			return None
		t = item[0]
		if not self._speed_hist or self._speed_hist[-1][0] != t:
			self._speed_hist.append((t, speed))
		self._speed_hist = [(ts, v) for ts, v in self._speed_hist if t - ts <= 1.5]
		if len(self._speed_hist) < 3 or self._speed_hist[-1][0] - self._speed_hist[0][0] < 0.4:
			return None
		# Pente par moindres carrés sur ~1,5 s pour lisser l'accélération.
		times = [ts - self._speed_hist[0][0] for ts, _ in self._speed_hist]
		speeds = [v / 3.6 for _, v in self._speed_hist]
		mean_t = sum(times) / len(times)
		mean_v = sum(speeds) / len(speeds)
		den = sum((x - mean_t) ** 2 for x in times)
		if den <= 0:
			return None
		accel = sum((x - mean_t) * (y - mean_v) for x, y in zip(times, speeds, strict=True)) / den
		power = road_power_kw(speed, accel, self.profile)
		return power if math.isfinite(power) and power > 0 else 0.0
