"""Identification de l'adaptateur, détection des clones et interprétation de la tension."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# Versions officielles publiées par ELM Electronics.
GENUINE_VERSIONS = {
	"1.0",
	"1.0a",
	"1.1",
	"1.2",
	"1.2a",
	"1.3",
	"1.3a",
	"1.4",
	"1.4a",
	"1.4b",
	"2.0",
	"2.1",
	"2.2",
	"2.3",
}
# Versions qui n'existent pas chez ELM Electronics : forcément des clones.
FAKE_VERSIONS = {"1.5", "1.5a", "1.6", "2.4", "2.5", "3.0"}

# Au-dessus de cette tension, l'alternateur charge : le moteur tourne.
ENGINE_RUNNING_VOLTAGE = 13.2

# Commandes de lecture sans effet, et version minimale de l'ELM327 qui les connaît.
PROBE_COMMANDS: tuple[tuple[str, str], ...] = (
	("ATPPS", "1.1"),
	("ATCS", "1.0"),
	("ATIGN", "1.4"),
	("ATRD", "1.4"),
	("STI", ""),  # puces STN (OBDLink) : bonne qualité
)


class VehicleState(Enum):
	"""État déduit de la tension et de la réponse du calculateur."""

	UNKNOWN = "Inconnu"
	NOT_PLUGGED = "Adaptateur probablement pas branché sur la prise OBD"
	ECU_SILENT = "Le calculateur ne répond pas (contact coupé ?)"
	IGNITION_ON = "Contact mis, moteur arrêté"
	ENGINE_RUNNING = "Moteur tournant (alternateur en charge)"
	ECU_RESPONDING = "Calculateur en communication"


@dataclass
class AdapterInfo:
	"""Informations lues sur l'adaptateur après connexion."""

	version: str = ""  # réponse ATI
	description: str = ""  # réponse AT@1
	identifier: str = ""  # réponse AT@2
	voltage: float | None = None
	voltage_raw: str = ""
	protocol_code: str = ""
	protocol_name: str = ""
	protocol_auto: bool = False
	ignition: str = ""  # réponse ATIGN
	stn: str = ""  # réponse STI (puces STN)
	ecu_responding: bool = False
	ecu_error: str = ""
	probes: dict[str, bool] = field(default_factory=dict)
	warnings: list[str] = field(default_factory=list)

	@property
	def version_number(self) -> str:
		return parse_version(self.version)

	@property
	def vehicle_state(self) -> VehicleState:
		return assess_vehicle_state(self.voltage, self.ecu_responding)


def parse_version(ati: str) -> str:
	"""Extrait le numéro de version d'une réponse ATI (« ELM327 v1.4b » → « 1.4b »)."""
	match = re.search(r"v\s*(\d+\.\d+[a-z]?)", ati, re.IGNORECASE)
	return match.group(1).lower() if match else ""


def parse_voltage(answer: str) -> float | None:
	"""Décode la réponse à ATRV (« 12.6V »)."""
	match = re.search(r"(\d+(?:[.,]\d+)?)\s*V?", answer.strip(), re.IGNORECASE)
	if not match:
		return None
	try:
		return float(match.group(1).replace(",", "."))
	except ValueError:
		return None


def _version_tuple(version: str) -> tuple[int, int, str]:
	match = re.match(r"(\d+)\.(\d+)([a-z]?)", version)
	if not match:
		return (0, 0, "")
	return (int(match.group(1)), int(match.group(2)), match.group(3))


def analyse_clone(info: AdapterInfo) -> list[str]:
	"""Retourne la liste des indices de clone ELM327 de qualité douteuse."""
	warnings: list[str] = []
	if info.stn:
		return warnings
	version = info.version_number
	if not info.version.upper().startswith("ELM327") and info.version:
		warnings.append(f"Identification inhabituelle (« {info.version} ») : ce n'est peut-être pas un ELM327.")
	if not version:
		warnings.append("Version de l'adaptateur illisible.")
		return warnings
	if version in FAKE_VERSIONS:
		warnings.append(
			f"La version v{version} n'existe pas chez ELM Electronics : c'est un clone. "
			"Certaines fonctions peuvent manquer ou mal fonctionner."
		)
	elif version == "2.1":
		warnings.append(
			"Version v2.1 : la grande majorité des adaptateurs annonçant v2.1 sont des clones au "
			"micrologiciel incomplet (réponses lentes, commandes manquantes, protocoles K-Line défaillants)."
		)
	elif version not in GENUINE_VERSIONS:
		warnings.append(f"Version v{version} inconnue : adaptateur non officiel.")
	current = _version_tuple(version)
	missing = []
	for command, minimum in PROBE_COMMANDS:
		if not minimum or command not in info.probes:
			continue
		if current >= _version_tuple(minimum) and not info.probes[command]:
			missing.append(command)
	if missing:
		warnings.append(
			f"L'adaptateur annonce v{version} mais ne reconnaît pas {', '.join(missing)} : "
			"micrologiciel cloné ou incomplet."
		)
	return warnings


def assess_voltage(voltage: float | None) -> str:
	"""Commentaire sur la tension mesurée par l'adaptateur (broche 16 de la prise OBD)."""
	if voltage is None:
		return "tension non disponible"
	if voltage < 3.0:
		return "quasi nulle : adaptateur non alimenté par la prise OBD"
	if voltage > 16.5:
		return "incohérente : adaptateur probablement pas branché sur le véhicule, ou mesure défaillante"
	if voltage < 11.8:
		return "batterie faible : risque d'échec du démarrage et de communications instables"
	if voltage < ENGINE_RUNNING_VOLTAGE:
		return "moteur probablement arrêté"
	if voltage <= 14.8:
		return "alternateur en charge, moteur probablement tournant"
	return "tension élevée : régulateur d'alternateur à vérifier"


def assess_vehicle_state(voltage: float | None, ecu_responding: bool) -> VehicleState:
	"""Déduit l'état du véhicule à partir de la tension et de la réponse du calculateur."""
	if voltage is not None and (voltage < 3.0 or voltage > 16.5):
		return VehicleState.NOT_PLUGGED if not ecu_responding else VehicleState.ECU_RESPONDING
	if not ecu_responding:
		return VehicleState.ECU_SILENT if voltage is not None else VehicleState.UNKNOWN
	if voltage is None:
		return VehicleState.ECU_RESPONDING
	return VehicleState.ENGINE_RUNNING if voltage >= ENGINE_RUNNING_VOLTAGE else VehicleState.IGNITION_ON
