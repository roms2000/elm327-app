"""Profils véhicule éditables (TOML dans ~/.config/obdtool/profiles/)."""

from __future__ import annotations

import logging
import math
import re
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import tomli_w

from obdtool.core import paths

log = logging.getLogger(__name__)

# Rapport stœchiométrique air/carburant et masse volumique (kg/L) par carburant.
FUELS: dict[str, tuple[str, float, float]] = {
	"essence": ("Essence (SP95/SP98)", 14.7, 0.745),
	"e10": ("Essence E10", 14.1, 0.750),
	"e85": ("Superéthanol E85", 9.8, 0.785),
	"gazole": ("Gazole", 14.5, 0.835),
	"gpl": ("GPL", 15.5, 0.540),
}
_TYRE = re.compile(r"(\d{3})\s*/\s*(\d{2})\s*[A-Z]*\s*R?\s*(\d{2})", re.IGNORECASE)


@dataclass
class VehicleProfile:
	"""Caractéristiques utilisées par les calculs (valeurs à vérifier sur votre véhicule)."""

	name: str = "Renault Clio III 1.6 16v (K4M)"
	make: str = "renault"
	model: str = "Clio III"
	engine: str = "K4M 1.6 16v, 112 ch"
	year: int = 2005
	fuel: str = "essence"
	displacement_cc: float = 1598.0
	cylinders: int = 4
	volumetric_efficiency: float = 0.85  # estimation : rendement volumétrique moyen d'un 16 soupapes atmosphérique
	mass_kg: float = 1150.0  # masse en ordre de marche + conducteur (estimation)
	gear_ratios: list[float] = field(default_factory=lambda: [3.727, 2.048, 1.393, 1.029, 0.820])
	final_drive: float = 4.214
	tyre: str = "185/60 R15"
	drag_coefficient: float = 0.33
	frontal_area_m2: float = 2.10
	rolling_resistance: float = 0.012
	notes: str = (
		"Rapports de boîte (JH3), pont, masse et SCx : valeurs indicatives à vérifier "
		"(carte grise, documentation constructeur)."
	)

	@property
	def displacement_l(self) -> float:
		return self.displacement_cc / 1000.0

	@property
	def afr(self) -> float:
		return FUELS.get(self.fuel, FUELS["essence"])[1]

	@property
	def fuel_density(self) -> float:
		return FUELS.get(self.fuel, FUELS["essence"])[2]

	def tyre_circumference_m(self) -> float:
		"""Circonférence de roulement calculée à partir de la dimension de pneu (ex. 185/60 R15)."""
		return tyre_circumference_m(self.tyre)

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> VehicleProfile:
		profile = cls()
		for f in fields(cls):
			if f.name not in data:
				continue
			value = data[f.name]
			current = getattr(profile, f.name)
			try:
				if isinstance(current, bool):
					value = bool(value)
				elif isinstance(current, int):
					value = int(value)
				elif isinstance(current, float):
					value = float(value)
				elif isinstance(current, list):
					value = [float(v) for v in value if float(v) > 0]
				else:
					value = str(value)
			except (TypeError, ValueError):
				log.warning("Profil : valeur invalide pour %s (%r)", f.name, value)
				continue
			setattr(profile, f.name, value)
		return profile


def tyre_circumference_m(tyre: str) -> float:
	"""Circonférence (m) d'un pneu « largeur/série Rjante » ; 1,87 m si illisible."""
	match = _TYRE.search(tyre or "")
	if not match:
		return 1.87
	width, aspect, rim = (int(g) for g in match.groups())
	diameter_mm = rim * 25.4 + 2 * width * aspect / 100
	# Écrasement du pneu en charge : environ 3 % de rayon en moins.
	return math.pi * diameter_mm / 1000 * 0.97


def profiles_dir() -> Path:
	return paths.config_dir() / "profiles"


def _slug(name: str) -> str:
	return re.sub(r"[^\w\-]+", "_", name.strip()).strip("_")[:60] or "profil"


def list_profiles() -> list[VehicleProfile]:
	"""Profils enregistrés ; crée le profil par défaut (Clio III K4M) s'il n'y en a aucun."""
	directory = profiles_dir()
	directory.mkdir(parents=True, exist_ok=True)
	profiles = []
	for path in sorted(directory.glob("*.toml")):
		try:
			with path.open("rb") as fh:
				profiles.append(VehicleProfile.from_dict(tomllib.load(fh)))
		except (OSError, tomllib.TOMLDecodeError) as exc:
			log.warning("Profil illisible %s : %s", path, exc)
	if not profiles:
		default = VehicleProfile()
		save_profile(default)
		profiles.append(default)
	return profiles


def save_profile(profile: VehicleProfile) -> Path:
	path = profiles_dir() / f"{_slug(profile.name)}.toml"
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("wb") as fh:
		tomli_w.dump(profile.to_dict(), fh)
	return path


def delete_profile(profile: VehicleProfile) -> None:
	(profiles_dir() / f"{_slug(profile.name)}.toml").unlink(missing_ok=True)


def find_profile(name: str) -> VehicleProfile:
	"""Profil par nom (le premier disponible sinon)."""
	profiles = list_profiles()
	return next((p for p in profiles if p.name == name), profiles[0])
