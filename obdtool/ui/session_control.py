"""Pilotage de l'enregistrement et de la relecture des sessions."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from obdtool.core.samples import SampleBatch
from obdtool.core.sessions import SessionRecorder, SessionReplay
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext

log = logging.getLogger(__name__)


class SessionController(QObject):
	"""Enregistre les lots reçus et rejoue une session « comme si on était connecté »."""

	recording_changed = Signal(bool, str)  # actif, nom
	replay_changed = Signal(str)  # stopped | playing | paused | finished
	replay_position = Signal(float, float, float)  # position, début, fin

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QObject | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.recorder = SessionRecorder()
		self.replay: SessionReplay | None = None
		self.replay_state = "stopped"
		self._last_tick = 0.0
		self._flush = QTimer(self)
		self._flush.setInterval(1000)
		self._flush.timeout.connect(self.recorder.flush)
		self._timer = QTimer(self)
		self._timer.setInterval(50)
		self._timer.timeout.connect(self._tick)
		acquisition.batch.connect(self._on_batch)

	# --------------------------------------------------------- enregistrement
	@property
	def recording(self) -> bool:
		return self.recorder.active

	def start_recording(self, name: str | None = None) -> Path:
		"""Démarre l'enregistrement des valeurs interrogées."""
		info = self.ctx.service.link.info
		metadata = {
			"vehicle": self.ctx.settings.extra.get("vehicle_name", ""),
			"protocol": info.protocol_name,
			"adapter": info.version,
			"connection": self.ctx.service.settings.kind if self.ctx.service.settings else "",
			"keys": self.acq.effective_selection(),
		}
		path = self.recorder.start(metadata, name)
		self._flush.start()
		self.recording_changed.emit(True, path.stem)
		return path

	def stop_recording(self) -> Path | None:
		path = self.recorder.stop()
		self._flush.stop()
		self.recording_changed.emit(False, path.stem if path else "")
		if path:
			self.ctx.notify(f"Session enregistrée : {path.name} ({self.recorder.samples} mesures)")
		return path

	def _on_batch(self, batch: SampleBatch) -> None:
		if batch.source == "live" and self.recorder.active:
			self.recorder.write(batch)

	# -------------------------------------------------------------- relecture
	def load(self, path: Path) -> SessionReplay:
		"""Charge une session et démarre sa relecture."""
		self.stop_replay()
		replay = SessionReplay(path)
		self.replay = replay
		self.acq.set_replaying(True)
		self.acq.clear_data()
		self.play()
		log.info("Relecture de %s (%.0f s, %d mesures)", path.name, replay.duration, len(replay.samples))
		return replay

	def play(self) -> None:
		if self.replay is None:
			return
		if self.replay.finished:
			self.seek(self.replay.start)
		self._last_tick = time.monotonic()
		self._timer.start()
		self._set_state("playing")

	def pause(self) -> None:
		if self.replay is None:
			return
		self._timer.stop()
		self._set_state("paused")

	def stop_replay(self) -> None:
		"""Arrête la relecture et rend la main à l'adaptateur."""
		if self.replay is None:
			return
		self._timer.stop()
		self.replay = None
		self.acq.clear_data()
		self.acq.set_replaying(False)
		self._set_state("stopped")

	def set_speed(self, speed: float) -> None:
		if self.replay is not None:
			self.replay.speed = speed

	def seek(self, t: float) -> None:
		"""Déplace la tête de lecture et recharge l'historique jusqu'à ce point."""
		if self.replay is None:
			return
		self.replay.seek(t)
		self.acq.clear_data()
		self.acq.publish(self.replay.history_until(self.replay.position))
		self.replay_position.emit(self.replay.position, self.replay.start, self.replay.end)

	def _tick(self) -> None:
		if self.replay is None:
			return
		now = time.monotonic()
		dt = now - self._last_tick
		self._last_tick = now
		batch = self.replay.advance(dt)
		if batch.samples:
			self.acq.publish(batch)
		self.replay_position.emit(self.replay.position, self.replay.start, self.replay.end)
		if self.replay.finished:
			self._timer.stop()
			self._set_state("finished")

	def _set_state(self, state: str) -> None:
		self.replay_state = state
		self.replay_changed.emit(state)

	def shutdown(self) -> None:
		"""Ferme proprement l'enregistrement en cours."""
		if self.recorder.active:
			self.stop_recording()
		self._timer.stop()
