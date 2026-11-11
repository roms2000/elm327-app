"""Mémoire de défauts constructeur Renault du calculateur moteur (KWP2000 sur CAN, adresse physique 7E0/7E8).

Le mode 04 OBD n'efface que la mémoire normalisée (codes P0xxx, voyant MIL) ; les défauts propres à Renault,
qui pilotent par exemple le message « Injection à contrôler », se lisent avec 17/18 et s'effacent avec 14 FF 00.

Constat sur Clio III 1.6 16v à carte (11/11/2026) : 14 FF 00 n'est accepté qu'en session de diagnostic (7F 14 80
sinon) et reste refusé (7F 14 22) moteur tournant, pendant la coupure, puis tant que la session maintient le
calculateur éveillé après l'arrêt ; les services OBD (04) sont eux refusés en adressage physique (7F 04 11), les deux
effacements ne peuvent donc pas partager la même session. L'effacement constructeur exige « contact mis, moteur
arrêté », calculateur alimenté : obtenu par le contact forcé (appui long sur START sans pédale), où 14 FF 00 a
répondu 54 FF 00 ; un simple appui sur START laisse le calculateur moteur éteint.
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
# Arrêt du moteur vu par l'adaptateur : la tension de bord retombe quand l'alternateur ne charge plus.
CHARGING_MIN_V = 13.2
STOP_DROP_V = 0.7
VOLTAGE_PERIOD_S = 1.0
NEEDS_IGNITION = (
	"Le calculateur n'accepte l'effacement des défauts constructeur que contact mis, moteur arrêté "
	"(réponse 7F 14 22 « conditions non remplies » moteur tournant comme après la coupure). "
	"Sur un véhicule à carte, mettez le contact forcé : carte insérée, appui long (5 s environ) sur START sans "
	"toucher les pédales, jusqu'à l'allumage des voyants moteur et pression d'huile ; attendez quelques secondes "
	"que le calculateur réponde, puis utilisez « Effacer les défauts constructeur… »."
)

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


def _voltage(link: ElmLink) -> float | None:
	try:
		return link.read_voltage()
	except ObdError as exc:
		if exc.is_link_lost:
			raise
		return None


def clear_faults(
	link: ElmLink,
	during_engine_stop: bool = False,
	should_stop: Callable[[], bool] | None = None,
	on_attempt: Callable[[int, str], None] | None = None,
	window_s: float = 60.0,
	silent_limit: int = 3,
	stop_grace_s: float = 5.0,
) -> int:
	"""Efface la mémoire de défauts constructeur (14 FF 00) ; retourne le nombre de demandes envoyées.

	Avec `during_engine_stop`, la demande est répétée pendant que l'utilisateur coupe le moteur (calculateur qui
	refuse l'effacement moteur tournant, réponse 7F 14 22, et s'éteint aussitôt après l'arrêt). L'arrêt du moteur
	est détecté par la chute de la tension de bord : si le calculateur refuse encore `stop_grace_s` secondes après,
	la boucle s'arrête (la session de diagnostic le maintiendrait éveillé indéfiniment, sans qu'il accepte).
	"""
	with RenaultSession(link, ENGINE_TX, ENGINE_RX) as session:
		deadline = time.monotonic() + (window_s if during_engine_stop else 5.0)
		attempts = 0
		answered = False
		refusal = ""
		silent = 0
		charging_v: float | None = None
		stopped_at: float | None = None
		next_voltage = 0.0
		while True:
			if should_stop is not None and should_stop():
				raise ObdError(ErrorKind.CANCELLED)
			if during_engine_stop and stopped_at is None and time.monotonic() >= next_voltage:
				next_voltage = time.monotonic() + VOLTAGE_PERIOD_S
				voltage = _voltage(link)
				if voltage is not None and voltage >= CHARGING_MIN_V:
					charging_v = max(charging_v or 0.0, voltage)
				elif voltage is not None and charging_v is not None and voltage <= charging_v - STOP_DROP_V:
					stopped_at = time.monotonic()
					log.info("Arrêt du moteur détecté (%.1f V → %.1f V), demande n° %d", charging_v, voltage, attempts)
			attempts += 1
			try:
				response = session.clear_fault_memory()
			except ObdError as exc:
				if exc.is_link_lost:
					raise
				log.info("Effacement constructeur, demande n° %d : %s", attempts, exc.user_message)
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
					state = "refusé (conditions non remplies)" if code == 0x22 else f"refusé (code 0x{code:02X})"
					refusal = state
					if attempts == 1 or not during_engine_stop:
						log.info("Effacement constructeur refusé : %s", response.hex().upper())
					if not during_engine_stop:
						detail = NEEDS_IGNITION if code == 0x22 else f"Réponse {response.hex().upper()}."
						raise ObdError(
							ErrorKind.NEGATIVE_RESPONSE,
							f"Le calculateur refuse l'effacement des défauts constructeur ({state}). {detail}",
						)
					if stopped_at is not None and time.monotonic() - stopped_at >= stop_grace_s:
						log.warning(
							"Effacement constructeur toujours refusé %.0f s après l'arrêt du moteur", stop_grace_s
						)
						raise ObdError(
							ErrorKind.NEGATIVE_RESPONSE,
							f"Moteur coupé, le calculateur refuse toujours l'effacement ({state}). {NEEDS_IGNITION}",
						)
			else:
				silent += 1
				state = "pas de réponse"
				if not during_engine_stop:
					raise ObdError(
						ErrorKind.NO_DATA,
						"(effacement des défauts constructeur : calculateur moteur muet ; sur un véhicule à carte, "
						"contact forcé par un appui long sur START sans pédale, puis patienter quelques secondes)",
					)
				if answered and silent >= silent_limit:
					raise ObdError(
						ErrorKind.UNABLE_TO_CONNECT,
						"Le calculateur s'est éteint avant d'accepter l'effacement. Réessayez en coupant le moteur "
						"franchement (un appui sur START/STOP).",
					)
			if on_attempt is not None:
				on_attempt(attempts, state)
			if time.monotonic() >= deadline:
				if answered and during_engine_stop:
					raise ObdError(
						ErrorKind.TIMEOUT,
						f"Le calculateur a refusé l'effacement pendant tout le délai ({refusal or state}). {NEEDS_IGNITION}",
					)
				raise ObdError(ErrorKind.TIMEOUT, "Le moteur n'a pas été coupé dans le délai imparti.")
			time.sleep(0.05)
