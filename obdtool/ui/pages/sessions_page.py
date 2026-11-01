"""Page Sessions : enregistrement, liste, relecture, renommage, suppression, export."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
	QComboBox,
	QFileDialog,
	QGroupBox,
	QHBoxLayout,
	QHeaderView,
	QInputDialog,
	QLabel,
	QLineEdit,
	QMessageBox,
	QPushButton,
	QSlider,
	QTableWidget,
	QTableWidgetItem,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import paths, sessions
from obdtool.core.log import format_size
from obdtool.ui import theme
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.session_control import SessionController

log = logging.getLogger(__name__)

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)


def _duration(seconds: float) -> str:
	seconds = round(seconds)
	return f"{seconds // 3600:d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class SessionsPage(BasePage):
	"""Gestion des sessions enregistrées."""

	title = "Sessions"
	subtitle = (
		"Enregistrez les valeurs interrogées, puis relisez-les dans les graphiques, le tableau de bord et les valeurs."
	)

	def __init__(self, ctx: AppContext, control: SessionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.control = control

		record_box = QGroupBox("Enregistrement")
		record_layout = QHBoxLayout(record_box)
		self.name = QLineEdit()
		self.name.setPlaceholderText("Nom de la session (facultatif)")
		record_layout.addWidget(self.name, 1)
		self.record_btn = QPushButton()
		self.record_btn.clicked.connect(self._toggle_record)
		record_layout.addWidget(self.record_btn)
		self.record_label = QLabel()
		record_layout.addWidget(self.record_label)
		self.content.addWidget(record_box)

		self.table = QTableWidget(0, 5)
		self.table.setHorizontalHeaderLabels(["Nom", "Début", "Durée", "Valeurs", "Taille"])
		self.table.verticalHeader().hide()
		self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
		self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
		header = self.table.horizontalHeader()
		header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
		self.table.doubleClicked.connect(lambda _index: self._replay())
		self.content.addWidget(self.table, 1)

		actions = QHBoxLayout()
		for label, slot in (
			("Relire", self._replay),
			("Renommer…", self._rename),
			("Supprimer…", self._delete),
			("Exporter (une colonne par valeur)…", self._export),
			("Ouvrir le dossier", self._open_folder),
			("Actualiser", self.refresh),
		):
			button = QPushButton(label)
			button.clicked.connect(slot)
			actions.addWidget(button)
		actions.addStretch(1)
		self.content.addLayout(actions)

		replay_box = QGroupBox("Relecture")
		replay_layout = QVBoxLayout(replay_box)
		self.replay_label = QLabel("Aucune session en relecture.")
		replay_layout.addWidget(self.replay_label)
		controls = QHBoxLayout()
		self.play_btn = QPushButton("▶ Lecture")
		self.play_btn.clicked.connect(self._play_pause)
		stop = QPushButton("■ Arrêter la relecture")
		stop.clicked.connect(control.stop_replay)
		controls.addWidget(self.play_btn)
		controls.addWidget(stop)
		controls.addWidget(QLabel("Vitesse :"))
		self.speed = QComboBox()
		for value in SPEEDS:
			self.speed.addItem(f"×{value:g}", value)
		self.speed.setCurrentIndex(SPEEDS.index(1.0))
		self.speed.currentIndexChanged.connect(lambda: control.set_speed(float(self.speed.currentData())))
		controls.addWidget(self.speed)
		self.slider = QSlider(Qt.Orientation.Horizontal)
		self.slider.setRange(0, 1000)
		self.slider.sliderReleased.connect(self._seek)
		controls.addWidget(self.slider, 1)
		self.position = QLabel("0:00:00 / 0:00:00")
		controls.addWidget(self.position)
		replay_layout.addLayout(controls)
		self.content.addWidget(replay_box)

		control.recording_changed.connect(self._on_recording)
		control.replay_changed.connect(self._on_replay_state)
		control.replay_position.connect(self._on_position)
		self._on_recording(control.recording, "")
		self._on_replay_state(control.replay_state)
		self.refresh()

	# -------------------------------------------------------------- liste
	def page_shown(self) -> None:
		self.refresh()

	def refresh(self) -> None:
		self.table.setRowCount(0)
		for info in sessions.list_sessions():
			row = self.table.rowCount()
			self.table.insertRow(row)
			start = datetime.fromtimestamp(info.start).strftime("%d/%m/%Y %H:%M:%S") if info.start else "—"
			values = [info.name, start, _duration(info.duration), str(len(info.keys)), format_size(info.size)]
			for column, text in enumerate(values):
				item = QTableWidgetItem(text)
				item.setData(Qt.ItemDataRole.UserRole, str(info.path))
				if column == 0:
					tooltip = ", ".join(info.keys)
					if info.metadata.get("vehicle"):
						tooltip = f"{info.metadata['vehicle']} — {tooltip}"
					item.setToolTip(tooltip)
				self.table.setItem(row, column, item)

	def _selected(self) -> Path | None:
		item = self.table.currentItem()
		if item is None:
			QMessageBox.information(self, "Sessions", "Sélectionnez une session dans la liste.")
			return None
		return Path(item.data(Qt.ItemDataRole.UserRole))

	# ------------------------------------------------------- enregistrement
	def _toggle_record(self) -> None:
		if self.control.recording:
			self.control.stop_recording()
			self.refresh()
			return
		if not self.ctx.service.link.car_connected:
			QMessageBox.information(self, "Enregistrement", "Connectez-vous au véhicule (ou au mode démo) d'abord.")
			return
		self.control.start_recording(self.name.text().strip() or None)
		self.name.clear()

	def _on_recording(self, active: bool, name: str) -> None:
		self.record_btn.setText("■ Arrêter l'enregistrement" if active else "● Démarrer l'enregistrement")
		self.record_label.setText(
			f"<span style='color:{theme.COLOR_ERROR}'>● Enregistrement en cours : {name}</span>" if active else ""
		)
		if not active:
			self.refresh()

	# ------------------------------------------------------------ relecture
	def _replay(self) -> None:
		path = self._selected()
		if path is None:
			return
		if self.control.recording:
			QMessageBox.information(self, "Relecture", "Arrêtez d'abord l'enregistrement en cours.")
			return
		try:
			replay = self.control.load(path)
		except (OSError, ValueError) as exc:
			QMessageBox.warning(self, "Relecture impossible", str(exc))
			return
		self.control.set_speed(float(self.speed.currentData()))
		self.replay_label.setText(
			f"Relecture de <b>{path.stem}</b> — {len(replay.samples)} mesures, {_duration(replay.duration)}. "
			"Les écrans Graphiques, Tableau de bord et Valeurs affichent la session."
		)

	def _play_pause(self) -> None:
		if self.control.replay_state == "playing":
			self.control.pause()
		else:
			self.control.play()

	def _seek(self) -> None:
		replay = self.control.replay
		if replay is None:
			return
		self.control.seek(replay.start + replay.duration * self.slider.value() / 1000)

	def _on_replay_state(self, state: str) -> None:
		self.play_btn.setText("⏸ Pause" if state == "playing" else "▶ Lecture")
		self.play_btn.setEnabled(state != "stopped")
		self.slider.setEnabled(state != "stopped")
		if state == "stopped":
			self.replay_label.setText("Aucune session en relecture.")
			self.position.setText("0:00:00 / 0:00:00")
			self.slider.setValue(0)
		elif state == "finished":
			self.ctx.notify("Fin de la relecture.")

	def _on_position(self, position: float, start: float, end: float) -> None:
		if not self.slider.isSliderDown() and end > start:
			self.slider.setValue(int((position - start) / (end - start) * 1000))
		self.position.setText(f"{_duration(position - start)} / {_duration(end - start)}")

	# ------------------------------------------------------- gestion fichiers
	def _rename(self) -> None:
		path = self._selected()
		if path is None:
			return
		name, ok = QInputDialog.getText(self, "Renommer la session", "Nouveau nom :", text=path.stem)
		if not ok or not name.strip():
			return
		try:
			sessions.rename_session(path, name)
		except (OSError, FileExistsError) as exc:
			QMessageBox.warning(self, "Renommer", str(exc))
		self.refresh()

	def _delete(self) -> None:
		path = self._selected()
		if path is None:
			return
		if self.control.replay is not None and self.control.replay.path == path:
			self.control.stop_replay()
		answer = QMessageBox.question(self, "Supprimer", f"Supprimer définitivement la session « {path.stem} » ?")
		if answer == QMessageBox.StandardButton.Yes:
			sessions.delete_session(path)
			self.refresh()

	def _export(self) -> None:
		path = self._selected()
		if path is None:
			return
		default = Path.home() / f"{path.stem}_tableur.csv"
		target, _ = QFileDialog.getSaveFileName(self, "Exporter la session", str(default), "CSV (*.csv)")
		if not target:
			return
		try:
			rows = sessions.export_wide(path, Path(target))
		except OSError as exc:
			QMessageBox.warning(self, "Export", str(exc))
			return
		self.ctx.notify(f"{rows} lignes exportées : {target}")

	def _open_folder(self) -> None:
		folder = paths.sessions_dir()
		folder.mkdir(parents=True, exist_ok=True)
		QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
