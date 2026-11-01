"""Relais Qt des événements du service de diagnostic (thread de travail → thread de l'interface)."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot

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


class ServiceBridge(QObject):
	"""Transforme les événements du service en signaux Qt, reçus dans le thread de l'interface."""

	state_changed = Signal(object)  # StateEvent
	error = Signal(object)  # ErrorEvent
	voltage = Signal(object)  # float | None
	raw = Signal(object)  # RawEvent
	poll = Signal(object)  # contenu de PollEvent
	_finished = Signal(object, object, object)  # future, on_done, on_error

	def __init__(self, service: DiagnosticService, parent: QObject | None = None) -> None:
		super().__init__(parent)
		self.service = service
		self._finished.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)
		service.subscribe(self._on_event)

	def _on_event(self, event: Event) -> None:
		# Appelé dans le thread de travail : les signaux sont mis en file vers le thread de l'interface.
		if isinstance(event, StateEvent):
			self.state_changed.emit(event)
		elif isinstance(event, ErrorEvent):
			self.error.emit(event)
		elif isinstance(event, VoltageEvent):
			self.voltage.emit(event.voltage)
		elif isinstance(event, RawEvent):
			self.raw.emit(event)
		elif isinstance(event, PollEvent):
			self.poll.emit(event.payload)

	def run(
		self,
		future: Future,
		on_done: Callable[[Any], None] | None = None,
		on_error: Callable[[ObdError], None] | None = None,
	) -> Future:
		"""Appelle `on_done(résultat)` ou `on_error(erreur)` dans le thread de l'interface à la fin de la tâche."""
		future.add_done_callback(lambda f: self._finished.emit(f, on_done, on_error))
		return future

	@Slot(object, object, object)
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
