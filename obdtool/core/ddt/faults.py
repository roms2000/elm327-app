"""Mémoire de défauts constructeur Renault du calculateur moteur (KWP2000 sur CAN, adresse physique 7E0/7E8).

Le mode 04 OBD n'efface que la mémoire normalisée (codes P0xxx, voyant MIL) ; les défauts propres à Renault,
qui pilotent par exemple le message « Injection à contrôler », se lisent avec 17/18 et s'effacent avec 14 FF 00.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from obdtool.core.ddt.session import RenaultSession
from obdtool.core.dtc import describe, parse_dtc_bytes
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ErrorKind, ObdError

log = logging.getLogger(__name__)

ENGINE_TX = "7E0"
ENGINE_RX = "7E8"
READ_REQUESTS = ("17FF00", "1802FF00")

# État de stockage (bits 6-5 de l'octet d'état, ISO 14230-3).
_STORAGE = {
	0b00: "aucun défaut détecté",
	0b01: "mémorisé, absent actuellement",
	0b10: "intermittent / en cours de validation",
	0b11: "présent",
}


@dataclass(frozen=True)
class RenaultFault:
	"""Défaut de la mémoire constructeur."""

	raw: str  # deux octets du code, en hexadécimal
	status: int

	@property
	def code(self) -> str:
		codes = parse_dtc_bytes(bytes.fromhex(self.raw))
		return codes[0] if codes else self.raw

	@property
	def present(self) -> bool:
		return (self.status >> 5) & 0b11 == 0b11

	@property
	def state(self) -> str:
		text = _STORAGE[(self.status >> 5) & 0b11]
		if self.status & 0x80:
			text += ", demande d'allumage du voyant"
		return text

	@property
	def description(self) -> str:
		return describe(self.code).display


def parse_fault_list(response: bytes) -> list[RenaultFault]:
	"""Décode une réponse 57 / 58 : nombre de défauts puis triplets (code sur 2 octets, état)."""
	if len(response) < 2 or response[0] not in (0x57, 0x58):
		raise ObdError(ErrorKind.INVALID_RESPONSE, f"(réponse {response.hex().upper()})")
	count = response[1]
	records = response[2:]
	faults = []
	for index in range(min(count, len(records) // 3)):
		chunk = records[3 * index : 3 * index + 3]
		if chunk[:2] == b"\x00\x00":
			continue
		faults.append(RenaultFault(chunk[:2].hex().upper(), chunk[2]))
	return faults


def read_faults(link: ElmLink) -> tuple[list[RenaultFault], str]:
	"""Lit la mémoire de défauts constructeur ; retourne (défauts, requête qui a répondu)."""
	with RenaultSession(link, ENGINE_TX, ENGINE_RX) as session:
		last_error: ObdError | None = None
		for request in READ_REQUESTS:
			try:
				response = session.request(request)
			except ObdError as exc:
				if exc.is_link_lost:
					raise
				last_error = exc
				continue
			faults = parse_fault_list(response)
			log.info("Défauts Renault (%s) : %s", request, [(f.raw, f"{f.status:02X}") for f in faults])
			return faults, request
	raise last_error or ObdError(ErrorKind.UNSUPPORTED, "(lecture des défauts constructeur)")


def clear_faults(
	link: ElmLink,
	during_engine_stop: bool = False,
	should_stop: Callable[[], bool] | None = None,
	on_attempt: Callable[[int, str], None] | None = None,
	window_s: float = 60.0,
	silent_limit: int = 3,
) -> int:
	"""Efface la mémoire de défauts constructeur (14 FF 00) ; retourne le nombre de demandes envoyées.

	Avec `during_engine_stop`, la demande est répétée pendant que l'utilisateur coupe le moteur (calculateur qui
	refuse l'effacement moteur tournant, réponse 7F 14 22, et s'éteint aussitôt après l'arrêt).
	"""
	with RenaultSession(link, ENGINE_TX, ENGINE_RX) as session:
		deadline = time.monotonic() + (window_s if during_engine_stop else 5.0)
		attempts = 0
		answered = False
		silent = 0
		while True:
			if should_stop is not None and should_stop():
				raise ObdError(ErrorKind.CANCELLED)
			attempts += 1
			try:
				response = session.clear_fault_memory()
			except ObdError as exc:
				if exc.is_link_lost:
					raise
				response = b""
			if response[:1] == b"\x54":
				log.info("Mémoire de défauts Renault effacée (demande n° %d)", attempts)
				return attempts
			if response[:2] == b"\x7f\x14":
				code = response[2] if len(response) > 2 else 0
				answered = True
				silent = 0
				if code in (0x7E, 0x7F):
					session.request(session.session, allow_negative=True)  # session perdue : la rouvrir
					state = "session rouverte"
				else:
					state = "refusé (moteur tournant)" if code == 0x22 else f"refusé (code 0x{code:02X})"
					if not during_engine_stop:
						raise ObdError(
							ErrorKind.NEGATIVE_RESPONSE,
							f"Le calculateur refuse l'effacement des défauts constructeur ({state}). "
							"Utilisez l'effacement en coupant le moteur.",
						)
			else:
				silent += 1
				state = "pas de réponse"
				if not during_engine_stop:
					raise ObdError(ErrorKind.NO_DATA, "(effacement des défauts constructeur)")
				if answered and silent >= silent_limit:
					raise ObdError(
						ErrorKind.UNABLE_TO_CONNECT,
						"Le calculateur s'est éteint avant d'accepter l'effacement. Réessayez en coupant le moteur "
						"franchement (un appui sur START/STOP).",
					)
			if on_attempt is not None:
				on_attempt(attempts, state)
			if time.monotonic() >= deadline:
				raise ObdError(ErrorKind.TIMEOUT, "Le moteur n'a pas été coupé dans le délai imparti.")
			time.sleep(0.05)
