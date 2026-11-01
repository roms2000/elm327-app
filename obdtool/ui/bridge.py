"""Relais Qt des événements du service de diagnostic (thread de travail → thread de l'interface).

Le thread de travail n'émet jamais de signal Qt : il dépose les événements dans une file, vidée par un
minuteur du thread de l'interface. (Émettre un signal PySide6 depuis un thread Python dérègle le compteur de
références de True sous Python 3.11 et finit par faire planter l'interpréteur.)
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.service import (
	DiagnosticService,
	ErrorEvent,
	Event,
	PollEvent,
	RawEvent,
	StateEvent,
	VoltageEvent,
)
from obdtool.ui.qtcompat import emit

DRAIN_INTERVAL_MS = 20


class ServiceBridge(QObject):
	"""Transforme les événements du service en signaux Qt émis dans le thread de l'interface."""

	state_changed = Signal(object)  # StateEvent
	error = Signal(object)  # ErrorEvent
	voltage = Signal(object)  # float | None
	raw = Signal(object)  # RawEvent
	poll = Signal(object)  # contenu de PollEvent

	def __init__(self, service: DiagnosticService, parent: QObject | None = None) -> None:
		super().__init__(parent)
		self.service = service
		self._alive = True
		self._queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
		self._timer = QTimer(self)
		self._timer.setInterval(DRAIN_INTERVAL_MS)
		self._timer.timeout.connect(self.drain)
		self._timer.start()
		service.subscribe(self._on_event)
		self.destroyed.connect(lambda *_: self.detach())

	def detach(self) -> None:
		"""Cesse de relayer les événements (avant destruction de l'interface)."""
		self._alive = False
		self.service.unsubscribe(self._on_event)

	def _on_event(self, event: Event) -> None:
		# Appelé dans le thread de travail : aucun appel Qt ici.
		if self._alive:
			self._queue.put(("event", event))

	def run(
		self,
		future: Future,
		on_done: Callable[[Any], None] | None = None,
		on_error: Callable[[ObdError], None] | None = None,
	) -> Future:
		"""Appelle `on_done(résultat)` ou `on_error(erreur)` dans le thread de l'interface à la fin de la tâche."""
		future.add_done_callback(lambda f: self._alive and self._queue.put(("done", (f, on_done, on_error))))
		return future

	def drain(self) -> None:
		"""Relaie les événements en attente (thread de l'interface)."""
		while True:
			try:
				kind, payload = self._queue.get_nowait()
			except queue.Empty:
				return
			if kind == "done":
				self._dispatch(*payload)
				continue
			event = payload
			if isinstance(event, StateEvent):
				emit(self.state_changed, event)
			elif isinstance(event, ErrorEvent):
				emit(self.error, event)
			elif isinstance(event, VoltageEvent):
				emit(self.voltage, event.voltage)
			elif isinstance(event, RawEvent):
				emit(self.raw, event)
			elif isinstance(event, PollEvent):
				emit(self.poll, event.payload)

	def _dispatch(self, future: Future, on_done: Callable[[Any], None] | None, on_error: Callable | None) -> None:
		if future.cancelled():
			return
		exc = future.exception()
		if exc is None:
			if on_done is not None:
				on_done(future.result())
			return
		error = exc if isinstance(exc, ObdError) else ObdError(ErrorKind.INTERNAL, str(exc))
		if on_error is not None:
			on_error(error)
