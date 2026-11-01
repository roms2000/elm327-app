"""Service de diagnostic : un thread unique possède l'adaptateur et exécute les tâches à la suite.

L'ELM327 ne traite qu'une requête à la fois. Les tâches ponctuelles (lecture des défauts, console…) sont
prioritaires sur l'interrogation cyclique, qui reprend entre deux tâches. Les événements sont transmis aux
abonnés depuis le thread de travail : l'interface doit les relayer vers son propre thread.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from obdtool.core import transport
from obdtool.core.adapter_info import AdapterInfo
from obdtool.core.elm_link import ElmLink, LinkConfig
from obdtool.core.emulator import DemoEmulator
from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.settings import ConnectionSettings

log = logging.getLogger(__name__)

RECONNECT_DELAY_S = 3.0
VEHICLE_RETRY_DELAY_S = 10.0
VOLTAGE_PERIOD_S = 5.0
VEHICLE_LOST_KINDS = (ErrorKind.UNABLE_TO_CONNECT, ErrorKind.CAN_ERROR, ErrorKind.BUS_INIT)


class LinkState(Enum):
	"""État de la liaison."""

	DISCONNECTED = "Déconnecté"
	CONNECTING = "Connexion…"
	ADAPTER_ONLY = "Adaptateur connecté, véhicule muet"
	CONNECTED = "Connecté au véhicule"
	RECONNECTING = "Reconnexion…"


@dataclass
class StateEvent:
	"""Changement d'état de la liaison."""

	state: LinkState
	message: str = ""
	info: AdapterInfo | None = None


@dataclass
class ErrorEvent:
	"""Erreur à signaler à l'utilisateur."""

	error: ObdError
	context: str = ""


@dataclass
class VoltageEvent:
	"""Nouvelle mesure de tension de l'adaptateur."""

	voltage: float | None


@dataclass
class RawEvent:
	"""Échange brut avec l'adaptateur (pour la console)."""

	command: str
	lines: list[str]


@dataclass
class PollEvent:
	"""Résultat d'un pas d'interrogation (contenu défini par le gestionnaire d'interrogation)."""

	payload: Any


Event = StateEvent | ErrorEvent | VoltageEvent | RawEvent | PollEvent


class PollHandler(Protocol):
	"""Gestionnaire d'interrogation cyclique, appelé dans le thread de travail."""

	def poll_step(self, link: ElmLink) -> Any | None:
		"""Effectue une requête ; retourne un objet à diffuser (PollEvent) ou None."""

	def reset(self) -> None:
		"""Réinitialise l'état après une (re)connexion."""


@dataclass(order=True)
class _Job:
	priority: int
	seq: int
	fn: Callable[[ElmLink], Any] = field(compare=False)
	future: Future = field(compare=False)
	name: str = field(compare=False, default="")
	needs_link: bool = field(compare=False, default=True)


class DiagnosticService:
	"""Propriétaire de la liaison ELM327 et de son thread de travail."""

	def __init__(self) -> None:
		self.link = ElmLink()
		self.link.raw_listener = lambda cmd, lines: self._emit(RawEvent(cmd, lines))
		self.state = LinkState.DISCONNECTED
		self.settings: ConnectionSettings | None = None
		self.emulator: DemoEmulator | None = None
		self.poll_handler: PollHandler | None = None
		self.polling = False
		self.auto_vehicle_retry = True
		self._listeners: list[Callable[[Event], None]] = []
		self._jobs: queue.PriorityQueue[_Job] = queue.PriorityQueue()
		self._seq = itertools.count()
		self._stop = threading.Event()
		self._user_disconnect = False
		self._next_reconnect = 0.0
		self._next_vehicle_retry = 0.0
		self._next_voltage = 0.0
		self._thread = threading.Thread(target=self._run, name="obd-worker", daemon=True)
		self._thread.start()

	# ----------------------------------------------------------- abonnements
	def subscribe(self, listener: Callable[[Event], None]) -> None:
		"""Ajoute un abonné aux événements (appelé depuis le thread de travail)."""
		self._listeners.append(listener)

	def unsubscribe(self, listener: Callable[[Event], None]) -> None:
		"""Retire un abonné (sans erreur s'il n'était pas abonné)."""
		if listener in self._listeners:
			self._listeners.remove(listener)

	def _emit(self, event: Event) -> None:
		for listener in list(self._listeners):
			try:
				listener(event)
			except Exception:
				log.exception("Erreur dans un abonné aux événements")

	def _set_state(self, state: LinkState, message: str = "") -> None:
		self.state = state
		info = self.link.info if state in (LinkState.ADAPTER_ONLY, LinkState.CONNECTED) else None
		log.info("État de la liaison : %s %s", state.value, message)
		self._emit(StateEvent(state, message, info))

	# ---------------------------------------------------------------- tâches
	def submit(
		self, fn: Callable[[ElmLink], Any], name: str = "", priority: int = 10, needs_link: bool = True
	) -> Future:
		"""Planifie une tâche exécutée dans le thread de travail avec la liaison en argument."""
		future: Future = Future()
		self._jobs.put(_Job(priority, next(self._seq), fn, future, name, needs_link))
		return future

	def connect(self, settings: ConnectionSettings) -> Future:
		"""Ouvre la connexion décrite par les réglages."""
		snapshot = ConnectionSettings(**vars(settings))
		return self.submit(lambda _link: self._do_connect(snapshot), "connexion", priority=0, needs_link=False)

	def disconnect(self) -> Future:
		"""Ferme la connexion et arrête les tentatives de reconnexion."""
		self._user_disconnect = True
		return self.submit(lambda _link: self._do_disconnect(), "déconnexion", priority=0, needs_link=False)

	def retry_vehicle(self) -> Future:
		"""Nouvelle tentative de dialogue avec le véhicule."""
		return self.submit(lambda _link: self._do_retry_vehicle(), "détection véhicule", priority=1)

	def set_polling(self, enabled: bool) -> None:
		"""Active ou suspend l'interrogation cyclique."""
		self.polling = enabled

	def shutdown(self, timeout: float = 5.0) -> None:
		"""Arrête le thread de travail et ferme la liaison."""
		self._user_disconnect = True
		self._stop.set()
		self._thread.join(timeout)
		self.link.close()
		if self.emulator is not None:
			self.emulator.stop()
			self.emulator = None

	# --------------------------------------------------------- implémentation
	def _url_for(self, settings: ConnectionSettings) -> str:
		if settings.kind == "wifi":
			error = transport.validate_wifi(settings.wifi_host, settings.wifi_port)
			if error:
				raise ObdError(ErrorKind.PORT_ERROR, error)
			return transport.wifi_url(settings.wifi_host, settings.wifi_port)
		if settings.kind == "demo":
			if self.emulator is None:
				self.emulator = DemoEmulator(settings.demo_scenario)
			try:
				return self.emulator.start()
			except RuntimeError as exc:
				raise ObdError(ErrorKind.PORT_ERROR, str(exc)) from exc
		if not settings.port.strip():
			raise ObdError(ErrorKind.PORT_ERROR, "Aucun port sélectionné.")
		return settings.port.strip()

	def _do_connect(self, settings: ConnectionSettings) -> AdapterInfo:
		self._user_disconnect = False
		self.settings = settings
		self._set_state(LinkState.CONNECTING, settings.kind)
		if settings.kind != "demo" and self.emulator is not None:
			self.emulator.stop()
			self.emulator = None
		try:
			url = self._url_for(settings)
			config = LinkConfig(
				url=url,
				baudrate=settings.baudrate,
				protocol=settings.protocol,
				timeout_s=settings.timeout_s,
				retries=settings.retries,
				fast=settings.fast,
			)
			info = self.link.open(config)
		except ObdError as exc:
			self._set_state(LinkState.DISCONNECTED, exc.user_message)
			raise
		if self.poll_handler is not None:
			self.poll_handler.reset()
		self._after_open(info)
		return info

	def _after_open(self, info: AdapterInfo) -> None:
		now = time.monotonic()
		self._next_voltage = now + VOLTAGE_PERIOD_S
		if self.link.car_connected:
			self._set_state(LinkState.CONNECTED, info.protocol_name)
		else:
			self._next_vehicle_retry = now + VEHICLE_RETRY_DELAY_S
			self._set_state(LinkState.ADAPTER_ONLY, info.ecu_error)

	def _do_disconnect(self) -> None:
		self.link.close()
		if self.emulator is not None:
			self.emulator.stop()
			self.emulator = None
		self._set_state(LinkState.DISCONNECTED, "Déconnexion demandée")

	def _do_retry_vehicle(self) -> bool:
		ok = self.link.retry_vehicle()
		if ok:
			if self.poll_handler is not None:
				self.poll_handler.reset()
			self._set_state(LinkState.CONNECTED, self.link.info.protocol_name)
		else:
			self._next_vehicle_retry = time.monotonic() + VEHICLE_RETRY_DELAY_S
			self._set_state(LinkState.ADAPTER_ONLY, self.link.info.ecu_error)
		return ok

	def _handle_error(self, error: ObdError, context: str) -> None:
		"""Traite une erreur : véhicule muet → attente du contact ; perte de liaison → reconnexion éventuelle."""
		if error.kind in VEHICLE_LOST_KINDS and self.state == LinkState.CONNECTED and context == "interrogation":
			self.link.mark_vehicle_lost()
			self._next_vehicle_retry = time.monotonic() + VEHICLE_RETRY_DELAY_S
			self._set_state(LinkState.ADAPTER_ONLY, error.user_message)
			self._emit(ErrorEvent(error, context))
			return
		if not error.is_link_lost:
			return
		self.link.close()
		auto = bool(self.settings and self.settings.auto_reconnect) and not self._user_disconnect
		if auto:
			self._next_reconnect = time.monotonic() + RECONNECT_DELAY_S
			self._set_state(LinkState.RECONNECTING, error.user_message)
		else:
			self._set_state(LinkState.DISCONNECTED, error.user_message)
		self._emit(ErrorEvent(error, context))

	def _run(self) -> None:
		while not self._stop.is_set():
			busy = self.polling and self.state == LinkState.CONNECTED and self.poll_handler is not None
			try:
				job = self._jobs.get_nowait() if busy else self._jobs.get(timeout=0.1)
			except queue.Empty:
				job = None
			if job is not None:
				self._run_job(job)
				continue
			try:
				self._background_step()
			except ObdError as exc:
				self._handle_error(exc, "interrogation")
			except Exception:
				log.exception("Erreur inattendue dans le thread de travail")
				time.sleep(0.2)

	def _run_job(self, job: _Job) -> None:
		if job.future.cancelled():
			return
		if job.needs_link and not self.link.is_open:
			job.future.set_exception(ObdError(ErrorKind.NOT_CONNECTED))
			return
		try:
			result = job.fn(self.link)
		except ObdError as exc:
			if job.name not in ("connexion",):
				self._handle_error(exc, job.name)
			job.future.set_exception(exc)
		except Exception as exc:
			log.exception("Erreur inattendue pendant la tâche « %s »", job.name)
			job.future.set_exception(ObdError(ErrorKind.INTERNAL, str(exc)))
		else:
			job.future.set_result(result)

	def _background_step(self) -> None:
		now = time.monotonic()
		if self.state == LinkState.RECONNECTING:
			if now >= self._next_reconnect and self.settings is not None:
				self._next_reconnect = now + RECONNECT_DELAY_S
				self._try_reconnect()
			return
		if self.state not in (LinkState.CONNECTED, LinkState.ADAPTER_ONLY):
			return
		if now >= self._next_voltage:
			self._next_voltage = now + VOLTAGE_PERIOD_S
			try:
				self._emit(VoltageEvent(self.link.read_voltage()))
			except ObdError as exc:
				if exc.is_link_lost:
					raise
		if self.state == LinkState.ADAPTER_ONLY:
			if (
				self.auto_vehicle_retry
				and self.settings
				and self.settings.auto_reconnect
				and now >= self._next_vehicle_retry
			):
				self._do_retry_vehicle()
			return
		if self.polling and self.poll_handler is not None:
			payload = self.poll_handler.poll_step(self.link)
			if payload is not None:
				self._emit(PollEvent(payload))

	def _try_reconnect(self) -> None:
		assert self.settings is not None
		try:
			url = self._url_for(self.settings)
			config = LinkConfig(
				url=url,
				baudrate=self.settings.baudrate,
				protocol=self.settings.protocol,
				timeout_s=self.settings.timeout_s,
				retries=0,
				fast=self.settings.fast,
			)
			info = self.link.open(config)
		except ObdError as exc:
			log.info("Reconnexion impossible pour l'instant : %s", exc.user_message)
			return
		if self.poll_handler is not None:
			self.poll_handler.reset()
		self._after_open(info)
