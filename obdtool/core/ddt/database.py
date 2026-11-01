"""Base de définitions de calculateurs au format DDT4All (`ecu.zip` : `db.json` + un JSON par calculateur).

La base n'est pas fournie avec OBDTool : l'utilisateur indique le fichier `ecu.zip` (ou un dossier extrait) de
son installation DDT4All. Le format est documenté par le code source de DDT4All (GPL-3.0).
"""

from __future__ import annotations

import json
import logging
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Adresses de diagnostic Renault courantes (repli si la base ne donne pas les identifiants CAN).
KNOWN_CAN_ADDRESSES: dict[str, tuple[str, str, str]] = {
	"7A": ("7E0", "7E8", "Injection"),
	"26": ("745", "765", "UCH (boîtier habitacle)"),
	"51": ("743", "763", "Tableau de bord"),
}


@dataclass(frozen=True)
class AutoIdent:
	"""Signature d'identification d'une version de calculateur."""

	diagversion: str
	supplier: str
	soft: str
	version: str

	def matches(self, other: AutoIdent) -> bool:
		"""Correspondance exacte (même règle que DDT4All : préfixes pour fournisseur, logiciel, version)."""
		try:
			if int(self.diagversion or "0", 16) != int(other.diagversion or "0", 16):
				return False
		except ValueError:
			return False
		for mine, theirs in ((self.supplier, other.supplier), (self.soft, other.soft), (self.version, other.version)):
			if mine.strip() != theirs.strip()[: len(mine.strip())]:
				return False
		return True

	def approximately(self, other: AutoIdent) -> bool:
		"""Correspondance approchée : même fournisseur et même logiciel."""
		return self.supplier.strip() == other.supplier.strip() and self.soft.strip() == other.soft.strip()


@dataclass
class DdtTarget:
	"""Entrée de l'index : un fichier de définition de calculateur."""

	href: str
	name: str
	group: str
	address: str
	protocol: str
	projects: list[str] = field(default_factory=list)
	autoidents: list[AutoIdent] = field(default_factory=list)


@dataclass
class DdtDataItem:
	"""Position d'une donnée dans une trame."""

	name: str
	firstbyte: int = 1
	bitoffset: int = 0
	endian: str = ""


@dataclass
class DdtData:
	"""Définition d'une donnée (codage, échelle, liste de valeurs)."""

	name: str
	bitscount: int = 8
	bytescount: int = 1
	scaled: bool = False
	signed: bool = False
	bytesascii: bool = False
	step: float = 1.0
	offset: float = 0.0
	divideby: float = 1.0
	fmt: str = ""
	unit: str = ""
	description: str = ""
	comment: str = ""
	lists: dict[int, str] = field(default_factory=dict)


@dataclass
class DdtRequest:
	"""Requête d'un calculateur."""

	name: str
	sentbytes: str
	replybytes: str = ""
	minbytes: int = 0
	dataitems: dict[str, DdtDataItem] = field(default_factory=dict)
	send_dataitems: dict[str, DdtDataItem] = field(default_factory=dict)

	@property
	def service(self) -> int | None:
		try:
			return int(self.sentbytes[:2], 16)
		except ValueError:
			return None


@dataclass
class DdtValue:
	"""Valeur décodée."""

	request: str
	name: str
	raw: str
	display: str
	unit: str
	description: str


@dataclass
class DdtEcu:
	"""Définition complète d'un calculateur."""

	name: str
	protocol: str
	send_id: str
	recv_id: str
	endian: str
	requests: dict[str, DdtRequest]
	data: dict[str, DdtData]

	def decode(self, request: DdtRequest, response: bytes) -> list[DdtValue]:
		"""Décode toutes les données d'une réponse (réponse complète, octet de service compris)."""
		values = []
		for name, item in request.dataitems.items():
			definition = self.data.get(name)
			if definition is None:
				continue
			raw = extract_bits(response, item, definition.bitscount, self.endian)
			if raw is None:
				continue
			values.append(
				DdtValue(
					request.name,
					name,
					format(raw, f"0{max(1, math.ceil(definition.bitscount / 4))}X"),
					display_value(definition, raw),
					definition.unit,
					definition.description or definition.comment,
				)
			)
		return values


def extract_bits(response: bytes, item: DdtDataItem, bits: int, ecu_endian: str) -> int | None:
	"""Extrait `bits` bits à partir de l'octet `firstbyte` (1 = premier octet) et du bit `bitoffset`.

	Codage gros-boutiste par défaut (bit 0 = bit de poids fort du premier octet) ; petit-boutiste si la
	donnée ou le calculateur le précise (même convention que DDT4All).
	"""
	start = item.firstbyte - 1
	if start < 0:
		return None
	needed = math.ceil((bits + item.bitoffset) / 8)
	chunk = response[start : start + needed]
	if len(chunk) < math.ceil(bits / 8) or not chunk:
		return None
	little = (item.endian or ecu_endian).lower() == "little"
	if little and needed > 1:
		chunk = chunk[::-1]
	binary = "".join(f"{b:08b}" for b in chunk)
	if little:
		end = len(binary) - item.bitoffset
		segment = binary[max(0, end - bits) : end]
	else:
		segment = binary[item.bitoffset : item.bitoffset + bits]
	if not segment:
		return None
	return int(segment, 2)


def display_value(definition: DdtData, raw: int) -> str:
	"""Valeur affichable : liste de valeurs, texte ASCII, valeur mise à l'échelle ou hexadécimale."""
	if definition.bytesascii:
		length = max(1, math.ceil(definition.bitscount / 8))
		text = raw.to_bytes(length, "big").decode("ascii", errors="replace")
		return text.replace("\x00", "").strip()
	value = raw
	if definition.signed:
		width = definition.bitscount
		if value & (1 << (width - 1)):
			value -= 1 << width
	if value in definition.lists:
		return definition.lists[value]
	if raw in definition.lists:
		return definition.lists[raw]
	if not definition.scaled:
		return f"{raw:X}" if definition.bitscount > 1 else str(raw)
	if not definition.divideby:
		return f"{raw:X}"
	result = (value * definition.step + definition.offset) / definition.divideby
	if "." in definition.fmt:
		decimals = len(definition.fmt.split(".")[1])
		return f"{result:.{decimals}f}".replace(".", ",")
	return str(int(result)) if float(result).is_integer() else f"{result:g}".replace(".", ",")


def _items(raw: dict[str, Any]) -> dict[str, DdtDataItem]:
	items = {}
	for name, spec in (raw or {}).items():
		spec = spec if isinstance(spec, dict) else {}
		items[name] = DdtDataItem(
			name, int(spec.get("firstbyte", 1) or 1), int(spec.get("bitoffset", 0) or 0), str(spec.get("endian", ""))
		)
	return items


def parse_ecu(text: str | bytes) -> DdtEcu:
	"""Analyse un fichier JSON de calculateur."""
	raw = json.loads(text)
	obd = raw.get("obd", {})
	requests = {}
	for spec in raw.get("requests", []):
		if not isinstance(spec, dict) or "name" not in spec:
			continue
		request = DdtRequest(
			name=str(spec["name"]),
			sentbytes=str(spec.get("sentbytes", "")).replace(" ", "").upper(),
			replybytes=str(spec.get("replybytes", "")).replace(" ", "").upper(),
			minbytes=int(spec.get("minbytes", 0) or 0),
			dataitems=_items(spec.get("receivebyte_dataitems", {})),
			send_dataitems=_items(spec.get("sendbyte_dataitems", {})),
		)
		requests[request.name] = request
	data = {}
	for name, spec in (raw.get("data") or {}).items():
		if not isinstance(spec, dict):
			continue
		lists = {}
		for key, label in (spec.get("lists") or {}).items():
			try:
				lists[int(key)] = str(label)
			except (TypeError, ValueError):
				continue
		bits = int(spec.get("bitscount", 8) or 8)
		data[name] = DdtData(
			name=name,
			bitscount=bits,
			bytescount=int(spec.get("bytescount", math.ceil(bits / 8)) or 1),
			scaled=bool(spec.get("scaled", False)),
			signed=bool(spec.get("signed", False)),
			bytesascii=bool(spec.get("bytesascii", False)),
			step=float(spec.get("step", 1) or 1),
			offset=float(spec.get("offset", 0) or 0),
			divideby=float(spec.get("divideby", 1) or 0),
			fmt=str(spec.get("format", "") or ""),
			unit=str(spec.get("unit", "") or ""),
			description=str(spec.get("description", "") or ""),
			comment=str(spec.get("comment", "") or ""),
			lists=lists,
		)
	return DdtEcu(
		name=str(raw.get("ecuname", "")),
		protocol=str(obd.get("protocol", "")),
		send_id=str(obd.get("send_id", "")).upper(),
		recv_id=str(obd.get("recv_id", "")).upper(),
		endian=str(raw.get("endian", "")),
		requests=requests,
		data=data,
	)


class DdtDatabase:
	"""Index de la base et accès aux fichiers de calculateurs."""

	def __init__(self, path: Path) -> None:
		self.path = Path(path)
		self._zip: zipfile.ZipFile | None = None
		if self.path.is_file():
			try:
				self._zip = zipfile.ZipFile(self.path)
			except zipfile.BadZipFile as exc:
				raise ValueError(f"{self.path} n'est pas une archive ZIP valide.") from exc
		elif not self.path.is_dir():
			raise ValueError(f"Base introuvable : {self.path}")
		try:
			index = json.loads(self._read("db.json"))
		except (KeyError, FileNotFoundError) as exc:
			raise ValueError("Fichier db.json absent : ce n'est pas une base DDT4All (ecu.zip).") from exc
		except json.JSONDecodeError as exc:
			raise ValueError(f"db.json illisible : {exc}") from exc
		self.targets: list[DdtTarget] = []
		for href, spec in index.items() if isinstance(index, dict) else []:
			if not isinstance(spec, dict):
				continue
			idents = [
				AutoIdent(
					str(a.get("diagnostic_version", "")),
					str(a.get("supplier_code", "")),
					str(a.get("soft_version", "")),
					str(a.get("version", "")),
				)
				for a in spec.get("autoidents", [])
				if isinstance(a, dict)
			]
			self.targets.append(
				DdtTarget(
					href=str(href),
					name=str(spec.get("ecuname", href)),
					group=str(spec.get("group", "")),
					address=str(spec.get("address", "")).upper().zfill(2),
					protocol=str(spec.get("protocol", "")),
					projects=[str(p) for p in spec.get("projects", [])],
					autoidents=idents,
				)
			)
		log.info("Base DDT4All chargée : %s (%d calculateurs)", self.path, len(self.targets))

	def _read(self, name: str) -> bytes:
		if self._zip is not None:
			names = self._zip.namelist()
			if name in names:
				return self._zip.read(name)
			base = Path(name).name
			for candidate in names:
				if Path(candidate).name == base:
					return self._zip.read(candidate)
			raise KeyError(name)
		path = self.path / name
		if not path.exists():
			path = self.path / Path(name).name
		return path.read_bytes()

	def projects(self) -> list[str]:
		return sorted({p for t in self.targets for p in t.projects})

	def groups(self, project: str | None = None) -> dict[str, str]:
		"""Adresse → nom de groupe, pour les calculateurs CAN (filtrés par projet véhicule)."""
		result: dict[str, str] = {}
		for target in self.targets:
			if "CAN" not in target.protocol.upper():
				continue
			if project and project not in target.projects:
				continue
			result.setdefault(target.address, target.group)
		return dict(sorted(result.items()))

	def candidates(self, address: str, project: str | None = None) -> list[DdtTarget]:
		address = address.upper().zfill(2)
		return [
			t
			for t in self.targets
			if t.address == address and "CAN" in t.protocol.upper() and (not project or project in t.projects)
		]

	def match(self, address: str, ident: AutoIdent, project: str | None = None) -> tuple[DdtTarget | None, bool]:
		"""Fichier correspondant à une identification : (cible, correspondance exacte)."""
		candidates = self.candidates(address, project) or self.candidates(address)
		for target in candidates:
			if any(a.matches(ident) for a in target.autoidents if a.diagversion):
				return target, True
		for target in candidates:
			if any(a.approximately(ident) for a in target.autoidents if a.diagversion):
				return target, False
		return None, False

	def load_ecu(self, target: DdtTarget) -> DdtEcu:
		return parse_ecu(self._read(target.href))

	def can_ids(self, address: str, project: str | None = None) -> tuple[str, str] | None:
		"""Identifiants CAN (émission, réception) d'une adresse, d'après la base ou la table connue."""
		for target in self.candidates(address, project) or self.candidates(address):
			try:
				ecu = self.load_ecu(target)
			except (KeyError, OSError, ValueError, json.JSONDecodeError):
				continue
			if ecu.send_id and ecu.recv_id:
				return ecu.send_id, ecu.recv_id
		known = KNOWN_CAN_ADDRESSES.get(address.upper())
		return (known[0], known[1]) if known else None
