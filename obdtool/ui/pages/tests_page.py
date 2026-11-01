"""Page Tests embarqués : Mode 06, Mode 05 et Mode 08 (contrôle de composants, mode expert)."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget

from obdtool.core import pids
from obdtool.core.errors import ObdError
from obdtool.core.onboard_tests import (
	MODE08_TESTS,
	TestsReport,
	read_mode05,
	read_mode06,
	read_mode08_support,
	run_mode08,
)
from obdtool.core.service import LinkState, StateEvent
from obdtool.ui import theme
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.widgets.tables import add_row, make_table

_HEADERS = ["Moniteur", "Test", "Valeur", "Min", "Max", "Unité", "Statut"]


def _fmt(value: float | None) -> str:
	return "—" if value is None else pids.format_value(value)


class TestsPage(BasePage):
	"""Résultats des tests embarqués."""

	title = "Tests embarqués"
	subtitle = "Résultats des autodiagnostics du calculateur : valeur mesurée et limites."

	def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		tabs = QTabWidget()
		self.content.addWidget(tabs, 1)

		# Mode 06
		tab06 = QWidget()
		layout06 = QVBoxLayout(tab06)
		row = QHBoxLayout()
		self.read06 = QPushButton("Lire les résultats (Mode 06)")
		self.read06.clicked.connect(self._read06)
		row.addWidget(self.read06)
		row.addStretch(1)
		layout06.addLayout(row)
		self.note06 = QLabel(
			"Les tests définis par le constructeur n'ont pas de nom standard : seul leur numéro est affiché."
		)
		self.note06.setWordWrap(True)
		self.note06.setObjectName("pageSubtitle")
		layout06.addWidget(self.note06)
		self.table06 = make_table(_HEADERS, stretch=1)
		layout06.addWidget(self.table06, 1)
		tabs.addTab(tab06, "Mode 06")

		# Mode 05
		tab05 = QWidget()
		layout05 = QVBoxLayout(tab05)
		row = QHBoxLayout()
		self.read05 = QPushButton("Lire la surveillance des sondes lambda (Mode 05)")
		self.read05.clicked.connect(self._read05)
		row.addWidget(self.read05)
		row.addStretch(1)
		layout05.addLayout(row)
		self.note05 = QLabel("Le Mode 05 n'existe que sur les protocoles non CAN (K-Line, J1850).")
		self.note05.setWordWrap(True)
		self.note05.setObjectName("pageSubtitle")
		layout05.addWidget(self.note05)
		self.table05 = make_table(_HEADERS, stretch=1)
		layout05.addWidget(self.table05, 1)
		tabs.addTab(tab05, "Mode 05")

		# Mode 08
		tab08 = QWidget()
		layout08 = QVBoxLayout(tab08)
		warning = QLabel(
			f"<b style='color:{theme.COLOR_WARN}'>⚠ Mode 08 : commande d'organes du véhicule.</b><br>"
			"Ce mode actionne des composants (par exemple la fermeture de l'électrovanne de mise à l'air EVAP pour un "
			"test d'étanchéité). Il est rarement supporté et peut perturber le fonctionnement du moteur. "
			"Véhicule à l'arrêt, moteur coupé, contact mis."
		)
		warning.setWordWrap(True)
		warning.setTextFormat(Qt.TextFormat.RichText)
		layout08.addWidget(warning)
		self.enable08 = QCheckBox("J'ai compris les risques : activer le Mode 08")
		self.enable08.toggled.connect(self._toggle08)
		layout08.addWidget(self.enable08)
		row = QHBoxLayout()
		self.support08 = QPushButton("Lister les tests supportés")
		self.support08.clicked.connect(self._support08)
		self.run08 = QPushButton("Lancer le test d'étanchéité EVAP (TID 01)…")
		self.run08.clicked.connect(self._run08)
		row.addWidget(self.support08)
		row.addWidget(self.run08)
		row.addStretch(1)
		layout08.addLayout(row)
		self.note08 = QLabel("Désactivé par défaut.")
		self.note08.setWordWrap(True)
		layout08.addWidget(self.note08)
		layout08.addStretch(1)
		tabs.addTab(tab08, "Mode 08")

		ctx.bridge.state_changed.connect(self._on_state)
		self._on_state(StateEvent(ctx.service.state))
		self._toggle08(False)

	# -------------------------------------------------------------- lecture
	def _fill(self, table: object, note: QLabel, report: TestsReport) -> None:
		table.setRowCount(0)  # type: ignore[attr-defined]
		if report.unavailable:
			note.setText(html.escape(report.unavailable))
			return
		failed = 0
		for result in report.results:
			color = theme.COLOR_OK if result.passed else theme.COLOR_ERROR if result.passed is False else None
			failed += result.passed is False
			add_row(
				table,  # type: ignore[arg-type]
				[
					result.mid_name,
					result.tid_name,
					_fmt(result.value),
					_fmt(result.minimum),
					_fmt(result.maximum),
					result.unit,
					{"OK": "✔ OK", "KO": "✖ KO"}.get(result.status, result.status),
				],
				color,
				6,
			)
		note.setText(
			f"{len(report.results)} résultat(s), {failed} hors limites."
			+ (" Les tests définis par le constructeur n'ont pas de nom standard." if report.results else "")
		)

	def _read06(self) -> None:
		self.read06.setEnabled(False)
		self.ctx.bridge.run(
			self.ctx.service.submit(read_mode06, "mode 06", priority=1),
			lambda report: (self.read06.setEnabled(True), self._fill(self.table06, self.note06, report)),
			lambda error: (self.read06.setEnabled(True), self.note06.setText(error.user_message)),
		)

	def _read05(self) -> None:
		self.read05.setEnabled(False)
		self.ctx.bridge.run(
			self.ctx.service.submit(read_mode05, "mode 05", priority=1),
			lambda report: (self.read05.setEnabled(True), self._fill(self.table05, self.note05, report)),
			lambda error: (self.read05.setEnabled(True), self.note05.setText(error.user_message)),
		)

	# --------------------------------------------------------------- mode 08
	def _toggle08(self, enabled: bool) -> None:
		connected = self.ctx.service.state == LinkState.CONNECTED
		self.support08.setEnabled(enabled and connected)
		self.run08.setEnabled(enabled and connected)

	def _support08(self) -> None:
		def show(tids: list[int]) -> None:
			if not tids:
				self.note08.setText("Aucun test Mode 08 supporté par ce calculateur.")
				return
			names = [f"{tid:02X} : {MODE08_TESTS.get(tid, 'test constructeur (non exécutable ici)')}" for tid in tids]
			self.note08.setText("Tests supportés : " + " ; ".join(names))

		self.ctx.bridge.run(
			self.ctx.service.submit(read_mode08_support, "mode 08", priority=1),
			show,
			lambda error: self.note08.setText(error.user_message),
		)

	def _run08(self) -> None:
		answer = QMessageBox.warning(
			self,
			"Mode 08",
			"Lancer le test d'étanchéité EVAP ? Le calculateur va fermer l'électrovanne de mise à l'air du réservoir.\n"
			"Véhicule à l'arrêt, moteur coupé, contact mis.",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
			QMessageBox.StandardButton.Cancel,
		)
		if answer != QMessageBox.StandardButton.Yes:
			return

		def done(lines: list[str]) -> None:
			self.note08.setText("Réponse du calculateur : " + " / ".join(lines))

		def failed(error: ObdError) -> None:
			self.note08.setText(f"Test refusé ou non supporté : {error.user_message}")

		self.ctx.bridge.run(
			self.ctx.service.submit(lambda link: run_mode08(link, 0x01), "mode 08", priority=1), done, failed
		)

	def _on_state(self, event: StateEvent) -> None:
		connected = event.state == LinkState.CONNECTED
		self.read06.setEnabled(connected)
		self.read05.setEnabled(connected)
		self._toggle08(self.enable08.isChecked())
