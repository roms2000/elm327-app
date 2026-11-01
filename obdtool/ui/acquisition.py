"""Acquisition des valeurs : PID supportés, sélection, diffusion des échantillons aux écrans."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from obdtool.core import pids
from obdtool.core.errors import ObdError
from obdtool.core.poller import LivePoller, read_supported_pids
from obdtool.core.samples import SampleBatch, SessionStats
from obdtool.core.service import LinkState, StateEvent
from obdtool.ui.context import AppContext

log = logging.getLogger(__name__)


class AcquisitionController(QObject):
	"""Point central des mesures : les écrans déclarent les valeurs dont ils ont besoin et reçoivent les lots.

	La source des lots est soit l'adaptateur (`live`), soit la relecture d'une session (`replay`).
	"""

	batch = Signal(object)  # SampleBatch
	supported_changed = Signal()
	running_changed = Signal(bool)
	rates_changed = Signal(float, float)  # requêtes/s, cycles/s
	selection_changed = Signal()

	def __init__(self, ctx: AppContext, parent: QObject | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.poller = LivePoller()
		self.poller.set_interval(ctx.settings.polling.interval_ms)
		ctx.service.poll_handler = self.poller
		self.stats = SessionStats()
		self.supported: set[int] | None = None  # None = inconnu (pas de connexion)
		self.running = True
		self.replaying = False
		self._requirements: dict[str, list[str]] = {}
		selection = ctx.settings.polling.selected_pids or list(pids.DEFAULT_SELECTION)
		self._requirements["live"] = [k for k in selection if pids.get(k)]
		ctx.bridge.state_changed.connect(self._on_state)
		ctx.bridge.poll.connect(self._on_poll)

	# --------------------------------------------------------- sélection
	def live_selection(self) -> list[str]:
		"""Valeurs choisies sur l'écran Valeurs."""
		return list(self._requirements.get("live", []))

	def set_live_selection(self, keys: list[str]) -> None:
		"""Modifie la sélection de l'écran Valeurs et l'enregistre."""
		self.require("live", keys)
		self.ctx.settings.polling.selected_pids = list(keys)
		self.ctx.save_settings()

	def require(self, owner: str, keys: list[str]) -> None:
		"""Déclare les valeurs nécessaires à un écran (remplace sa déclaration précédente)."""
		self._requirements[owner] = list(dict.fromkeys(keys))
		self._apply_selection()

	def effective_selection(self) -> list[str]:
		"""Union des valeurs demandées, limitée aux PID supportés si on les connaît."""
		keys: list[str] = []
		for owner_keys in self._requirements.values():
			for key in owner_keys:
				if key not in keys and self.is_available(key):
					keys.append(key)
		return keys

	def is_available(self, key: str) -> bool:
		"""Vrai si la valeur peut être interrogée sur ce véhicule (ou si on ne sait pas encore)."""
		definition = pids.get(key)
		if definition is None:
			return False
		if definition.source != "obd" or self.supported is None:
			return True
		return definition.pid in self.supported

	def _apply_selection(self) -> None:
		self.poller.set_selection(self.effective_selection())
		self.selection_changed.emit()

	# ----------------------------------------------------------- contrôle
	def set_running(self, running: bool) -> None:
		"""Démarre ou suspend l'interrogation cyclique."""
		self.running = running
		self.ctx.service.set_polling(running and not self.replaying and self.ctx.service.state == LinkState.CONNECTED)
		self.running_changed.emit(running)

	def set_interval(self, milliseconds: int) -> None:
		self.poller.set_interval(milliseconds)
		self.ctx.settings.polling.interval_ms = milliseconds
		self.ctx.save_settings()

	def reset_stats(self) -> None:
		self.stats.reset()

	def publish(self, batch: SampleBatch) -> None:
		"""Diffuse un lot (utilisé par la relecture)."""
		self.stats.add_batch(batch)
		self.batch.emit(batch)

	def set_replaying(self, replaying: bool) -> None:
		"""Pendant une relecture, l'interrogation de l'adaptateur est suspendue."""
		self.replaying = replaying
		self.set_running(self.running)

	# --------------------------------------------------------- événements
	def _on_state(self, event: StateEvent) -> None:
		if event.state == LinkState.CONNECTED:
			self.ctx.bridge.run(
				self.ctx.service.submit(read_supported_pids, "PID supportés", priority=2),
				self._on_supported,
				self._on_supported_error,
			)
		elif event.state == LinkState.DISCONNECTED:
			self.supported = None
			self.ctx.service.set_polling(False)
			self.supported_changed.emit()
			self._apply_selection()
		else:
			self.ctx.service.set_polling(False)

	def _on_supported(self, supported: set[int]) -> None:
		self.supported = supported
		self.supported_changed.emit()
		self._apply_selection()
		self.set_running(self.running)

	def _on_supported_error(self, error: ObdError) -> None:
		log.warning("Lecture des PID supportés impossible : %s", error.user_message)
		self.ctx.notify(f"Lecture des PID supportés impossible : {error.user_message}")
		self.supported = None
		self._apply_selection()
		self.set_running(self.running)

	def _on_poll(self, payload: object) -> None:
		if not isinstance(payload, SampleBatch) or self.replaying:
			return
		self.stats.add_batch(payload)
		self.rates_changed.emit(payload.query_rate, payload.cycle_rate)
		self.batch.emit(payload)
