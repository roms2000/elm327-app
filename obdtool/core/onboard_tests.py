"""Résultats des tests embarqués : Mode 06 (CAN), Mode 05 (sondes lambda, hors CAN), Mode 08 (contrôle)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import obd

from obdtool.core import pids
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.protocols import is_can

log = logging.getLogger(__name__)

# Identifiants de moniteur (OBDMID) selon SAE J1979 / ISO 15031-5.
_O2 = [(b, s) for b in (1, 2, 3, 4) for s in (1, 2, 3, 4)]
MID_NAMES: dict[int, str] = {}
for _i, (_bank, _sensor) in enumerate(_O2[:8], start=1):
	MID_NAMES[_i] = f"Sonde lambda banc {_bank} capteur {_sensor}"
for _i, (_bank, _sensor) in enumerate(_O2[:8], start=0x41):
	MID_NAMES[_i] = f"Réchauffage sonde lambda banc {_bank} capteur {_sensor}"
MID_NAMES.update(
	{
		0x21: "Catalyseur banc 1",
		0x22: "Catalyseur banc 2",
		0x23: "Catalyseur banc 3",
		0x24: "Catalyseur banc 4",
		0x31: "EGR / distribution variable, moniteur 1",
		0x32: "EGR / distribution variable, moniteur 2",
		0x33: "EGR / distribution variable, moniteur 3",
		0x34: "EGR / distribution variable, moniteur 4",
		0x35: "Distribution variable banc 1",
		0x36: "Distribution variable banc 2",
		0x39: 'EVAP : fuite 0,150" (3,8 mm)',
		0x3A: 'EVAP : fuite 0,090" (2,3 mm)',
		0x3B: 'EVAP : fuite 0,040" (1,0 mm)',
		0x3C: 'EVAP : fuite 0,020" (0,5 mm)',
		0x3D: "EVAP : débit de purge",
		0x61: "Catalyseur chauffé banc 1",
		0x62: "Catalyseur chauffé banc 2",
		0x71: "Air secondaire, moniteur 1",
		0x72: "Air secondaire, moniteur 2",
		0x81: "Système d'alimentation banc 1",
		0x82: "Système d'alimentation banc 2",
		0x85: "Pression de suralimentation banc 1",
		0x90: "Traitement des NOx banc 1",
		0x98: "Catalyseur NOx banc 1",
		0xB0: "Filtre à particules banc 1",
		0xA1: "Ratés d'allumage : général",
	}
)
for _cyl in range(1, 13):
	MID_NAMES[0xA1 + _cyl] = f"Ratés d'allumage : cylindre {_cyl}"

# Identifiants de test standard (TID) pour les sondes lambda.
TID_NAMES: dict[int, str] = {
	0x01: "Seuil de tension riche → pauvre",
	0x02: "Seuil de tension pauvre → riche",
	0x03: "Tension basse pour le calcul du temps de bascule",
	0x04: "Tension haute pour le calcul du temps de bascule",
	0x05: "Temps de bascule riche → pauvre",
	0x06: "Temps de bascule pauvre → riche",
	0x07: "Tension minimale de la sonde",
	0x08: "Tension maximale de la sonde",
	0x09: "Temps entre deux bascules",
	0x0A: "Période de la sonde",
	0x0B: "Ratés d'allumage : moyenne EWMA des 10 derniers cycles",
	0x0C: "Ratés d'allumage : cycle en cours / dernier cycle",
}

MODE05_TIDS: dict[int, tuple[str, float, str]] = {
	0x01: ("Seuil de tension riche → pauvre", 0.005, "V"),
	0x02: ("Seuil de tension pauvre → riche", 0.005, "V"),
	0x03: ("Tension basse (calcul du temps de bascule)", 0.005, "V"),
	0x04: ("Tension haute (calcul du temps de bascule)", 0.005, "V"),
	0x05: ("Temps de bascule riche → pauvre", 0.004, "s"),
	0x06: ("Temps de bascule pauvre → riche", 0.004, "s"),
	0x07: ("Tension minimale pendant le test", 0.005, "V"),
	0x08: ("Tension maximale pendant le test", 0.005, "V"),
	0x09: ("Temps entre deux bascules", 0.04, "s"),
}


@dataclass
class TestResult:
	"""Résultat d'un test (une ligne)."""

	__test__ = False  # pas une classe de test pytest

	mid: int
	mid_name: str
	tid: int
	tid_name: str
	value: float | None
	minimum: float | None
	maximum: float | None
	unit: str
	passed: bool | None

	@property
	def status(self) -> str:
		if self.passed is None:
			return "—"
		return "OK" if self.passed else "KO"


@dataclass
class TestsReport:
	"""Résultats d'un mode de tests, avec la raison s'il n'est pas disponible."""

	__test__ = False

	results: list[TestResult] = field(default_factory=list)
	supported_ids: list[int] = field(default_factory=list)
	unavailable: str = ""


def _bitmap(link: ElmLink, mode: str, suffix: str = "") -> list[int]:
	"""Lit les masques de support d'un mode (xx00, xx20, …)."""
	supported: list[int] = []
	block = 0
	while block <= 0xE0:
		try:
			messages = link.request(f"{mode}{block:02X}{suffix}")
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			break
		ids: set[int] = set()
		for message in messages:
			data = bytes(message.data)
			offset = 2 + len(suffix) // 2
			if len(data) >= offset + 4 and data[1] == block:
				ids |= pids.decode_supported_bitmap(block, data[offset : offset + 4])
			elif mode == "06" and len(data) == 5:
				# python-OBD retire l'identifiant de moniteur des réponses 46 en CAN.
				ids |= pids.decode_supported_bitmap(block, data[1:5])
		supported.extend(sorted(i for i in ids if i % 0x20 != 0))
		if block + 0x20 not in ids:
			break
		block += 0x20
	return supported


_UNITS = {
	"volt": "V",
	"second": "s",
	"millisecond": "ms",
	"milliampere": "mA",
	"kilopascal": "kPa",
	"pascal": "Pa",
	"degree_Celsius": "°C",
	"percent": "%",
	"count": "",
	"dimensionless": "",
	"ratio": "",
}


def _quantity(value: object) -> tuple[float | None, str]:
	if value is None:
		return None, ""
	magnitude = getattr(value, "magnitude", value)
	try:
		number = float(magnitude)  # type: ignore[arg-type]
	except (TypeError, ValueError):
		return None, ""
	unit = str(getattr(value, "units", ""))
	unit = _UNITS.get(unit, unit)
	return round(number, 5), unit


def read_mode06(link: ElmLink) -> TestsReport:
	"""Lit tous les résultats Mode 06 (format CAN, décodé par python-OBD)."""
	report = TestsReport()
	if not is_can(link.protocol_code):
		report.unavailable = (
			"Mode 06 : le format des protocoles non CAN (identifiants de test constructeur) n'est pas décodé."
		)
		return report
	report.supported_ids = _bitmap(link, "06")
	if not report.supported_ids:
		report.unavailable = "Le calculateur ne publie aucun résultat Mode 06."
		return report
	commands = obd.commands.modes[6]
	for mid in report.supported_ids:
		command = commands[mid] if mid < len(commands) else None
		if command is None:
			continue
		try:
			response = link.query(command)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			log.debug("MID %02X illisible : %s", mid, exc.user_message)
			continue
		monitor = response.value
		if monitor is None:
			continue
		for test in getattr(monitor, "tests", []):
			value, unit = _quantity(test.value)
			minimum, _ = _quantity(test.min)
			maximum, _ = _quantity(test.max)
			name = TID_NAMES.get(test.tid) if test.tid < 0x80 else None
			described = test.desc if test.desc and test.desc != "Unknown" else None
			report.results.append(
				TestResult(
					mid=mid,
					mid_name=MID_NAMES.get(mid, f"MID {mid:02X}"),
					tid=test.tid,
					tid_name=name or described or f"Test {test.tid:02X} (défini par le constructeur)",
					value=value,
					minimum=minimum,
					maximum=maximum,
					unit=unit,
					passed=bool(test.passed) if test.value is not None else None,
				)
			)
	return report


def read_mode05(link: ElmLink, sensors: tuple[int, ...] = (0x01, 0x02)) -> TestsReport:
	"""Mode 05 : surveillance des sondes lambda (protocoles non CAN seulement)."""
	report = TestsReport()
	if is_can(link.protocol_code):
		report.unavailable = (
			"Mode 05 non disponible en CAN (ISO 15765-4) : la norme y remplace ce mode par le Mode 06, "
			"où figurent les résultats des sondes lambda."
		)
		return report
	for sensor in sensors:
		for tid, (name, scale, unit) in MODE05_TIDS.items():
			try:
				messages = link.request(f"05{tid:02X}{sensor:02X}")
			except ObdError as exc:
				if exc.is_link_lost:
					raise
				continue
			data = bytes(messages[0].data)
			if len(data) < 4 or data[0] != 0x45:
				continue
			value = data[3] * scale
			minimum = data[4] * scale if len(data) > 4 else None
			maximum = data[5] * scale if len(data) > 5 else None
			passed = None
			if minimum is not None and maximum is not None:
				passed = minimum <= value <= maximum
			report.results.append(
				TestResult(sensor, f"Sonde lambda n° {sensor}", tid, name, value, minimum, maximum, unit, passed)
			)
	if not report.results:
		report.unavailable = "Le calculateur ne répond pas aux requêtes Mode 05."
	return report


MODE08_TESTS = {0x01: "Étanchéité du système antiévaporation (fermeture de la mise à l'air)"}


def read_mode08_support(link: ElmLink) -> list[int]:
	"""Identifiants de test Mode 08 supportés (lecture seule)."""
	return _bitmap(link, "08", "0000000000" if is_can(link.protocol_code) else "")


def run_mode08(link: ElmLink, tid: int) -> list[str]:
	"""Lance un test Mode 08 (commande d'un organe). À réserver à un utilisateur averti."""
	if tid not in MODE08_TESTS:
		raise ObdError(ErrorKind.BLOCKED, f"Test Mode 08 {tid:02X} non autorisé par l'application.")
	request = f"08{tid:02X}" + ("0000000000" if is_can(link.protocol_code) else "")
	log.warning("Exécution du test Mode 08 %02X à la demande de l'utilisateur", tid)
	return link.send_raw(request, timeout=10.0)
