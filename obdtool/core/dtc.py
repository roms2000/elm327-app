"""Codes défaut : lecture (modes 03, 07, 0A), effacement (mode 04), descriptions, freeze frame (mode 02)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from importlib import resources
from typing import Any

from obd.codes import DTC as SAE_EN

from obdtool.core import paths, pids
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.poller import decode_messages

log = logging.getLogger(__name__)


class DtcKind(Enum):
	"""Origine d'un code défaut."""

	STORED = ("03", "Mémorisé")
	PENDING = ("07", "En attente")
	PERMANENT = ("0A", "Permanent")

	@property
	def mode(self) -> str:
		return self.value[0]

	@property
	def label(self) -> str:
		return self.value[1]


_SYSTEMS = {"P": "Groupe motopropulseur", "C": "Châssis", "B": "Carrosserie", "U": "Réseau de communication"}
_P0_SUBSYSTEMS = {
	"0": "dosage air/carburant et dépollution auxiliaire",
	"1": "dosage air/carburant",
	"2": "dosage air/carburant (circuit d'injection)",
	"3": "allumage ou ratés d'allumage",
	"4": "dépollution auxiliaire",
	"5": "vitesse véhicule, ralenti, entrées auxiliaires",
	"6": "calculateur et sorties",
	"7": "transmission",
	"8": "transmission",
	"9": "transmission",
	"A": "propulsion hybride",
	"B": "propulsion hybride",
	"C": "propulsion hybride",
}


@dataclass(frozen=True)
class DtcDescription:
	"""Description d'un code et fiabilité de la source."""

	text: str
	source: str  # fr | sae_en | constructeur | personnalisé | générique
	verified: bool = True

	@property
	def display(self) -> str:
		suffix = ""
		if self.source == "sae_en":
			suffix = " (description SAE en anglais)"
		elif not self.verified:
			suffix = " (non vérifié)"
		return self.text + suffix


@dataclass
class DtcEntry:
	"""Code défaut lu sur le calculateur."""

	code: str
	kind: DtcKind
	description: DtcDescription
	ecu: str = ""


def code_category(code: str) -> str:
	"""Description structurelle d'un code (système, générique/constructeur, sous-système)."""
	if len(code) != 5:
		return "Code invalide"
	system = _SYSTEMS.get(code[0], "Système inconnu")
	second = code[1]
	if code[0] == "P":
		origin = {"0": "générique SAE", "1": "constructeur", "2": "générique SAE", "3": "constructeur ou SAE"}.get(
			second, "inconnu"
		)
		sub = _P0_SUBSYSTEMS.get(code[2], "")
		return f"{system}, code {origin}" + (f", {sub}" if sub and second in "02" else "")
	origin = {"0": "générique SAE", "1": "constructeur", "2": "constructeur", "3": "générique SAE"}.get(
		second, "inconnu"
	)
	return f"{system}, code {origin}"


@cache
def _load_json(name: str) -> dict[str, Any]:
	try:
		text = resources.files("obdtool.core").joinpath("data", name).read_text(encoding="utf-8")
		return json.loads(text)
	except (OSError, json.JSONDecodeError) as exc:
		log.error("Base de codes %s illisible : %s", name, exc)
		return {}


def _load_custom() -> dict[str, Any]:
	path = paths.config_dir() / "dtc_custom.json"
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError) as exc:
		log.warning("Fichier de codes personnalisés illisible (%s) : %s", path, exc)
		return {}


def describe(code: str, make: str = "renault") -> DtcDescription:
	"""Description d'un code : personnalisée, constructeur, française, SAE anglaise ou générique."""
	code = code.upper()
	custom = _load_custom()
	for table in (custom.get(make.lower(), {}), custom):
		entry = table.get(code) if isinstance(table, dict) else None
		if isinstance(entry, dict) and "description" in entry:
			return DtcDescription(str(entry["description"]), "personnalisé", bool(entry.get("verified", False)))
		if isinstance(entry, str):
			return DtcDescription(entry, "personnalisé", False)
	maker = _load_json("dtc_manufacturer.json").get(make.lower(), {})
	entry = maker.get(code)
	if isinstance(entry, dict) and "description" in entry:
		return DtcDescription(str(entry["description"]), "constructeur", bool(entry.get("verified", False)))
	french = _load_json("dtc_fr.json").get(code)
	if french:
		return DtcDescription(french, "fr")
	english = SAE_EN.get(code)
	if english:
		return DtcDescription(english, "sae_en")
	return DtcDescription(f"Code non répertorié — {code_category(code)}", "générique", False)


def parse_dtc_bytes(data: bytes) -> list[str]:
	"""Décode une suite de paires d'octets en codes (00 00 = remplissage ignoré)."""
	codes = []
	for index in range(0, len(data) - 1, 2):
		first, second = data[index], data[index + 1]
		if first == 0 and second == 0:
			continue
		letter = "PCBU"[first >> 6]
		codes.append(f"{letter}{(first >> 4) & 0x03}{first & 0x0F:X}{second:02X}")
	return codes


def _payload(message: Any, mode_response: int) -> bytes:
	"""Octets des codes d'une réponse 43/47/4A.

	Les codes vont par paires d'octets. Sur CAN, un octet « nombre de codes » suit normalement le mode
	(python-OBD l'ajoute aussi pour 43 en K-Line) ; certains calculateurs et émulateurs l'omettent.
	Un nombre impair d'octets après le mode signale sa présence.
	"""
	data = bytes(message.data)
	if not data or data[0] != mode_response:
		return b""
	rest = data[1:]
	return rest[1:] if len(rest) % 2 == 1 else rest


def read_dtcs(link: ElmLink, kind: DtcKind, make: str = "renault") -> list[DtcEntry] | None:
	"""Lit les codes d'un mode. Retourne None si le mode n'est pas supporté par le calculateur."""
	try:
		messages = link.request(kind.mode)
	except ObdError as exc:
		if exc.kind in (ErrorKind.NO_DATA, ErrorKind.UNSUPPORTED):
			if kind == DtcKind.PERMANENT:
				return None
			if exc.kind == ErrorKind.NO_DATA:
				return []
			return None
		raise
	response_mode = int(kind.mode, 16) + 0x40
	entries: list[DtcEntry] = []
	seen: set[tuple[str, str]] = set()
	for message in messages:
		ecu = f"{message.tx_id:X}" if isinstance(getattr(message, "tx_id", None), int) else ""
		for code in parse_dtc_bytes(_payload(message, response_mode)):
			if (code, ecu) in seen:
				continue
			seen.add((code, ecu))
			entries.append(DtcEntry(code, kind, describe(code, make), ecu))
	return entries


@dataclass
class MilStatus:
	"""État du voyant moteur et compteur de défauts (Mode 01 PID 01)."""

	mil_on: bool
	dtc_count: int
	ignition: str  # spark | compression


def read_mil_status(link: ElmLink) -> MilStatus | None:
	"""Lit l'état du voyant moteur (None si le PID 01 n'est pas supporté)."""
	try:
		messages = link.request("0101")
	except ObdError as exc:
		if exc.is_link_lost:
			raise
		return None
	data = next((bytes(m.data) for m in messages if len(m.data) >= 6 and m.data[1] == 0x01), b"")
	if not data:
		return None
	a, b = data[2], data[3]
	return MilStatus(bool(a & 0x80), a & 0x7F, "compression" if b & 0x08 else "spark")


def clear_dtcs(link: ElmLink) -> None:
	"""Efface les codes défaut et les données associées (mode 04)."""
	lines = link.send_raw("04", timeout=10.0)
	if not any("44" in line.replace(" ", "")[:8] for line in lines):
		log.warning("Réponse inattendue à l'effacement : %s", lines)
		raise ObdError(
			ErrorKind.NEGATIVE_RESPONSE, "Le calculateur a refusé l'effacement (moteur tournant ?).", raw=lines
		)
	log.info("Codes défaut effacés (mode 04)")


@dataclass
class FreezeFrame:
	"""Instantané enregistré par le calculateur au moment d'un défaut (Mode 02, trame 0)."""

	dtc: str = ""
	description: DtcDescription | None = None
	values: list[tuple[pids.PidDef, float | str | None]] = field(default_factory=list)
	supported: set[int] = field(default_factory=set)


def _freeze_request(link: ElmLink, pid: int, frame: int = 0) -> list[Any]:
	"""Requête Mode 02 ; les messages sont réécrits au format Mode 01 pour réutiliser les décodeurs."""
	messages = link.request(f"02{pid:02X}{frame:02X}")
	rewritten = []
	for message in messages:
		data = bytes(message.data)
		if len(data) >= 3 and data[0] == 0x42 and data[1] == pid:
			message.data = bytearray([0x41, pid]) + bytearray(data[3:])
			rewritten.append(message)
	if not rewritten:
		raise ObdError(ErrorKind.INVALID_RESPONSE, f"(freeze frame PID {pid:02X})")
	return rewritten


def read_freeze_frame(link: ElmLink, make: str = "renault", frame: int = 0) -> FreezeFrame | None:
	"""Lit le freeze frame. Retourne None si le calculateur n'en a pas (ou ne supporte pas le Mode 02)."""
	result = FreezeFrame()
	block = 0
	while block <= 0xE0:
		try:
			messages = _freeze_request(link, block, frame)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			break
		data = bytes(messages[0].data)
		block_pids = pids.decode_supported_bitmap(block, data[2:6]) if len(data) >= 6 else set()
		result.supported |= block_pids
		if (block + 0x20) not in block_pids:
			break
		block += 0x20
	if not result.supported:
		return None
	if 0x02 in result.supported:
		try:
			messages = _freeze_request(link, 0x02, frame)
			codes = parse_dtc_bytes(bytes(messages[0].data[2:4]))
			if codes:
				result.dtc = codes[0]
				result.description = describe(result.dtc, make)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
	if not result.dtc and 0x02 in result.supported:
		# DTC 0000 : aucun freeze frame mémorisé.
		return FreezeFrame(supported=result.supported)
	for pid in sorted(result.supported):
		if pid in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0x02):
			continue
		definitions = [d for d in pids.defs_for_pid(pid) if d.source == "obd"]
		if not definitions:
			continue
		try:
			messages = _freeze_request(link, pid, frame)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			continue
		for definition in definitions:
			try:
				result.values.append((definition, decode_messages(definition, messages)))
			except Exception as exc:
				log.debug("Décodage freeze frame %s impossible : %s", definition.key, exc)
	return result
