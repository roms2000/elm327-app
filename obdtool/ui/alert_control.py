"""Alertes : évaluation des règles, bandeau visuel et signal sonore optionnel."""

from __future__ import annotations

import logging
import math
import struct
import wave
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from obdtool.core import paths
from obdtool.core.alerts import AlertEngine, AlertEvent, AlertRule, dump_rules, load_rules
from obdtool.core.samples import SampleBatch
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.qtcompat import emit

log = logging.getLogger(__name__)


def _beep_file() -> Path:
	"""Crée (une fois) un court signal sonore WAV dans le dossier de configuration."""
	path = paths.config_dir() / "alerte.wav"
	if path.exists():
		return path
	path.parent.mkdir(parents=True, exist_ok=True)
	rate = 22050
	frames = bytearray()
	for repeat in range(2):
		for i in range(int(rate * 0.18)):
			envelope = min(1.0, i / 200, (rate * 0.18 - i) / 200)
			frames += struct.pack("<h", int(12000 * envelope * math.sin(2 * math.pi * 880 * i / rate)))
		if repeat == 0:
			frames += b"\x00\x00" * int(rate * 0.08)
	with wave.open(str(path), "wb") as wav:
		wav.setnchannels(1)
		wav.setsampwidth(2)
		wav.setframerate(rate)
		wav.writeframes(bytes(frames))
	return path


class AlertController(QObject):
	"""Évalue les alertes sur les lots reçus (temps réel et relecture)."""

	changed = Signal()

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QObject | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.engine = AlertEngine()
		self._sound = None
		self.set_rules(load_rules(ctx.settings.extra.get("alerts")), save=False)
		acquisition.batch.connect(self._on_batch)

	@property
	def rules(self) -> list[AlertRule]:
		return self.engine.rules

	def set_rules(self, rules: list[AlertRule], save: bool = True) -> None:
		self.engine.set_rules(rules)
		self.acq.require("alerts", self.engine.keys())
		if save:
			self.ctx.settings.extra["alerts"] = dump_rules(rules)
			self.ctx.save_settings()
		emit(self.changed)

	def active(self) -> list[AlertRule]:
		return self.engine.active()

	def _on_batch(self, batch: SampleBatch) -> None:
		events = self.engine.evaluate(batch)
		if not events:
			return
		for event in events:
			self._log(event)
		if any(e.active and e.rule.sound for e in events) and self.ctx.settings.general.alert_sound:
			self._play()
		emit(self.changed)

	def _log(self, event: AlertEvent) -> None:
		if event.active:
			log.warning("Alerte : %s (valeur %.2f)", event.rule.describe(), event.value)
		else:
			log.info("Fin d'alerte : %s", event.rule.describe())

	def _play(self) -> None:
		try:
			from PySide6.QtMultimedia import QSoundEffect

			if self._sound is None:
				self._sound = QSoundEffect(self)
				self._sound.setSource(QUrl.fromLocalFile(str(_beep_file())))
				self._sound.setVolume(0.8)
			self._sound.play()
		except Exception as exc:  # QtMultimedia indisponible ou pas de sortie audio
			log.debug("Son d'alerte indisponible (%s), bip système", exc)
			QApplication.beep()


class AlertBanner(QWidget):
	"""Bandeau affiché en haut de la fenêtre tant qu'une alerte est active."""

	def __init__(self, control: AlertController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.control = control
		self.setAutoFillBackground(True)
		self.setStyleSheet(f"background:{theme.COLOR_ERROR}; color:white;")
		layout = QHBoxLayout(self)
		layout.setContentsMargins(12, 6, 12, 6)
		self.label = QLabel()
		self.label.setStyleSheet("color:white; font-weight:600; font-size:12pt;")
		self.label.setTextFormat(Qt.TextFormat.RichText)
		layout.addWidget(self.label, 1)
		self.dismiss = QPushButton("Masquer")
		self.dismiss.setStyleSheet("color:black;")
		self.dismiss.clicked.connect(self.hide)
		layout.addWidget(self.dismiss)
		self.hide()
		control.changed.connect(self.refresh)

	def refresh(self) -> None:
		active = self.control.active()
		if not active:
			self.hide()
			return
		self.label.setText("⚠ " + " — ".join(rule.describe() for rule in active))
		self.show()
