"""Moniteurs de préparation (readiness) : Mode 01 PID 01 (depuis l'effacement) et PID 41 (ce cycle)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ObdError


class MonitorState(Enum):
	"""État d'un moniteur."""

	UNSUPPORTED = "Non supporté"
	COMPLETE = "Terminé"
	INCOMPLETE = "Non terminé"


CONTINUOUS = ("Ratés d'allumage", "Système d'alimentation", "Composants")
SPARK = (
	"Catalyseur",
	"Catalyseur chauffé",
	"Système antiévaporation (EVAP)",
	"Injection d'air secondaire",
	"Climatisation (fluide frigorigène)",
	"Sondes lambda",
	"Réchauffage des sondes lambda",
	"EGR / distribution variable",
)
COMPRESSION = (
	"Catalyseur NMHC",
	"Traitement des NOx / SCR",
	"(réservé)",
	"Pression de suralimentation",
	"(réservé)",
	"Capteur de gaz d'échappement",
	"Filtre à particules",
	"EGR / distribution variable",
)


@dataclass
class Monitor:
	"""Un moniteur et son état."""

	name: str
	state: MonitorState
	continuous: bool


@dataclass
class ReadinessReport:
	"""Résultat du décodage d'un PID 01 ou 41."""

	source: str  # "01" ou "41"
	mil_on: bool = False
	dtc_count: int = 0
	ignition: str = "spark"  # spark | compression
	monitors: list[Monitor] = field(default_factory=list)

	@property
	def supported(self) -> list[Monitor]:
		return [m for m in self.monitors if m.state != MonitorState.UNSUPPORTED]

	@property
	def incomplete(self) -> list[Monitor]:
		return [m for m in self.monitors if m.state == MonitorState.INCOMPLETE]

	@property
	def incomplete_non_continuous(self) -> list[Monitor]:
		return [m for m in self.incomplete if not m.continuous]

	def ready_for_inspection(self, tolerance: int = 0) -> bool:
		"""Prêt pour un contrôle anti-pollution : voyant éteint et moniteurs non continus terminés.

		`tolerance` : nombre de moniteurs non terminés acceptés (1 selon la règle américaine EPA pour les
		véhicules 2001 et plus ; les règles européennes varient selon les pays).
		"""
		return not self.mil_on and len(self.incomplete_non_continuous) <= tolerance


def decode_status(data: bytes, source: str = "01") -> ReadinessReport:
	"""Décode les 4 octets A B C D d'une réponse PID 01 ou PID 41."""
	if len(data) < 4:
		raise ValueError("Réponse PID 01/41 trop courte")
	a, b, c, d = data[:4]
	report = ReadinessReport(source)
	if source == "01":
		report.mil_on = bool(a & 0x80)
		report.dtc_count = a & 0x7F
	report.ignition = "compression" if b & 0x08 else "spark"
	for bit, name in enumerate(CONTINUOUS):
		supported = bool(b & (1 << bit))
		incomplete = bool(b & (1 << (bit + 4)))
		report.monitors.append(Monitor(name, _state(supported, incomplete), True))
	names = COMPRESSION if report.ignition == "compression" else SPARK
	for bit, name in enumerate(names):
		if name == "(réservé)":
			continue
		supported = bool(c & (1 << bit))
		incomplete = bool(d & (1 << bit))
		report.monitors.append(Monitor(name, _state(supported, incomplete), False))
	return report


def _state(supported: bool, incomplete: bool) -> MonitorState:
	if not supported:
		return MonitorState.UNSUPPORTED
	return MonitorState.INCOMPLETE if incomplete else MonitorState.COMPLETE


def read_readiness(link: ElmLink, pid: int) -> ReadinessReport | None:
	"""Lit le PID 01 ou 41 ; None si non supporté."""
	try:
		messages = link.request(f"01{pid:02X}")
	except ObdError as exc:
		if exc.is_link_lost:
			raise
		return None
	for message in messages:
		data = bytes(message.data)
		if len(data) >= 6 and data[1] == pid:
			return decode_status(data[2:6], f"{pid:02X}")
	return None
