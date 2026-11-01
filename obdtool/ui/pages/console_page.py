"""Page Console : commandes AT et requêtes hexadécimales brutes, écoute passive du bus (expérimental)."""

from __future__ import annotations

import html
import logging
import queue
import threading
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
	QCheckBox,
	QGridLayout,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QMessageBox,
	QPushButton,
	QSpinBox,
	QTabWidget,
	QTextEdit,
	QVBoxLayout,
	QWidget,
)

from obdtool.core.console_guard import SHORTCUTS, Level, changes_format, classify, is_reset, normalize
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ObdError
from obdtool.core.service import LinkState, StateEvent
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage

log = logging.getLogger(__name__)

MAX_LINES = 5000


def _mono() -> QFont:
	font = QFont("monospace")
	font.setStyleHint(QFont.StyleHint.Monospace)
	return font


class ConsolePage(BasePage):
	"""Console experte."""

	title = "Console experte"
	subtitle = (
		"Envoi direct de commandes AT et de requêtes hexadécimales. Les services d'écriture et de programmation "
		"sont bloqués tant que le mode expert n'est pas activé."
	)

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.history: list[str] = list(ctx.settings.extra.get("console_history", []))[-200:]
		self._history_index = len(self.history)
		self._was_running = acquisition.running
		self._monitor_stop = threading.Event()
		self._monitor_queue: queue.Queue[str] = queue.Queue()
		self._monitor_count = 0

		options = QHBoxLayout()
		self.expert = QCheckBox("Mode expert")
		self.expert.setToolTip("Débloque les services d'écriture, de programmation et de commande.")
		self.expert.toggled.connect(self._toggle_expert)
		options.addWidget(self.expert)
		self.pause_acq = QCheckBox("Suspendre l'acquisition pendant l'utilisation de la console")
		self.pause_acq.setChecked(True)
		self.pause_acq.toggled.connect(self._apply_pause)
		options.addWidget(self.pause_acq)
		options.addStretch(1)
		restore = QPushButton("Rétablir la configuration de l'adaptateur")
		restore.setToolTip("ATE0, ATL0, ATH1… puis nouvelle détection du protocole")
		restore.clicked.connect(self._restore)
		options.addWidget(restore)
		self.content.addLayout(options)

		tabs = QTabWidget()
		self.content.addWidget(tabs, 1)

		# --- terminal ---
		terminal = QWidget()
		term_layout = QVBoxLayout(terminal)
		self.output = QTextEdit()
		self.output.setReadOnly(True)
		self.output.setFont(_mono())
		term_layout.addWidget(self.output, 1)
		row = QHBoxLayout()
		self.input = QLineEdit()
		self.input.setFont(_mono())
		self.input.setPlaceholderText("Commande (ex. ATI, 010C, 22F190)… — ↑/↓ : historique")
		self.input.returnPressed.connect(self._send_input)
		self.input.installEventFilter(self)
		row.addWidget(self.input, 1)
		send = QPushButton("Envoyer")
		send.clicked.connect(self._send_input)
		row.addWidget(send)
		clear = QPushButton("Effacer")
		clear.clicked.connect(self.output.clear)
		row.addWidget(clear)
		term_layout.addLayout(row)
		grid = QGridLayout()
		for index, (command, label) in enumerate(SHORTCUTS):
			button = QPushButton(command)
			button.setToolTip(label)
			button.clicked.connect(lambda _checked=False, c=command: self.send(c))
			grid.addWidget(button, index // 8, index % 8)
		term_layout.addLayout(grid)
		tabs.addTab(terminal, "Terminal")

		# --- écoute du bus ---
		monitor = QWidget()
		mon_layout = QVBoxLayout(monitor)
		warning = QLabel(
			f"<b style='color:{theme.COLOR_WARN}'>Expérimental.</b> L'écoute passive (ATMA) affiche les trames qui "
			"circulent sur le bus de la prise OBD. Sur beaucoup de véhicules, la prise est isolée du bus principal "
			"par une passerelle : peu ou pas de trames sont visibles. Les clones ELM327 saturent vite (BUFFER FULL) : "
			"utilisez un filtre."
		)
		warning.setWordWrap(True)
		warning.setTextFormat(Qt.TextFormat.RichText)
		mon_layout.addWidget(warning)
		filters = QHBoxLayout()
		filters.addWidget(QLabel("Filtre d'identifiant (ATCRA, ex. 7E8 ou 7EX) :"))
		self.filter = QLineEdit()
		self.filter.setMaximumWidth(120)
		filters.addWidget(self.filter)
		filters.addWidget(QLabel("Lignes max :"))
		self.max_lines = QSpinBox()
		self.max_lines.setRange(100, 100000)
		self.max_lines.setValue(MAX_LINES)
		filters.addWidget(self.max_lines)
		self.monitor_btn = QPushButton("▶ Démarrer l'écoute")
		self.monitor_btn.clicked.connect(self._toggle_monitor)
		filters.addWidget(self.monitor_btn)
		filters.addStretch(1)
		self.monitor_status = QLabel()
		filters.addWidget(self.monitor_status)
		mon_layout.addLayout(filters)
		self.monitor_output = QTextEdit()
		self.monitor_output.setReadOnly(True)
		self.monitor_output.setFont(_mono())
		mon_layout.addWidget(self.monitor_output, 1)
		tabs.addTab(monitor, "Écoute du bus (ATMA)")

		self._monitor_timer = QTimer(self)
		self._monitor_timer.setInterval(100)
		self._monitor_timer.timeout.connect(self._drain_monitor)
		self._monitoring = False
		ctx.bridge.state_changed.connect(self._on_state)
		self._append("info", "Console prête. Connectez-vous à l'adaptateur pour envoyer des commandes.")

	# ------------------------------------------------------------ affichage
	def _append(self, kind: str, text: str) -> None:
		colors = {
			"sent": theme.COLOR_INFO,
			"error": theme.COLOR_ERROR,
			"warn": theme.COLOR_WARN,
			"info": theme.COLOR_MUTED,
		}
		stamp = datetime.now().strftime("%H:%M:%S")
		prefix = {"sent": "&gt; ", "recv": "&lt; "}.get(kind, "")
		color = colors.get(kind)
		body = html.escape(text)
		line = f"<span style='color:{theme.COLOR_MUTED}'>{stamp}</span> {prefix}"
		line += f"<span style='color:{color}'>{body}</span>" if color else body
		self.output.append(line)
		self.output.moveCursor(QTextCursor.MoveOperation.End)

	# --------------------------------------------------------------- envoi
	def eventFilter(self, watched: QObject, event: QEvent) -> bool:
		if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
			if event.key() == Qt.Key.Key_Up and self.history:
				self._history_index = max(0, self._history_index - 1)
				self.input.setText(self.history[self._history_index])
				return True
			if event.key() == Qt.Key.Key_Down and self.history:
				self._history_index = min(len(self.history), self._history_index + 1)
				self.input.setText(self.history[self._history_index] if self._history_index < len(self.history) else "")
				return True
		return super().eventFilter(watched, event)

	def _send_input(self) -> None:
		command = self.input.text().strip()
		if command:
			self.input.clear()
			self.send(command)

	def send(self, command: str) -> None:
		"""Vérifie la commande avec le garde-fou puis l'envoie à l'adaptateur."""
		if self._monitoring:
			self._append("warn", "Arrêtez d'abord l'écoute du bus.")
			return
		text = normalize(command)
		if not self.history or self.history[-1] != text:
			self.history.append(text)
			self.ctx.settings.extra["console_history"] = self.history[-200:]
		self._history_index = len(self.history)
		verdict = classify(text)
		if verdict.level == Level.INVALID:
			self._append("error", f"{text} : {verdict.reason}")
			return
		if verdict.level == Level.EXPERT and not self.expert.isChecked():
			self._append("error", f"{text} bloquée — {verdict.reason}. Activez le mode expert pour l'envoyer.")
			log.warning("Console : commande bloquée %s (%s)", text, verdict.reason)
			return
		if verdict.level == Level.CONFIRM or (verdict.level == Level.EXPERT and self.expert.isChecked()):
			answer = QMessageBox.warning(
				self,
				"Commande sensible",
				f"{text}\n{verdict.reason}.\n\nEnvoyer cette commande au véhicule ?",
				QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
				QMessageBox.StandardButton.Cancel,
			)
			if answer != QMessageBox.StandardButton.Yes:
				self._append("info", f"{text} annulée.")
				return
		if not self.ctx.service.link.is_open:
			self._append("error", "Pas de connexion à l'adaptateur.")
			return
		if changes_format(text):
			self._append("warn", "Cette commande modifie la configuration : les autres écrans peuvent être perturbés.")
		self._append("sent", text)
		log.info("Console : %s", text)
		reset = is_reset(text)

		def job(link: ElmLink) -> tuple[list[str], bool | None]:
			lines = link.send_raw(text, check=False, timeout=10.0, clean=False)
			restored = link.restore_defaults() if reset else None
			return lines, restored

		self.ctx.bridge.run(
			self.ctx.service.submit(job, "console", priority=1),
			self._on_answer,
			lambda error: self._append("error", error.user_message),
		)

	def _on_answer(self, result: tuple[list[str], bool | None]) -> None:
		lines, restored = result
		for line in lines or ["(réponse vide)"]:
			self._append("recv", line)
		if restored is not None:
			self._append(
				"info",
				"Configuration de l'adaptateur rétablie"
				+ (" ; véhicule détecté." if restored else " ; véhicule muet."),
			)

	# ---------------------------------------------------------- mode expert
	def _toggle_expert(self, enabled: bool) -> None:
		if not enabled:
			self._append("info", "Mode expert désactivé.")
			return
		answer = QMessageBox.warning(
			self,
			"Mode expert",
			"Le mode expert autorise l'envoi de requêtes d'écriture, de programmation, de réinitialisation ou de "
			"commande d'organes. Une mauvaise requête peut dérégler ou bloquer un calculateur.\n\n"
			"Activer le mode expert pour cette session ?",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
			QMessageBox.StandardButton.Cancel,
		)
		if answer != QMessageBox.StandardButton.Yes:
			self.expert.blockSignals(True)
			self.expert.setChecked(False)
			self.expert.blockSignals(False)
			return
		log.warning("Console : mode expert activé")
		self._append("warn", "Mode expert ACTIVÉ : les requêtes d'écriture sont possibles (avec confirmation).")

	def _restore(self) -> None:
		if not self.ctx.service.link.is_open:
			return
		self.ctx.bridge.run(
			self.ctx.service.submit(lambda link: link.restore_defaults(), "configuration adaptateur", priority=1),
			lambda ok: self._append(
				"info", "Configuration rétablie" + (" ; véhicule détecté." if ok else " ; véhicule muet.")
			),
			lambda error: self._append("error", error.user_message),
		)

	# -------------------------------------------------------- écoute du bus
	def _toggle_monitor(self) -> None:
		if self._monitoring:
			self._monitor_stop.set()
			return
		if not self.ctx.service.link.is_open:
			self.monitor_status.setText("Pas de connexion à l'adaptateur.")
			return
		filt = normalize(self.filter.text())
		if filt and not all(c in "0123456789ABCDEFX" for c in filt):
			self.monitor_status.setText("Filtre invalide (chiffres hexadécimaux et X).")
			return
		self._monitor_stop.clear()
		self._monitoring = True
		self._monitor_count = 0
		self.monitor_btn.setText("■ Arrêter l'écoute")
		self.monitor_status.setText("Écoute en cours…")
		self._monitor_timer.start()
		stop = self._monitor_stop
		out = self._monitor_queue

		def job(link: ElmLink) -> None:
			if filt:
				link.send_raw(f"ATCRA{filt}", check=False, timeout=2.0)
			try:
				link.monitor("ATMA", out.put, stop.is_set)
			finally:
				if filt and link.is_open:
					link.send_raw("ATAR", check=False, timeout=2.0)

		def finished(_result: object = None) -> None:
			self._monitoring = False
			self.monitor_btn.setText("▶ Démarrer l'écoute")
			self._drain_monitor()
			self._monitor_timer.stop()
			self.monitor_status.setText(f"Écoute arrêtée — {self._monitor_count} ligne(s).")

		def failed(error: ObdError) -> None:
			finished()
			self.monitor_status.setText(f"Écoute interrompue : {error.user_message}")

		self.ctx.bridge.run(self.ctx.service.submit(job, "écoute du bus", priority=1), finished, failed)

	def _drain_monitor(self) -> None:
		lines = []
		while True:
			try:
				lines.append(self._monitor_queue.get_nowait())
			except queue.Empty:
				break
		if not lines:
			return
		self._monitor_count += len(lines)
		self.monitor_output.append(html.escape("\n".join(lines)).replace("\n", "<br>"))
		document = self.monitor_output.document()
		if document.blockCount() > self.max_lines.value():
			cursor = QTextCursor(document)
			cursor.movePosition(QTextCursor.MoveOperation.Start)
			cursor.movePosition(
				QTextCursor.MoveOperation.Down,
				QTextCursor.MoveMode.KeepAnchor,
				document.blockCount() - self.max_lines.value(),
			)
			cursor.removeSelectedText()
		self.monitor_output.moveCursor(QTextCursor.MoveOperation.End)
		self.monitor_status.setText(f"Écoute en cours — {self._monitor_count} ligne(s).")

	# ------------------------------------------------------------ visibilité
	def _apply_pause(self, *_args: object) -> None:
		if self.isVisible() and self.pause_acq.isChecked():
			self.acq.set_running(False)
		else:
			self.acq.set_running(self._was_running)

	def page_shown(self) -> None:
		self._was_running = self.acq.running
		self.ctx.service.auto_vehicle_retry = False
		self._apply_pause()
		self.input.setFocus()

	def page_hidden(self) -> None:
		if self._monitoring:
			self._monitor_stop.set()
		self.ctx.service.auto_vehicle_retry = True
		if self.pause_acq.isChecked():
			self.acq.set_running(self._was_running)

	def _on_state(self, event: StateEvent) -> None:
		if event.state in (LinkState.CONNECTED, LinkState.ADAPTER_ONLY):
			self._append("info", f"{event.state.value}.")
		elif event.state == LinkState.DISCONNECTED:
			self._append("info", "Déconnecté.")
