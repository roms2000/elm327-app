"""Visionneuse des journaux de l'application."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
	QCheckBox,
	QComboBox,
	QDialog,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QListWidget,
	QListWidgetItem,
	QMessageBox,
	QPlainTextEdit,
	QPushButton,
	QSplitter,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import log as applog
from obdtool.core import paths

log = logging.getLogger(__name__)

_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def open_log_folder() -> None:
	"""Ouvre le dossier des journaux dans le gestionnaire de fichiers."""
	folder = paths.log_dir()
	folder.mkdir(parents=True, exist_ok=True)
	if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
		log.warning("Impossible d'ouvrir le dossier %s", folder)


def confirm_delete_old_logs(parent: QWidget) -> bool:
	"""Demande confirmation puis supprime les journaux des sessions précédentes."""
	sessions = [s for s in applog.list_sessions() if not s.is_current]
	if not sessions:
		QMessageBox.information(parent, "Journaux", "Aucun ancien journal à supprimer.")
		return False
	size = applog.format_size(sum(s.size for s in sessions))
	answer = QMessageBox.question(
		parent,
		"Supprimer les journaux",
		f"Supprimer les journaux de {len(sessions)} session(s) précédente(s) ({size}) ?\n"
		"Le journal de la session en cours est conservé.",
	)
	if answer != QMessageBox.StandardButton.Yes:
		return False
	removed = applog.delete_sessions(sessions)
	log.info("%d fichier(s) de journal supprimé(s)", removed)
	return True


class LogViewerDialog(QDialog):
	"""Liste des sessions de journal et affichage de leur contenu."""

	def __init__(self, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.setWindowTitle("Journaux de l'application")
		self.resize(1000, 650)
		layout = QVBoxLayout(self)

		splitter = QSplitter(Qt.Orientation.Horizontal)
		left = QWidget()
		left_layout = QVBoxLayout(left)
		left_layout.setContentsMargins(0, 0, 0, 0)
		left_layout.addWidget(QLabel("Sessions"))
		self.sessions = QListWidget()
		self.sessions.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
		self.sessions.currentItemChanged.connect(self._show_selected)
		left_layout.addWidget(self.sessions)
		self.total = QLabel()
		left_layout.addWidget(self.total)
		splitter.addWidget(left)

		right = QWidget()
		right_layout = QVBoxLayout(right)
		right_layout.setContentsMargins(0, 0, 0, 0)
		filters = QHBoxLayout()
		filters.addWidget(QLabel("Niveau minimal :"))
		self.level = QComboBox()
		self.level.addItems(_LEVELS)
		self.level.setCurrentText("DEBUG")
		self.level.currentTextChanged.connect(self._render)
		filters.addWidget(self.level)
		self.search = QLineEdit()
		self.search.setPlaceholderText("Rechercher…")
		self.search.textChanged.connect(self._render)
		filters.addWidget(self.search, 1)
		self.follow = QCheckBox("Suivre la session en cours")
		self.follow.setChecked(True)
		filters.addWidget(self.follow)
		right_layout.addLayout(filters)
		self.text = QPlainTextEdit()
		self.text.setReadOnly(True)
		self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
		mono = QFont("monospace")
		mono.setStyleHint(QFont.StyleHint.Monospace)
		self.text.setFont(mono)
		right_layout.addWidget(self.text)
		splitter.addWidget(right)
		splitter.setSizes([260, 740])
		layout.addWidget(splitter, 1)

		buttons = QHBoxLayout()
		refresh = QPushButton("Actualiser")
		refresh.clicked.connect(self.refresh)
		folder = QPushButton("Ouvrir le dossier")
		folder.clicked.connect(open_log_folder)
		delete_sel = QPushButton("Supprimer la sélection")
		delete_sel.clicked.connect(self._delete_selected)
		delete_old = QPushButton("Supprimer les anciens journaux")
		delete_old.clicked.connect(self._delete_old)
		close = QPushButton("Fermer")
		close.clicked.connect(self.accept)
		for button in (refresh, folder, delete_sel, delete_old):
			buttons.addWidget(button)
		buttons.addStretch(1)
		buttons.addWidget(close)
		layout.addLayout(buttons)

		self._raw = ""
		self._timer = QTimer(self)
		self._timer.setInterval(1500)
		self._timer.timeout.connect(self._tick)
		self._timer.start()
		self.refresh()

	def refresh(self) -> None:
		"""Recharge la liste des sessions."""
		selected = self._current_key()
		self.sessions.blockSignals(True)
		self.sessions.clear()
		for session in applog.list_sessions():
			label = session.started.strftime("%d/%m/%Y %H:%M:%S")
			label += f"  ({applog.format_size(session.size)})"
			if session.is_current:
				label += "  — en cours"
			item = QListWidgetItem(label)
			item.setData(Qt.ItemDataRole.UserRole, session)
			self.sessions.addItem(item)
			if session.key == selected or (selected is None and session.is_current):
				self.sessions.setCurrentItem(item)
		self.sessions.blockSignals(False)
		if self.sessions.currentItem() is None and self.sessions.count():
			self.sessions.setCurrentRow(0)
		self.total.setText(f"Espace utilisé : {applog.format_size(applog.total_size())}")
		self._show_selected()

	def _current_key(self) -> str | None:
		item = self.sessions.currentItem()
		return item.data(Qt.ItemDataRole.UserRole).key if item else None

	def _show_selected(self, *_args: object) -> None:
		item = self.sessions.currentItem()
		if item is None:
			self._raw = ""
		else:
			session: applog.LogSession = item.data(Qt.ItemDataRole.UserRole)
			# Fichiers tournants : .log.2 (le plus ancien) … .log (le plus récent).
			ordered = sorted(session.files, key=lambda p: -int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0)
			self._raw = "".join(applog.read_log(p) for p in ordered)
		self._render()

	def _render(self, *_args: object) -> None:
		min_index = _LEVELS.index(self.level.currentText())
		allowed = set(_LEVELS[min_index:])
		needle = self.search.text().strip().lower()
		lines = []
		keep_continuation = False
		for line in self._raw.splitlines():
			parts = line.split(maxsplit=3)
			if len(parts) >= 3 and parts[2] in _LEVELS:
				keep_continuation = parts[2] in allowed
			if keep_continuation and (not needle or needle in line.lower()):
				lines.append(line)
		bar = self.text.verticalScrollBar()
		at_end = bar.value() >= bar.maximum() - 4
		self.text.setPlainText("\n".join(lines))
		if at_end:
			self.text.moveCursor(QTextCursor.MoveOperation.End)

	def _tick(self) -> None:
		item = self.sessions.currentItem()
		if self.follow.isChecked() and item and item.data(Qt.ItemDataRole.UserRole).is_current:
			self._show_selected()

	def _delete_selected(self) -> None:
		sessions = [i.data(Qt.ItemDataRole.UserRole) for i in self.sessions.selectedItems()]
		sessions = [s for s in sessions if not s.is_current]
		if not sessions:
			QMessageBox.information(self, "Journaux", "Sélectionnez une ou plusieurs sessions terminées.")
			return
		answer = QMessageBox.question(self, "Supprimer", f"Supprimer {len(sessions)} session(s) de journal ?")
		if answer == QMessageBox.StandardButton.Yes:
			applog.delete_sessions(sessions)
			self.refresh()

	def _delete_old(self) -> None:
		if confirm_delete_old_logs(self):
			self.refresh()
