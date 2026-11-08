"""Session de diagnostic Renault sur CAN (adressage physique), en lecture seule.

L'ELM327 est configuré pour un calculateur précis (en-tête d'émission, filtre de réception, trames de contrôle
de flux ISO-TP), une session de diagnostic est ouverte, puis seules des requêtes de lecture sont envoyées.
À la fermeture, le calculateur est remis en session par défaut et l'adaptateur retrouve sa configuration OBD.
"""

from __future__ import annotations

import logging
import time
from types import TracebackType

from obdtool.core.ddt.database import AutoIdent
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ErrorKind, ObdError

log = logging.getLogger(__name__)

# Services autorisés : lecture et identification uniquement.
READ_SERVICES = {0x17, 0x18, 0x19, 0x1A, 0x21, 0x22, 0x3E}
# Sessions autorisées : par défaut (01, 81), diagnostic après-vente Renault (C0), étendue (03).
ALLOWED_SESSIONS = {0x01, 0x81, 0xC0, 0x03}
KEEPALIVE_S = 3.0
# Effacement de la mémoire de défauts constructeur (KWP2000 ClearDiagnosticInformation, tous les groupes).
CLEAR_ALL_FAULTS = bytes.fromhex("14FF00")

NEGATIVE_CODES = {
	0x10: "refus général",
	0x11: "service non supporté",
	0x12: "sous-fonction non supportée",
	0x13: "longueur incorrecte",
	0x21: "occupé, recommencer",
	0x22: "conditions non remplies",
	0x31: "hors plage",
	0x33: "accès refusé (sécurité)",
	0x78: "réponse en attente",
	0x7E: "non supporté dans cette session",
	0x7F: "service non supporté dans cette session",
}


def check_read_only(request: str) -> bytes:
	"""Vérifie qu'une requête est une lecture autorisée ; retourne ses octets, lève BLOCKED sinon."""
	text = request.replace(" ", "").upper()
	try:
		data = bytes.fromhex(text)
	except ValueError as exc:
		raise ObdError(ErrorKind.INVALID_RESPONSE, f"requête invalide : {request}") from exc
	if not data:
		raise ObdError(ErrorKind.BLOCKED, "requête vide")
	service = data[0]
	if service == 0x10 and len(data) == 2 and data[1] in ALLOWED_SESSIONS:
		return data
	if service in READ_SERVICES:
		return data
	raise ObdError(ErrorKind.BLOCKED, f"(service 0x{service:02X} refusé : module en lecture seule)")


def reassemble(lines: list[str], rx_id: str) -> bytes:
	"""Reconstitue une réponse ISO-TP à partir des trames affichées avec en-tête (« 765 10 1A 61 80 … »)."""
	payload = bytearray()
	expected = None
	for line in lines:
		tokens = line.upper().split()
		if len(tokens) < 2 or tokens[0] != rx_id.upper():
			continue
		try:
			frame = bytes(int(t, 16) for t in tokens[1:])
		except ValueError:
			continue
		kind = frame[0] >> 4
		if kind == 0:
			length = frame[0] & 0x0F
			return bytes(frame[1 : 1 + length])
		if kind == 1:
			expected = ((frame[0] & 0x0F) << 8) | frame[1]
			payload = bytearray(frame[2:])
		elif kind == 2 and expected is not None:
			payload.extend(frame[1:])
	if expected is None:
		return b""
	return bytes(payload[:expected])


class RenaultSession:
	"""Dialogue en lecture seule avec un calculateur Renault (CAN 11 bits, 500 kbit/s)."""

	def __init__(self, link: ElmLink, tx_id: str, rx_id: str, session: str = "10C0") -> None:
		self.link = link
		self.tx_id = tx_id.upper()
		self.rx_id = rx_id.upper()
		self.session = session
		self._last = 0.0
		self._open = False

	def __enter__(self) -> RenaultSession:
		self.open()
		return self

	def __exit__(
		self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
	) -> None:
		self.close()

	def _at(self, command: str) -> None:
		self.link.send_raw(command, check=False, timeout=2.0)

	def open(self, start_session: bool = True) -> None:
		"""Configure l'adaptateur pour ce calculateur et ouvre la session de diagnostic."""
		for command in (
			"ATSP6",
			"ATH1",
			"ATCAF1",
			f"ATSH{self.tx_id}",
			f"ATFCSH{self.tx_id}",
			"ATFCSD300000",
			"ATFCSM1",
			f"ATCRA{self.rx_id}",
			"ATSTFF",
		):
			self._at(command)
		self._open = True
		if start_session:
			response = self.request(self.session, allow_negative=True)
			if not response or response[0] != 0x50:
				raise ObdError(
					ErrorKind.UNABLE_TO_CONNECT,
					f"Le calculateur {self.tx_id} n'a pas ouvert la session de diagnostic ({self.session}).",
				)

	def request(self, request: str, allow_negative: bool = False) -> bytes:
		"""Envoie une requête de lecture et retourne la réponse complète (octet de service compris)."""
		return self._exchange(check_read_only(request), allow_negative)

	def clear_fault_memory(self, allow_negative: bool = True) -> bytes:
		"""Efface la mémoire de défauts constructeur (KWP2000 14 FF 00, tous les groupes).

		Seule exception au mode lecture seule, réservée à l'écran Défauts (double confirmation de l'utilisateur).
		"""
		return self._exchange(CLEAR_ALL_FAULTS, allow_negative, timeout=6.0)

	def _exchange(self, data: bytes, allow_negative: bool, timeout: float = 4.0) -> bytes:
		if self._open and self.session and time.monotonic() - self._last > KEEPALIVE_S and data[0] != 0x10:
			self._exchange(check_read_only(self.session), allow_negative=True)
		hex_request = data.hex().upper()
		for _attempt in range(5):
			lines = self.link.send_raw(hex_request, check=False, timeout=timeout, clean=True)
			self._last = time.monotonic()
			response = reassemble(lines, self.rx_id)
			if not response:
				if any("NO DATA" in line for line in lines):
					raise ObdError(ErrorKind.NO_DATA, f"(calculateur {self.tx_id}, requête {hex_request})")
				raise ObdError(ErrorKind.INVALID_RESPONSE, f"(calculateur {self.tx_id}, réponse {lines})")
			if response[0] == 0x7F:
				code = response[2] if len(response) > 2 else 0
				if code == 0x78:
					time.sleep(0.2)
					continue
				if allow_negative:
					return response
				reason = NEGATIVE_CODES.get(code, f"code 0x{code:02X}")
				raise ObdError(ErrorKind.NEGATIVE_RESPONSE, f"({hex_request} : {reason})")
			return response
		raise ObdError(ErrorKind.TIMEOUT, f"(calculateur {self.tx_id} toujours occupé)")

	def close(self) -> None:
		"""Session par défaut, puis configuration OBD standard de l'adaptateur."""
		if not self._open:
			return
		self._open = False
		try:
			self._exchange(check_read_only("1081"), allow_negative=True)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
		finally:
			try:
				self._at("ATAR")
				self._at("ATST32")
				self.link.restore_defaults()
			except ObdError as exc:
				log.debug("Rétablissement de la configuration OBD : %s", exc.user_message)


def parse_ident_2180(response: bytes) -> AutoIdent | None:
	"""Identification « ancienne génération » Renault : réponse 61 80 … (positions reprises de DDT4All)."""
	if len(response) < 20 or response[:2] != b"\x61\x80":
		return None
	diagversion = str(response[7])
	supplier = response[8:11].decode("ascii", errors="replace")
	soft = response[16:18].hex().upper()
	version = response[18:20].hex().upper()
	return AutoIdent(diagversion, supplier, soft, version)


def identify(session: RenaultSession) -> AutoIdent | None:
	"""Identifie le calculateur (2180, puis UDS 22F1A0 / F18A / F194 / F195)."""
	try:
		ident = parse_ident_2180(session.request("2180"))
		if ident is not None:
			return ident
	except ObdError as exc:
		if exc.is_link_lost:
			raise
	try:
		diag = session.request("22F1A0")
		supplier = session.request("22F18A")
		soft = session.request("22F194")
		version = session.request("22F195")
	except ObdError as exc:
		if exc.is_link_lost:
			raise
		return None
	return AutoIdent(
		str(diag[3]) if len(diag) > 3 else "",
		supplier[3:].decode("ascii", errors="replace").strip("\x00 "),
		soft[3:].rstrip(b"\x00\xff ").decode("ascii", errors="replace"),
		version[3:].rstrip(b"\x00\xff ").decode("ascii", errors="replace"),
	)
