"""Page Défauts : codes mémorisés / en attente / permanents, freeze frame, effacement, historique, rapport."""

from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from pathlib import Path

import obd
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
	QCheckBox,
	QDialog,
	QDialogButtonBox,
	QDoubleSpinBox,
	QFileDialog,
	QHBoxLayout,
	QHeaderView,
	QLabel,
	QMessageBox,
	QPushButton,
	QSplitter,
	QTableWidget,
	QTableWidgetItem,
	QTabWidget,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import dtc, pids
from obdtool.core.dtc_history import DtcHistory, HistoryRecord
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.poller import decode_messages
from obdtool.core.report import DtcReport, to_html, to_text
from obdtool.core.service import LinkState, StateEvent
from obdtool.ui import theme
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage

log = logging.getLogger(__name__)

_CLEAR_WARNING = """<p><b>Avant d'effacer les codes défaut :</b></p>
<ul>
<li>mettez le <b>contact</b>, <b>moteur arrêté</b> (la plupart des calculateurs refusent l'effacement moteur tournant) ;</li>
<li>notez ou exportez les codes et le freeze frame : ils seront <b>perdus</b>.</li>
</ul>
<p><b>Conséquences :</b></p>
<ul>
<li>le voyant moteur s'éteint, mais il se rallumera si la panne est toujours présente ;</li>
<li>les <b>moniteurs de préparation</b> (readiness) repassent à « non terminé » : le véhicule sera refusé
au contrôle technique anti-pollution tant qu'un cycle de conduite complet n'aura pas été effectué ;</li>
<li>les freeze frames, résultats des tests embarqués (mode 06) et adaptations de richesse peuvent être remis à zéro.</li>
</ul>
<p>L'effacement ne répare rien : il supprime seulement la trace du défaut.</p>"""


def read_full_report(link: ElmLink, make: str) -> DtcReport:
	"""Lecture complète (exécutée dans le thread de travail)."""
	report = DtcReport(adapter=link.info.version, protocol=link.info.protocol_name)
	report.mil = dtc.read_mil_status(link)
	for kind in dtc.DtcKind:
		report.entries[kind] = dtc.read_dtcs(link, kind, make)
	report.freeze = dtc.read_freeze_frame(link, make)
	for pid, attribute in ((0x31, "distance_since_clear"), (0x21, "distance_with_mil")):
		try:
			messages = link.request(f"01{pid:02X}")
			value = decode_messages(
				pids.CATALOG["DISTANCE_SINCE_DTC_CLEAR" if pid == 0x31 else "DISTANCE_W_MIL"], messages
			)
			if isinstance(value, float):
				setattr(report, attribute, value)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
	try:
		vin = link.query(obd.commands.VIN).value
		if vin:
			report.vin = vin.decode("ascii", errors="ignore") if isinstance(vin, bytes | bytearray) else str(vin)
			report.vin = report.vin.strip("\x00 ").strip()
	except ObdError as exc:
		if exc.is_link_lost:
			raise
	except Exception as exc:
		log.debug("VIN illisible : %s", exc)
	return report


def read_full_checkup(link: ElmLink, make: str) -> DtcReport:
	"""Bilan complet : codes, freeze frame, moniteurs, tests mode 06, identification."""
	from obdtool.core.onboard_tests import read_mode06
	from obdtool.core.readiness import read_readiness
	from obdtool.core.vehicle_info import read_vehicle_info

	report = read_full_report(link, make)
	report.readiness = read_readiness(link, 0x01)
	report.tests = read_mode06(link)
	report.vehicle_info = read_vehicle_info(link)
	if report.vehicle_info.vin:
		report.vin = report.vehicle_info.vin
	return report


def clear_codes(link: ElmLink) -> None:
	"""Vérifie que le moteur est arrêté puis efface les codes (thread de travail)."""
	try:
		rpm = decode_messages(pids.CATALOG["RPM"], link.request("010C"))
	except ObdError as exc:
		if exc.is_link_lost:
			raise
		rpm = None
	if isinstance(rpm, float) and rpm > 300:
		raise ObdError(
			ErrorKind.BLOCKED, f"Le moteur tourne ({rpm:.0f} tr/min) : arrêtez-le, contact mis, puis recommencez."
		)
	dtc.clear_dtcs(link)


class ClearConfirmDialog(QDialog):
	"""Première confirmation : rappel des conditions et des conséquences."""

	def __init__(self, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.setWindowTitle("Effacer les codes défaut")
		layout = QVBoxLayout(self)
		text = QLabel(_CLEAR_WARNING)
		text.setWordWrap(True)
		text.setTextFormat(Qt.TextFormat.RichText)
		layout.addWidget(text)
		self.understood = QCheckBox("J'ai compris : contact mis, moteur arrêté, codes notés.")
		layout.addWidget(self.understood)
		buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
		self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
		self.ok.setText("Continuer…")
		self.ok.setEnabled(False)
		self.understood.toggled.connect(self.ok.setEnabled)
		buttons.accepted.connect(self.accept)
		buttons.rejected.connect(self.reject)
		layout.addWidget(buttons)
		self.resize(560, 420)


class DtcPage(BasePage):
	"""Lecture et effacement des codes défaut."""

	title = "Codes défaut"
	subtitle = "Modes 03 (mémorisés), 07 (en attente), 0A (permanents), 02 (freeze frame) et 04 (effacement)."

	def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.history = DtcHistory()
		self.report: DtcReport | None = None

		bar = QHBoxLayout()
		self.read_btn = QPushButton("Lire les codes")
		self.read_btn.clicked.connect(self.read_codes)
		self.clear_btn = QPushButton("Effacer les codes…")
		self.clear_btn.clicked.connect(self._clear)
		self.export_btn = QPushButton("Exporter le rapport…")
		self.export_btn.clicked.connect(self._export)
		self.checkup_btn = QPushButton("Bilan complet…")
		self.checkup_btn.setToolTip(
			"Codes, freeze frame, moniteurs, tests mode 06 et identification dans un rapport HTML"
		)
		self.checkup_btn.clicked.connect(self.full_checkup)
		for button in (self.read_btn, self.clear_btn, self.export_btn, self.checkup_btn):
			bar.addWidget(button)
		bar.addSpacing(20)
		bar.addWidget(QLabel("Kilométrage (facultatif) :"))
		self.odometer = QDoubleSpinBox()
		self.odometer.setRange(0, 2_000_000)
		self.odometer.setDecimals(0)
		self.odometer.setSuffix(" km")
		self.odometer.setSpecialValueText("non saisi")
		self.odometer.setToolTip("L'OBD générique ne donne pas le kilométrage : saisissez-le pour l'historique.")
		bar.addWidget(self.odometer)
		bar.addStretch(1)
		self.mil = QLabel()
		self.mil.setTextFormat(Qt.TextFormat.RichText)
		bar.addWidget(self.mil)
		self.content.addLayout(bar)

		self.status = QLabel()
		self.status.setWordWrap(True)
		self.status.setObjectName("pageSubtitle")
		self.content.addWidget(self.status)

		splitter = QSplitter(Qt.Orientation.Vertical)
		self.codes = QTableWidget(0, 4)
		self.codes.setHorizontalHeaderLabels(["Code", "Type", "Description", "Remarque"])
		self.codes.verticalHeader().hide()
		self.codes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.codes.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
		header = self.codes.horizontalHeader()
		header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
		splitter.addWidget(self.codes)

		tabs = QTabWidget()
		freeze_box = QWidget()
		freeze_layout = QVBoxLayout(freeze_box)
		self.freeze_label = QLabel("Lisez les codes pour afficher le freeze frame.")
		self.freeze_label.setWordWrap(True)
		freeze_layout.addWidget(self.freeze_label)
		self.freeze = QTableWidget(0, 2)
		self.freeze.setHorizontalHeaderLabels(["Valeur au moment du défaut", "Mesure"])
		self.freeze.verticalHeader().hide()
		self.freeze.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.freeze.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
		self.freeze.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
		freeze_layout.addWidget(self.freeze)
		tabs.addTab(freeze_box, "Freeze frame")

		history_box = QWidget()
		history_layout = QVBoxLayout(history_box)
		self.history_table = QTableWidget(0, 6)
		self.history_table.setHorizontalHeaderLabels(
			["Date", "Événement", "Codes", "Voyant", "Dist. depuis effacement", "Kilométrage"]
		)
		self.history_table.verticalHeader().hide()
		self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
		self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
		self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
		history_layout.addWidget(self.history_table)
		row = QHBoxLayout()
		delete = QPushButton("Supprimer la ligne")
		delete.clicked.connect(self._delete_history)
		purge = QPushButton("Vider l'historique…")
		purge.clicked.connect(self._purge_history)
		row.addWidget(delete)
		row.addWidget(purge)
		row.addStretch(1)
		self.recurring = QLabel()
		self.recurring.setTextFormat(Qt.TextFormat.RichText)
		row.addWidget(self.recurring)
		history_layout.addLayout(row)
		tabs.addTab(history_box, "Historique local")
		splitter.addWidget(tabs)
		splitter.setSizes([300, 300])
		self.content.addWidget(splitter, 1)

		ctx.bridge.state_changed.connect(self._on_state)
		self._on_state(StateEvent(ctx.service.state))
		self._set_mil(None)
		self._load_history()

	def _make(self) -> str:
		return str(self.ctx.settings.extra.get("vehicle_make", "renault"))

	# --------------------------------------------------------------- lecture
	def read_codes(self) -> None:
		"""Lance la lecture complète des codes."""
		self.read_btn.setEnabled(False)
		self.status.setText("Lecture en cours…")
		make = self._make()
		self.ctx.bridge.run(
			self.ctx.service.submit(lambda link: read_full_report(link, make), "lecture des codes", priority=1),
			self._on_report,
			self._on_error,
		)

	def _on_report(self, report: DtcReport) -> None:
		self.read_btn.setEnabled(self.ctx.service.state == LinkState.CONNECTED)
		report.vehicle = str(self.ctx.settings.extra.get("vehicle_name", ""))
		self.report = report
		record = HistoryRecord(
			timestamp=time.time(),
			vin=report.vin,
			mil=report.mil.mil_on if report.mil else None,
			distance_since_clear=report.distance_since_clear,
			distance_with_mil=report.distance_with_mil,
			odometer=self.odometer.value() or None,
			stored=[e.code for e in report.entries.get(dtc.DtcKind.STORED) or []],
			pending=[e.code for e in report.entries.get(dtc.DtcKind.PENDING) or []],
			permanent=[e.code for e in report.entries.get(dtc.DtcKind.PERMANENT) or []],
		)
		self.history.add(record)
		report.recurring = self.history.recurring_codes(report.vin or None)
		self._show_report(report)
		self._load_history()

	def _on_error(self, error: ObdError) -> None:
		self.read_btn.setEnabled(self.ctx.service.state == LinkState.CONNECTED)
		self.status.setText(f"<span style='color:{theme.COLOR_ERROR}'>{html.escape(error.user_message)}</span>")

	def _set_mil(self, status: dtc.MilStatus | None) -> None:
		if status is None:
			self.mil.setText("Voyant moteur : —")
		elif status.mil_on:
			self.mil.setText(
				f"<span style='color:{theme.COLOR_WARN};font-size:14pt'>⚠</span> "
				f"<b style='color:{theme.COLOR_WARN}'>Voyant moteur ALLUMÉ</b> — {status.dtc_count} défaut(s)"
			)
		else:
			self.mil.setText(
				f"<span style='color:{theme.COLOR_OK}'>●</span> Voyant moteur éteint — {status.dtc_count} défaut(s) mémorisé(s)"
			)

	def _show_report(self, report: DtcReport) -> None:
		self._set_mil(report.mil)
		self.codes.setRowCount(0)
		notes = []
		for kind in dtc.DtcKind:
			entries = report.entries.get(kind)
			if entries is None:
				notes.append(f"mode {kind.mode} ({kind.label.lower()}s) non supporté par le calculateur")
				continue
			for entry in entries:
				row = self.codes.rowCount()
				self.codes.insertRow(row)
				recur = report.recurring.get(entry.code)
				remark = f"revenu après {recur} effacement(s)" if recur else ""
				if entry.description.source == "générique":
					remark = (remark + " — " if remark else "") + dtc.code_category(entry.code)
				values = [entry.code, kind.label, entry.description.display, remark]
				for column, value in enumerate(values):
					item = QTableWidgetItem(value)
					if column == 0:
						font = item.font()
						font.setBold(True)
						font.setFamily("monospace")
						item.setFont(font)
					if column == 3 and recur:
						item.setForeground(QBrush(QColor(theme.COLOR_WARN)))
					item.setToolTip(f"{entry.code} — {dtc.code_category(entry.code)}")
					self.codes.setItem(row, column, item)
		total = self.codes.rowCount()
		text = f"Lecture du {datetime.now():%d/%m/%Y %H:%M:%S} : {total} code(s)."
		if report.distance_since_clear is not None:
			text += f" Distance depuis le dernier effacement : {report.distance_since_clear:.0f} km."
		if notes:
			text += " " + " ; ".join(notes).capitalize() + "."
		self.status.setText(text)
		self._show_freeze(report.freeze)

	def _show_freeze(self, freeze: dtc.FreezeFrame | None) -> None:
		self.freeze.setRowCount(0)
		if freeze is None:
			self.freeze_label.setText("Freeze frame (mode 02) non supporté par le calculateur.")
			return
		if not freeze.dtc:
			self.freeze_label.setText("Aucun freeze frame mémorisé.")
			return
		description = freeze.description.display if freeze.description else ""
		self.freeze_label.setText(
			f"Instantané enregistré lors du défaut <b>{freeze.dtc}</b> — {html.escape(description)}"
		)
		for definition, value in freeze.values:
			row = self.freeze.rowCount()
			self.freeze.insertRow(row)
			self.freeze.setItem(row, 0, QTableWidgetItem(definition.name))
			text = pids.format_value(value, definition.unit)
			unit = definition.unit if value is not None and not isinstance(value, str) else ""
			self.freeze.setItem(row, 1, QTableWidgetItem(f"{text} {unit}".strip()))

	# ------------------------------------------------------------ effacement
	def _clear(self) -> None:
		if ClearConfirmDialog(self).exec() != QDialog.DialogCode.Accepted:
			return
		answer = QMessageBox.warning(
			self,
			"Confirmer l'effacement",
			"Dernière confirmation : effacer définitivement les codes défaut et les données associées ?",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
			QMessageBox.StandardButton.Cancel,
		)
		if answer != QMessageBox.StandardButton.Yes:
			return
		before = self.report
		self.clear_btn.setEnabled(False)

		def done(_result: object) -> None:
			self.clear_btn.setEnabled(True)
			record = HistoryRecord(
				timestamp=time.time(),
				event="clear",
				vin=before.vin if before else "",
				odometer=self.odometer.value() or None,
				stored=[e.code for e in (before.entries.get(dtc.DtcKind.STORED) or [])] if before else [],
				pending=[e.code for e in (before.entries.get(dtc.DtcKind.PENDING) or [])] if before else [],
			)
			self.history.add(record)
			self.ctx.notify("Codes défaut effacés.")
			self.read_codes()

		def failed(error: ObdError) -> None:
			self.clear_btn.setEnabled(True)
			QMessageBox.warning(self, "Effacement impossible", error.user_message)

		self.ctx.bridge.run(self.ctx.service.submit(clear_codes, "effacement des codes", priority=1), done, failed)

	# -------------------------------------------------------------- rapport
	def _export(self) -> None:
		if self.report is None:
			QMessageBox.information(self, "Rapport", "Lisez d'abord les codes défaut.")
			return
		default = Path.home() / f"rapport_defauts_{datetime.now():%Y%m%d_%H%M}.html"
		path, selected = QFileDialog.getSaveFileName(
			self, "Exporter le rapport", str(default), "Page HTML (*.html);;Texte (*.txt)"
		)
		if not path:
			return
		as_text = path.endswith(".txt") or selected.startswith("Texte")
		try:
			Path(path).write_text(to_text(self.report) if as_text else to_html(self.report), encoding="utf-8")
		except OSError as exc:
			QMessageBox.warning(self, "Export impossible", str(exc))
			return
		self.ctx.notify(f"Rapport enregistré : {path}")

	def full_checkup(self) -> None:
		"""Lit tout et propose d'enregistrer le bilan au format HTML."""
		if self.ctx.service.state != LinkState.CONNECTED:
			QMessageBox.information(self, "Bilan", "Connectez-vous au véhicule d'abord.")
			return
		self.checkup_btn.setEnabled(False)
		self.status.setText("Bilan en cours (codes, moniteurs, tests, identification)…")
		make = self._make()

		def done(report: DtcReport) -> None:
			self.checkup_btn.setEnabled(True)
			self._on_report(report)
			default = Path.home() / f"bilan_obd_{datetime.now():%Y%m%d_%H%M}.html"
			path, _ = QFileDialog.getSaveFileName(self, "Enregistrer le bilan", str(default), "Page HTML (*.html)")
			if path:
				Path(path).write_text(to_html(report), encoding="utf-8")
				self.ctx.notify(f"Bilan enregistré : {path}")

		def failed(error: ObdError) -> None:
			self.checkup_btn.setEnabled(True)
			self._on_error(error)

		self.ctx.bridge.run(
			self.ctx.service.submit(lambda link: read_full_checkup(link, make), "bilan complet", priority=1),
			done,
			failed,
		)

	# ------------------------------------------------------------ historique
	def _load_history(self) -> None:
		records = self.history.records()
		self.history_table.setRowCount(0)
		for record in records:
			row = self.history_table.rowCount()
			self.history_table.insertRow(row)
			codes = (
				record.stored
				+ [f"{c} (attente)" for c in record.pending]
				+ [f"{c} (permanent)" for c in record.permanent]
			)
			values = [
				datetime.fromtimestamp(record.timestamp).strftime("%d/%m/%Y %H:%M"),
				"Effacement" if record.event == "clear" else "Lecture",
				", ".join(codes) or "aucun",
				"—" if record.mil is None else ("allumé" if record.mil else "éteint"),
				"—" if record.distance_since_clear is None else f"{record.distance_since_clear:.0f} km",
				"—" if record.odometer is None else f"{record.odometer:.0f} km",
			]
			for column, value in enumerate(values):
				item = QTableWidgetItem(value)
				item.setData(Qt.ItemDataRole.UserRole, record.id)
				if record.event == "clear":
					item.setForeground(QBrush(QColor(theme.COLOR_INFO)))
				self.history_table.setItem(row, column, item)
		recurring = self.history.recurring_codes()
		if recurring:
			text = ", ".join(f"{code} ×{count}" for code, count in sorted(recurring.items()))
			self.recurring.setText(
				f"<span style='color:{theme.COLOR_WARN}'>Codes revenus après effacement : {text}</span>"
			)
		else:
			self.recurring.setText("")

	def _delete_history(self) -> None:
		item = self.history_table.currentItem()
		if item is None:
			return
		self.history.delete(int(item.data(Qt.ItemDataRole.UserRole)))
		self._load_history()

	def _purge_history(self) -> None:
		answer = QMessageBox.question(self, "Historique", "Supprimer tout l'historique local des codes défaut ?")
		if answer == QMessageBox.StandardButton.Yes:
			self.history.clear_all()
			self._load_history()

	# --------------------------------------------------------------- état
	def _on_state(self, event: StateEvent) -> None:
		connected = event.state == LinkState.CONNECTED
		self.read_btn.setEnabled(connected)
		self.clear_btn.setEnabled(connected)
		self.checkup_btn.setEnabled(connected)
		if not connected and event.state != LinkState.CONNECTING:
			self.status.setText("Connectez-vous au véhicule pour lire les codes défaut.")
