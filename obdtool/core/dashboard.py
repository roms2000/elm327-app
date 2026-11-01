"""Mises en page du tableau de bord : jauges configurables, enregistrables par nom."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from obdtool.core import pids

ZONE_COLORS = {"vert": "#2e9d4f", "orange": "#d58b00", "rouge": "#d03b3b", "bleu": "#3d7fd6"}
GAUGE_KINDS = {"dial": "Cadran circulaire", "bar": "Barre", "value": "Valeur numérique"}


@dataclass
class Zone:
	"""Plage colorée d'une jauge."""

	start: float
	end: float
	color: str = "rouge"


@dataclass
class GaugeConfig:
	"""Jauge : valeur affichée, type, bornes et zones."""

	key: str
	kind: str = "dial"
	vmin: float | None = None
	vmax: float | None = None
	zones: list[Zone] = field(default_factory=list)
	label: str = ""

	def bounds(self) -> tuple[float, float]:
		"""Bornes effectives (celles du catalogue de PID par défaut)."""
		definition = pids.get(self.key)
		low = self.vmin if self.vmin is not None else (definition.vmin if definition else 0.0)
		high = self.vmax if self.vmax is not None else (definition.vmax if definition else 100.0)
		if high <= low:
			high = low + 1.0
		return low, high

	def title(self) -> str:
		definition = pids.get(self.key)
		return self.label or (definition.name if definition else self.key)

	def zone_color(self, value: float) -> str | None:
		"""Couleur de la zone contenant la valeur, s'il y en a une."""
		for zone in self.zones:
			if zone.start <= value <= zone.end:
				return ZONE_COLORS.get(zone.color, zone.color)
		return None


@dataclass
class DashboardLayout:
	"""Mise en page nommée."""

	name: str
	columns: int = 3
	gauges: list[GaugeConfig] = field(default_factory=list)

	def keys(self) -> list[str]:
		return list(dict.fromkeys(g.key for g in self.gauges))

	def to_dict(self) -> dict[str, Any]:
		data = asdict(self)
		for gauge in data["gauges"]:
			for name in ("vmin", "vmax"):
				if gauge[name] is None:
					del gauge[name]
		return data

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> DashboardLayout:
		gauges = []
		for raw in data.get("gauges", []):
			if not isinstance(raw, dict) or "key" not in raw:
				continue
			zones = [
				Zone(float(z.get("start", 0)), float(z.get("end", 0)), str(z.get("color", "rouge")))
				for z in raw.get("zones", [])
				if isinstance(z, dict)
			]
			gauges.append(
				GaugeConfig(
					key=str(raw["key"]),
					kind=str(raw.get("kind", "dial")) if raw.get("kind") in GAUGE_KINDS else "dial",
					vmin=float(raw["vmin"]) if raw.get("vmin") is not None else None,
					vmax=float(raw["vmax"]) if raw.get("vmax") is not None else None,
					zones=zones,
					label=str(raw.get("label", "")),
				)
			)
		columns = data.get("columns", 3)
		return cls(
			str(data.get("name", "Sans nom")), max(1, min(int(columns), 6)) if isinstance(columns, int) else 3, gauges
		)


def default_layouts() -> list[DashboardLayout]:
	"""Mises en page fournies par défaut (moteur essence à capteur MAP)."""
	return [
		DashboardLayout(
			"Conduite",
			3,
			[
				GaugeConfig("RPM", "dial", 0, 7000, [Zone(6000, 7000, "rouge")]),
				GaugeConfig("SPEED", "dial", 0, 200),
				GaugeConfig("FUEL_RATE_DERIVED", "value", label="Consommation instantanée"),
				GaugeConfig("COOLANT_TEMP", "bar", 40, 130, [Zone(40, 70, "bleu"), Zone(105, 130, "rouge")]),
				GaugeConfig("ENGINE_LOAD", "bar", 0, 100),
				GaugeConfig("ADAPTER_VOLTAGE", "value", zones=[Zone(0, 11.8, "rouge"), Zone(15.0, 20, "rouge")]),
			],
		),
		DashboardLayout(
			"Diagnostic moteur",
			4,
			[
				GaugeConfig("RPM", "dial", 0, 7000, [Zone(6000, 7000, "rouge")]),
				GaugeConfig("ENGINE_LOAD", "dial", 0, 100),
				GaugeConfig("INTAKE_PRESSURE", "dial", 0, 110),
				GaugeConfig("TIMING_ADVANCE", "dial", -10, 50),
				GaugeConfig("THROTTLE_POS", "bar", 0, 100),
				GaugeConfig("INTAKE_TEMP", "bar", -10, 70, [Zone(50, 70, "orange")]),
				GaugeConfig("COOLANT_TEMP", "bar", 40, 130, [Zone(40, 70, "bleu"), Zone(105, 130, "rouge")]),
				GaugeConfig("FUEL_STATUS", "value"),
			],
		),
		DashboardLayout(
			"Sondes lambda",
			3,
			[
				GaugeConfig("O2_B1S1", "bar", 0, 1.0, [Zone(0, 0.2, "bleu"), Zone(0.8, 1.0, "orange")]),
				GaugeConfig("O2_B1S2", "bar", 0, 1.0, [Zone(0, 0.2, "bleu"), Zone(0.8, 1.0, "orange")]),
				GaugeConfig("FUEL_STATUS", "value"),
				GaugeConfig("SHORT_FUEL_TRIM_1", "dial", -25, 25, [Zone(-25, -10, "orange"), Zone(10, 25, "orange")]),
				GaugeConfig("LONG_FUEL_TRIM_1", "dial", -25, 25, [Zone(-25, -10, "orange"), Zone(10, 25, "orange")]),
				GaugeConfig("O2_B1S1_TRIM", "value"),
			],
		),
	]


def load_layouts(raw: Any) -> list[DashboardLayout]:
	"""Mises en page enregistrées dans les réglages (défauts si absentes ou invalides)."""
	if not isinstance(raw, list) or not raw:
		return default_layouts()
	layouts = [DashboardLayout.from_dict(item) for item in raw if isinstance(item, dict)]
	return layouts or default_layouts()


def dump_layouts(layouts: list[DashboardLayout]) -> list[dict[str, Any]]:
	return [layout.to_dict() for layout in layouts]
