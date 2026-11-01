"""Interrogation cyclique des valeurs en direct (Mode 01)."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from obd.protocols import ECU

from obdtool.core import pids
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.pids import PidDef
from obdtool.core.samples import Sample, SampleBatch

log = logging.getLogger(__name__)

SLOW_EVERY = 5  # les valeurs lentes ne sont interrogées qu'un cycle sur cinq
FLUSH_PERIOD_S = 0.1  # diffusion au moins toutes les 100 ms
MAX_FAILURES = 3  # échecs consécutifs avant mise en pause d'un PID
SKIP_DURATION_S = 15.0
_VEHICLE_LOST = (ErrorKind.UNABLE_TO_CONNECT, ErrorKind.CAN_ERROR, ErrorKind.BUS_INIT)

# Traductions des textes renvoyés par python-OBD.
_TEXT_FR = {
	"Open loop due to insufficient engine temperature": "Boucle ouverte (moteur froid)",
	"Closed loop, using oxygen sensor feedback to determine fuel mix": "Boucle fermée (régulation par sonde lambda)",
	"Open loop due to engine load OR fuel cut due to deceleration": "Boucle ouverte (pleine charge ou coupure en décélération)",
	"Open loop due to system failure": "Boucle ouverte (défaut du système)",
	"Closed loop, using at least one oxygen sensor but there is a fault in the feedback system": (
		"Boucle fermée avec défaut de la régulation"
	),
	"Gasoline": "Essence",
	"Diesel": "Gazole",
	"Electric": "Électrique",
	"Upstream": "En amont du catalyseur",
	"Downstream of catalytic converter": "En aval du catalyseur",
	"From the outside atmosphere or off": "Air extérieur ou arrêt",
	"Pump commanded on for diagnostics": "Pompe commandée pour diagnostic",
}


def translate(text: str) -> str:
	"""Traduit un texte de python-OBD quand une traduction est connue."""
	return _TEXT_FR.get(text, text)


@dataclass
class _Group:
	"""Requête unique et valeurs qu'on en extrait."""

	request: str
	pid: int
	source: str
	defs: list[PidDef]
	slow: bool
	failures: int = 0
	skip_until: float = 0.0


def decode_messages(definition: PidDef, messages: list) -> float | str | None:
	"""Décode la valeur `definition` à partir des messages de réponse."""
	if definition.decoder is not None:
		message = next((m for m in messages if m.ecu == ECU.ENGINE), messages[0])
		data = bytes(message.data[2:])
		return definition.decoder(data)
	command = definition.command
	if command is None:
		return None
	response = command(messages)
	value = response.value
	if value is None:
		return None
	if hasattr(value, "magnitude"):
		magnitude = float(value.magnitude)
		return round(magnitude, 4)
	if isinstance(value, tuple | list):
		parts = [translate(str(v)) for v in value if v]
		return " / ".join(parts) if parts else None
	return translate(str(value))


class LivePoller:
	"""Gestionnaire d'interrogation : un PID par pas, diffusion par lots."""

	def __init__(self) -> None:
		self._lock = threading.Lock()
		self._groups: list[_Group] = []
		self._index = 0
		self._cycle = 0
		self.interval_s = 0.0
		self._pending: list[Sample] = []
		self._last_flush = 0.0
		self._cycle_start = time.monotonic()
		self._queries = 0
		self._rate_t0 = time.monotonic()
		self.query_rate = 0.0
		self.cycle_rate = 0.0
		self._consecutive_vehicle_errors = 0
		self._wait_until = 0.0

	@property
	def selection(self) -> list[str]:
		with self._lock:
			return [d.key for g in self._groups for d in g.defs]

	def set_selection(self, keys: list[str] | set[str]) -> None:
		"""Définit les valeurs à interroger (ordre conservé, clés inconnues ignorées)."""
		groups: dict[str, _Group] = {}
		for key in keys:
			definition = pids.get(key)
			if definition is None or definition.source == "derived":
				continue
			group_key = definition.request or definition.source
			group = groups.get(group_key)
			if group is None:
				group = _Group(group_key, definition.pid, definition.source, [], definition.slow)
				groups[group_key] = group
			if definition not in group.defs:
				group.defs.append(definition)
				group.slow = group.slow and definition.slow
		if all(g.slow for g in groups.values()):
			for group in groups.values():
				group.slow = False
		with self._lock:
			self._groups = list(groups.values())
			self._index = 0

	def set_interval(self, milliseconds: int) -> None:
		"""Durée minimale d'un cycle complet (0 = aussi vite que possible)."""
		self.interval_s = max(0, milliseconds) / 1000.0

	def reset(self) -> None:
		"""Réinitialise compteurs et pauses (après une connexion)."""
		with self._lock:
			for group in self._groups:
				group.failures = 0
				group.skip_until = 0.0
			self._index = 0
			self._cycle = 0
			self._pending.clear()
			self._consecutive_vehicle_errors = 0
			self._wait_until = 0.0
		self.query_rate = 0.0
		self.cycle_rate = 0.0
		self._rate_t0 = time.monotonic()
		self._queries = 0
		self._cycle_start = time.monotonic()

	def _next_group(self) -> _Group | None:
		with self._lock:
			if not self._groups:
				return None
			now = time.monotonic()
			for _ in range(len(self._groups) + 1):
				if self._index >= len(self._groups):
					elapsed = now - self._cycle_start
					if self.interval_s and elapsed < self.interval_s:
						# Cycle plus court que l'intervalle demandé : attente sans bloquer les autres tâches.
						self._wait_until = self._cycle_start + self.interval_s
						return None
					if elapsed > 0:
						instant = 1.0 / elapsed
						self.cycle_rate = instant if self.cycle_rate == 0 else 0.7 * self.cycle_rate + 0.3 * instant
					self._cycle_start = now
					self._cycle += 1
					self._index = 0
				group = self._groups[self._index]
				self._index += 1
				if group.skip_until > now or (group.slow and self._cycle % SLOW_EVERY != 0):
					continue
				return group
			return None

	def poll_step(self, link: ElmLink) -> SampleBatch | None:
		"""Interroge le PID suivant ; retourne un lot à diffuser quand il est temps."""
		remaining = self._wait_until - time.monotonic()
		if remaining > 0:
			time.sleep(min(remaining, 0.02))
			return self._maybe_flush()
		group = self._next_group()
		if group is None:
			if not self._groups:
				time.sleep(0.02)
			return self._maybe_flush()
		self._query_group(link, group)
		return self._maybe_flush()

	def _query_group(self, link: ElmLink, group: _Group) -> None:
		now = time.time()
		self._queries += 1
		if group.source == "atrv":
			try:
				value = link.read_voltage()
			except ObdError as exc:
				if exc.is_link_lost:
					raise
				value = None
			self._pending.append(Sample(group.defs[0].key, now, value))
			return
		try:
			messages = link.request(group.request, fast=True)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			self._on_failure(group, exc, now)
			return
		group.failures = 0
		self._consecutive_vehicle_errors = 0
		for definition in group.defs:
			try:
				value = decode_messages(definition, messages)
				self._pending.append(Sample(definition.key, now, value))
			except Exception as exc:  # décodeur python-OBD sur une réponse inattendue
				log.debug("Décodage impossible de %s : %s", definition.key, exc)
				self._pending.append(Sample(definition.key, now, None, "réponse illisible"))

	def _on_failure(self, group: _Group, error: ObdError, now: float) -> None:
		group.failures += 1
		label = {
			ErrorKind.NO_DATA: "pas de réponse",
			ErrorKind.UNSUPPORTED: "non supporté",
			ErrorKind.TIMEOUT: "délai dépassé",
		}.get(error.kind, error.kind.name.lower())
		if group.failures >= MAX_FAILURES:
			group.skip_until = time.monotonic() + SKIP_DURATION_S
			label += " (en pause 15 s)"
			log.info("PID %s mis en pause après %d échecs : %s", group.request, group.failures, error.user_message)
		for definition in group.defs:
			self._pending.append(Sample(definition.key, now, None, label))
		if error.kind in _VEHICLE_LOST or error.kind == ErrorKind.NO_DATA:
			self._consecutive_vehicle_errors += 1
			threshold = max(6, 2 * len(self._groups))
			if error.kind in _VEHICLE_LOST and self._consecutive_vehicle_errors >= 3:
				raise ObdError(error.kind, "Le véhicule ne répond plus (contact coupé ?).")
			if self._consecutive_vehicle_errors >= threshold:
				self._consecutive_vehicle_errors = 0
				raise ObdError(ErrorKind.UNABLE_TO_CONNECT, "Le véhicule ne répond plus à aucune requête.")

	def _maybe_flush(self) -> SampleBatch | None:
		now = time.monotonic()
		if not self._pending or now - self._last_flush < FLUSH_PERIOD_S:
			return None
		elapsed = now - self._rate_t0
		if elapsed >= 1.0:
			self.query_rate = self._queries / elapsed
			self._queries = 0
			self._rate_t0 = now
		batch = SampleBatch(self._pending, self.query_rate, self.cycle_rate)
		self._pending = []
		self._last_flush = now
		return batch


def read_supported_pids(link: ElmLink) -> set[int]:
	"""Lit les masques de PID supportés (0100, 0120, 0140…) de tous les calculateurs qui répondent."""
	supported: set[int] = set()
	block = 0x00
	while block <= 0xE0:
		try:
			messages = link.request(f"01{block:02X}")
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			break
		block_pids: set[int] = set()
		for message in messages:
			data = bytes(message.data)
			if len(data) >= 6 and data[1] == block:
				block_pids |= pids.decode_supported_bitmap(block, data[2:6])
		supported |= block_pids
		if (block + 0x20) not in block_pids:
			break
		block += 0x20
	log.info("PID Mode 01 supportés : %s", " ".join(f"{p:02X}" for p in sorted(supported)))
	return supported
