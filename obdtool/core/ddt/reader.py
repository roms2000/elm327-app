"""Identification des calculateurs et lecture de leurs paramètres (configuration comprise), en lecture seule."""

from __future__ import annotations

import csv
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from obdtool.core.ddt.database import AutoIdent, DdtDatabase, DdtEcu, DdtRequest, DdtTarget, DdtValue
from obdtool.core.ddt.session import READ_SERVICES, RenaultSession, identify
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ErrorKind, ObdError

log = logging.getLogger(__name__)

# Mots-clés des paramètres de personnalisation recherchés par l'utilisateur (repérés à l'affichage).
INTEREST_KEYWORDS = (
	"clignot",
	"impuls",
	"autoroute",
	"bip",
	"buzzer",
	"sonore",
	"réserve",
	"reserve",
	"carburant",
	"verrouill",
	"condamn",
	"accompagnement",
	"follow",
	"plafonnier",
	"vitre",
	"essuie",
	"ceinture",
	"feux de jour",
	"allumage auto",
)


@dataclass
class EcuScanResult:
	"""Résultat de l'identification d'une adresse."""

	address: str
	group: str
	tx_id: str = ""
	rx_id: str = ""
	ident: AutoIdent | None = None
	target: DdtTarget | None = None
	exact: bool = False
	error: str = ""

	@property
	def status(self) -> str:
		if self.error:
			return self.error
		if self.ident is None:
			return "pas de réponse"
		if self.target is None:
			return "répond, version absente de la base"
		return "identifié" if self.exact else "identifié (correspondance approchée)"


@dataclass
class EcuReadout:
	"""Paramètres lus sur un calculateur."""

	scan: EcuScanResult
	values: list[DdtValue] = field(default_factory=list)
	failed: dict[str, str] = field(default_factory=dict)
	skipped: list[str] = field(default_factory=list)


def is_config_request(request: DdtRequest) -> bool:
	"""Requête de lecture de configuration (convention Renault : « LC_ », « Lecture configuration »…)."""
	name = request.name.lower()
	return name.startswith("lc_") or "config" in name or name.startswith("lecture cf")


def is_interesting(value: DdtValue) -> bool:
	text = f"{value.name} {value.description} {value.request}".lower()
	return any(keyword in text for keyword in INTEREST_KEYWORDS)


def readable_requests(ecu: DdtEcu, only_config: bool = False) -> tuple[list[DdtRequest], list[str]]:
	"""Requêtes envoyables en lecture seule (sans paramètre à saisir) ; et noms des requêtes écartées."""
	usable, skipped = [], []
	for request in ecu.requests.values():
		if request.service not in READ_SERVICES or request.service == 0x3E or not request.dataitems:
			continue
		if request.send_dataitems:
			skipped.append(request.name)
			continue
		if only_config and not is_config_request(request):
			continue
		usable.append(request)
	return usable, skipped


def scan_addresses(
	link: ElmLink,
	db: DdtDatabase,
	addresses: list[str],
	project: str | None = None,
	progress: Callable[[str], None] | None = None,
) -> list[EcuScanResult]:
	"""Identifie les calculateurs aux adresses données."""
	groups = db.groups(project) or db.groups()
	results = []
	for address in addresses:
		result = EcuScanResult(address, groups.get(address, ""))
		if progress:
			progress(f"Identification de l'adresse {address} ({result.group})…")
		ids = db.can_ids(address, project)
		if ids is None:
			result.error = "identifiants CAN inconnus"
			results.append(result)
			continue
		result.tx_id, result.rx_id = ids
		try:
			with RenaultSession(link, result.tx_id, result.rx_id) as session:
				result.ident = identify(session)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			result.error = exc.user_message
		if result.ident is not None:
			result.target, result.exact = db.match(address, result.ident, project)
		log.info("Adresse %s : %s %s", address, result.status, result.ident)
		results.append(result)
	return results


def read_ecu(
	link: ElmLink,
	db: DdtDatabase,
	scan: EcuScanResult,
	only_config: bool = False,
	progress: Callable[[str], None] | None = None,
) -> EcuReadout:
	"""Lit toutes les requêtes de lecture (ou celles de configuration) du calculateur identifié."""
	if scan.target is None:
		raise ObdError(ErrorKind.UNSUPPORTED, "(calculateur non identifié dans la base)")
	ecu = db.load_ecu(scan.target)
	requests, skipped = readable_requests(ecu, only_config)
	readout = EcuReadout(scan, skipped=skipped)
	tx = ecu.send_id or scan.tx_id
	rx = ecu.recv_id or scan.rx_id
	with RenaultSession(link, tx, rx) as session:
		for index, request in enumerate(requests, start=1):
			if progress:
				progress(f"Lecture {index}/{len(requests)} : {request.name}")
			try:
				response = session.request(request.sentbytes)
			except ObdError as exc:
				if exc.is_link_lost:
					raise
				readout.failed[request.name] = exc.user_message
				continue
			readout.values.extend(ecu.decode(request, response))
	return readout


def export_csv(readout: EcuReadout, path: Path) -> int:
	"""Sauvegarde des valeurs lues (référence avant toute future modification)."""
	with path.open("w", newline="", encoding="utf-8") as fh:
		writer = csv.writer(fh, delimiter=";")
		scan = readout.scan
		writer.writerow(["# calculateur", scan.group, scan.address, scan.target.name if scan.target else ""])
		if scan.ident:
			writer.writerow(
				["# identification", scan.ident.diagversion, scan.ident.supplier, scan.ident.soft, scan.ident.version]
			)
		writer.writerow(["requete", "parametre", "brut", "valeur", "unite", "description"])
		for value in readout.values:
			writer.writerow([value.request, value.name, value.raw, value.display, value.unit, value.description])
	return len(readout.values)
