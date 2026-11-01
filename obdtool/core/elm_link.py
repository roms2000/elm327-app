"""Liaison avec l'adaptateur ELM327.

Seul module qui manipule python-OBD : sa classe `ELM327` assure l'ouverture du port, la détection du débit,
le choix du protocole et le décodage des trames. La sous-classe `_Elm327` ajoute l'envoi brut (accès aux
attributs internes de python-OBD, isolé ici) pour voir les erreurs (`NO DATA`, `CAN ERROR`…), alimenter la
console et l'écoute du bus.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import serial
from obd import OBDCommand, OBDResponse, OBDStatus
from obd.elm327 import ELM327
from obd.protocols import ISO_15765_4_11bit_500k

from obdtool.core import transport
from obdtool.core.adapter_info import PROBE_COMMANDS, AdapterInfo, analyse_clone, parse_voltage
from obdtool.core.errors import ErrorKind, ObdError, classify, clean_lines
from obdtool.core.protocols import describe_dpn, parse_dpn

log = logging.getLogger(__name__)

# python-OBD installe son propre affichage console à l'import : ses messages passent par nos journaux.
for _handler in list(logging.getLogger("obd").handlers):
	logging.getLogger("obd").removeHandler(_handler)

# Débit fictif pour les connexions TCP : la détection automatique n'a pas de sens sur une socket.
_SOCKET_BAUDRATE = 38400


class _UserCanProtocol(ISO_15765_4_11bit_500k):
	"""Protocoles CAN « utilisateur » B et C, décodés comme du CAN 11 bit."""

	ELM_NAME = "USER CAN (11 bit)"
	ELM_ID = "B"


class _Elm327(ELM327):
	"""ELM327 de python-OBD, avec envoi brut et sélection du protocole par ATSP."""

	def __init__(self, portname: str, baudrate: int | None, protocol: str, timeout: float) -> None:
		self.last_0100: list[str] = []
		self.protocol_error: ErrorKind | None = None
		super().__init__(portname, baudrate, protocol, timeout, check_voltage=False)

	# Délai de réponse par défaut d'une commande (réglage utilisateur), appliqué après l'initialisation.
	response_timeout: float = 10.0

	def raw(self, command: bytes, delay: float | None = None, timeout: float | None = None) -> list[str]:
		"""Envoie une commande et retourne les lignes de réponse brutes ([] si pas d'invite dans le délai).

		Remplace l'envoi de python-OBD, qui relit jusqu'à dix fois en cas de silence (10 × le délai).
		"""
		port = self.serial_port
		if port is None:
			return []
		try:
			port.reset_input_buffer()
			port.write(command + b"\r")
			port.flush()
		except Exception as exc:
			self._lost(f"écriture : {exc}")
			return []
		log.debug("write: %r", command)
		if delay:
			time.sleep(delay)
		deadline = time.monotonic() + (self.response_timeout if timeout is None else timeout)
		buffer = bytearray()
		try:
			while time.monotonic() < deadline:
				data = port.read(port.in_waiting or 1)
				if data:
					buffer.extend(data)
					if self.ELM_PROMPT in buffer:
						break
		except Exception as exc:
			self._lost(f"lecture : {exc}")
			return []
		log.debug("read: %r", bytes(buffer))
		if self.ELM_PROMPT not in buffer:
			return []
		text = bytes(buffer).replace(b"\x00", b"").split(self.ELM_PROMPT)[0].decode("utf-8", "ignore")
		return [line.strip() for line in re.split("[\r\n]", text) if line.strip()]

	def _lost(self, reason: str) -> None:
		log.error("Liaison perdue avec l'adaptateur (%s)", reason)
		port = self.serial_port
		self._set_status(OBDStatus.NOT_CONNECTED)
		self._ELM327__port = None  # type: ignore[attr-defined]
		if port is not None:
			with contextlib.suppress(Exception):
				port.close()

	def write_only(self, data: bytes) -> None:
		"""Écrit sur le port sans attendre de réponse (sans ajouter de retour chariot)."""
		port = self.serial_port
		if port is None:
			raise ObdError(ErrorKind.NOT_CONNECTED)
		port.write(data)
		port.flush()

	@property
	def serial_port(self) -> serial.SerialBase | None:
		return self._ELM327__port  # type: ignore[attr-defined]

	def parse(self, lines: list[str]) -> list[Any]:
		"""Décode des lignes de réponse en messages OBD avec le protocole courant."""
		return self._ELM327__protocol(lines)  # type: ignore[attr-defined]

	def _set_status(self, status: OBDStatus) -> None:
		self._ELM327__status = status  # type: ignore[attr-defined]

	# -- sélection du protocole --
	def set_protocol(self, protocol_: str | None) -> bool:
		"""Sélectionne le protocole avec ATSPx puis vérifie que le véhicule répond à 0100."""
		code = (protocol_ or "0").upper()
		self.protocol_error = None
		self.raw(b"ATSP" + code.encode(), delay=0.1 if code == "0" else None)
		# La recherche automatique du protocole peut durer plus de dix secondes (protocoles K-Line).
		lines = self.raw(b"0100", timeout=max(self.response_timeout, 20.0))
		self.last_0100 = lines
		cleaned = clean_lines(lines)
		error = classify(lines)
		if error is not None or not cleaned:
			self.protocol_error = error or ErrorKind.TIMEOUT
			log.info("Pas de réponse du véhicule à 0100 : %s", lines)
			return False
		dpn = self.raw(b"ATDPN")
		found, _auto = parse_dpn(dpn[0] if dpn else code)
		protocol_class = self._SUPPORTED_PROTOCOLS.get(found)
		if protocol_class is None and found in ("B", "C"):
			protocol_class = _UserCanProtocol
		if protocol_class is None:
			log.warning("Protocole %r non géré par python-OBD", dpn)
			self.protocol_error = ErrorKind.UNSUPPORTED
			return False
		self._ELM327__protocol = protocol_class(cleaned)  # type: ignore[attr-defined]
		return True

	def retry_vehicle(self, protocol: str) -> bool:
		"""Nouvelle tentative de dialogue avec le véhicule (ex. après avoir mis le contact)."""
		if self.set_protocol(protocol):
			self._set_status(OBDStatus.CAR_CONNECTED)
			return True
		return False


@dataclass
class LinkConfig:
	"""Paramètres d'ouverture de la liaison."""

	url: str
	baudrate: int = 0  # 0 = automatique
	protocol: str = "0"
	timeout_s: float = 5.0
	retries: int = 2
	fast: bool = True


class ElmLink:
	"""Accès thread-safe à un adaptateur ELM327."""

	def __init__(self) -> None:
		self._elm: _Elm327 | None = None
		self._lock = threading.RLock()
		self.config: LinkConfig | None = None
		self.info = AdapterInfo()
		self._frame_counts: dict[bytes, int] = {}
		self.raw_listener: Callable[[str, list[str]], None] | None = None

	# ----------------------------------------------------------------- état
	@property
	def is_open(self) -> bool:
		return self._elm is not None and self._elm.status() != OBDStatus.NOT_CONNECTED

	@property
	def car_connected(self) -> bool:
		return self._elm is not None and self._elm.status() == OBDStatus.CAR_CONNECTED

	@property
	def protocol_code(self) -> str:
		if self._elm is None or not self.car_connected:
			return ""
		return str(self._elm.protocol_id())

	@property
	def protocol_name(self) -> str:
		return self.info.protocol_name

	# ------------------------------------------------------------ connexion
	def open(self, config: LinkConfig) -> AdapterInfo:
		"""Ouvre la liaison (avec tentatives) et lit les informations de l'adaptateur."""
		self.close()
		self.config = config
		self._precheck(config.url)
		baudrate: int | None = config.baudrate or None
		if config.url.startswith("socket://"):
			baudrate = _SOCKET_BAUDRATE
		attempts = max(1, config.retries + 1)
		last_error = ""
		for attempt in range(1, attempts + 1):
			log.info("Connexion à %s (tentative %d/%d)", config.url, attempt, attempts)
			with self._lock:
				elm = _Elm327(config.url, baudrate, config.protocol, config.timeout_s)
			if elm.status() != OBDStatus.NOT_CONNECTED:
				self._elm = elm
				break
			elm.close()
			last_error = "l'adaptateur ne répond pas aux commandes AT"
			time.sleep(0.5)
		else:
			raise ObdError(ErrorKind.PORT_ERROR, f"{config.url} : {last_error}.")
		port = self._elm.serial_port
		if port is not None:
			# Lectures courtes : le délai global de chaque commande est géré par `_Elm327.raw`.
			port.timeout = 0.05
		self._elm.response_timeout = config.timeout_s
		self._frame_counts.clear()
		self.info = self.read_adapter_info()
		return self.info

	def _precheck(self, url: str) -> None:
		if url.startswith("socket://"):
			host, _, port = url[len("socket://") :].rpartition(":")
			error = transport.probe_tcp(host, int(port or 0), timeout=3.0)
			if error:
				raise ObdError(ErrorKind.PORT_ERROR, error)
			return
		if url.startswith("/dev/"):
			access = transport.check_port_access(url)
			if not access.exists:
				raise ObdError(ErrorKind.PORT_ERROR, access.advice)
			if not access.ok:
				raise ObdError(ErrorKind.PERMISSION, access.advice)

	def close(self) -> None:
		"""Ferme la liaison (sans erreur si déjà fermée)."""
		with self._lock:
			if self._elm is not None:
				try:
					self._elm.close()
				except Exception as exc:
					log.debug("Erreur à la fermeture : %s", exc)
				self._elm = None

	def retry_vehicle(self) -> bool:
		"""Relance la détection du protocole ; True si le véhicule répond."""
		elm = self._require()
		with self._lock:
			ok = elm.retry_vehicle(self.config.protocol if self.config else "0")
			self._check_alive()
		self._frame_counts.clear()
		self._update_protocol_info(elm)
		return ok

	# ---------------------------------------------------------- informations
	def read_adapter_info(self) -> AdapterInfo:
		"""Lit version, tension, protocole et capacités de l'adaptateur."""
		elm = self._require()
		info = AdapterInfo()
		info.version = self._first(self._try("ATI"))
		info.description = self._first(self._try("AT@1"))
		info.voltage_raw = self._first(self._try("ATRV"))
		info.voltage = parse_voltage(info.voltage_raw)
		ign = self._first(self._try("ATIGN"))
		info.ignition = ign if ign in ("ON", "OFF") else ""
		for command, _minimum in PROBE_COMMANDS:
			answer = self._try(command)
			ok = bool(answer) and classify(answer) != ErrorKind.UNKNOWN_COMMAND
			info.probes[command] = ok
			if command == "STI" and ok:
				info.stn = self._first(answer)
		self.info = info
		self._update_protocol_info(elm)
		info.warnings = analyse_clone(info)
		for warning in info.warnings:
			log.warning("Adaptateur : %s", warning)
		log.info(
			"Adaptateur %s (%s), %s, protocole %s, véhicule %s",
			info.version,
			info.description,
			info.voltage_raw,
			info.protocol_name or "non déterminé",
			"en ligne" if info.ecu_responding else "muet",
		)
		return info

	def _update_protocol_info(self, elm: _Elm327) -> None:
		info = self.info
		info.ecu_responding = self.car_connected
		info.ecu_error = elm.protocol_error.value if elm.protocol_error and not info.ecu_responding else ""
		if info.ecu_responding:
			dpn = self._first(self._try("ATDPN"))
			code, auto = parse_dpn(dpn)
			info.protocol_code = code
			info.protocol_auto = auto
			info.protocol_name = describe_dpn(dpn)
		else:
			info.protocol_code = ""
			info.protocol_name = ""

	def read_voltage(self) -> float | None:
		"""Tension mesurée par l'adaptateur (ATRV)."""
		value = parse_voltage(self._first(self.send_raw("ATRV")))
		self.info.voltage = value
		return value

	# --------------------------------------------------------------- envoi
	def send_raw(self, command: str, check: bool = True, timeout: float | None = None) -> list[str]:
		"""Envoie une commande AT ou hexadécimale et retourne les lignes de réponse.

		Avec `check`, les réponses d'erreur (`NO DATA`, `CAN ERROR`…) lèvent une `ObdError`.
		"""
		elm = self._require()
		text = command.strip().upper()
		if not text.startswith(("AT", "ST")):
			text = text.replace(" ", "")
		data = text.encode("ascii", errors="ignore")
		with self._lock:
			lines = elm.raw(data, timeout=timeout)
			self._check_alive()
			if not lines:
				# Silence total (pas même l'invite) : l'adaptateur est-il encore là ?
				alive = bool(elm.raw(b"ATI", timeout=1.5))
				self._check_alive()
				if not alive:
					elm._lost("aucune réponse, même à ATI")
					self._check_alive()
		if self.raw_listener is not None:
			self.raw_listener(command, lines)
		if not lines:
			raise ObdError(ErrorKind.TIMEOUT, f"(commande {command})")
		if check:
			kind = classify(lines)
			if kind is not None:
				raise ObdError(kind, raw=lines)
		return clean_lines(lines)

	def query(self, command: OBDCommand) -> OBDResponse:
		"""Envoie une commande python-OBD et retourne la réponse décodée.

		Lève `ObdError` si le calculateur ne répond pas, répond négativement ou si la réponse est illisible.
		"""
		elm = self._require()
		if not self.car_connected:
			raise ObdError(ErrorKind.UNABLE_TO_CONNECT)
		request = bytes(command.command)
		to_send = request
		if self.config and self.config.fast and command.fast and request in self._frame_counts:
			to_send = request + format(self._frame_counts[request], "X").encode()
		lines = self.send_raw(to_send.decode())
		messages = [m for m in elm.parse(lines) if m.data]
		if not messages:
			raise ObdError(ErrorKind.INVALID_RESPONSE, f"(commande {request.decode()})", raw=lines)
		for message in messages:
			if message.data[0] == 0x7F:
				nrc = message.data[2] if len(message.data) > 2 else 0
				if nrc in (0x11, 0x12, 0x31):
					raise ObdError(ErrorKind.UNSUPPORTED, f"(code 0x{nrc:02X})", raw=lines)
				raise ObdError(ErrorKind.NEGATIVE_RESPONSE, f"(code 0x{nrc:02X})", raw=lines)
		if command.fast and request not in self._frame_counts:
			frames = sum(len(m.frames) for m in messages)
			if 0 < frames <= 15:
				self._frame_counts[request] = frames
		response = command(messages)
		return response

	def query_bytes(self, request: str) -> list[Any]:
		"""Envoie une requête hexadécimale et retourne les messages décodés (données brutes)."""
		elm = self._require()
		if not self.car_connected:
			raise ObdError(ErrorKind.UNABLE_TO_CONNECT)
		lines = self.send_raw(request)
		messages = [m for m in elm.parse(lines) if m.data]
		if not messages:
			raise ObdError(ErrorKind.INVALID_RESPONSE, f"(requête {request})", raw=lines)
		for message in messages:
			if message.data[0] == 0x7F:
				nrc = message.data[2] if len(message.data) > 2 else 0
				kind = ErrorKind.UNSUPPORTED if nrc in (0x11, 0x12, 0x31) else ErrorKind.NEGATIVE_RESPONSE
				raise ObdError(kind, f"(code 0x{nrc:02X})", raw=lines)
		return messages

	def monitor(self, command: str, on_line: Callable[[str], None], should_stop: Callable[[], bool]) -> None:
		"""Écoute continue (ATMA, ATCRA…) jusqu'à ce que `should_stop()` soit vrai."""
		elm = self._require()
		with self._lock:
			port = elm.serial_port
			if port is None:
				raise ObdError(ErrorKind.NOT_CONNECTED)
			previous = port.timeout
			port.timeout = 0.2
			try:
				port.reset_input_buffer()
				elm.write_only(command.strip().upper().encode() + b"\r")
				buffer = b""
				while not should_stop():
					chunk = port.read(port.in_waiting or 1)
					if not chunk:
						continue
					buffer += chunk
					*complete, buffer = buffer.replace(b"\n", b"\r").split(b"\r")
					for raw_line in complete:
						line = raw_line.decode("ascii", errors="replace").strip(" >")
						if line:
							on_line(line)
				# N'importe quel caractère arrête la surveillance ; on attend ensuite l'invite.
				elm.write_only(b"\r")
				deadline = time.monotonic() + 2.0
				tail = b""
				while time.monotonic() < deadline and b">" not in tail:
					tail += port.read(port.in_waiting or 1)
			except (serial.SerialException, OSError) as exc:
				self.close()
				raise ObdError(ErrorKind.ADAPTER_LOST, str(exc)) from exc
			finally:
				if elm.serial_port is not None:
					port.timeout = previous

	# ----------------------------------------------------------- utilitaires
	def _try(self, command: str) -> list[str]:
		"""Commande d'information : retourne [] si l'adaptateur ne répond pas (sauf liaison perdue)."""
		try:
			return self.send_raw(command, check=False, timeout=2.0)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			return []

	def _require(self) -> _Elm327:
		if self._elm is None or self._elm.status() == OBDStatus.NOT_CONNECTED:
			raise ObdError(ErrorKind.NOT_CONNECTED)
		return self._elm

	def _check_alive(self) -> None:
		if self._elm is not None and self._elm.status() == OBDStatus.NOT_CONNECTED:
			self._elm = None
			raise ObdError(ErrorKind.ADAPTER_LOST)

	@staticmethod
	def _first(lines: list[str]) -> str:
		return lines[0].strip() if lines else ""
