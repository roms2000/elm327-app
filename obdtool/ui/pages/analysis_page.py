"""Page Analyses : sondes lambda, corrections de richesse, test batterie / charge."""

from __future__ import annotations

import html
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from obdtool.core import pids
from obdtool.core.analysis import BatteryCheck, analyse_fuel_trims, analyse_lambda
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ObdError
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.widgets.tables import add_row, make_table

KEYS = ("RPM", "O2_B1S1", "O2_B1S2", "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1", "ADAPTER_VOLTAGE", "COOLANT_TEMP")


def capture_crank(link: ElmLink, seconds: float = 12.0) -> float | None:
	"""Relève la tension minimale pendant un démarrage (ATRV en boucle pendant `seconds`)."""
	minimum = None
	deadline = time.monotonic() + seconds
	while time.monotonic() < deadline:
		try:
			value = link.read_voltage()
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			continue
		if value is not None and value > 3:
			minimum = value if minimum is None else min(minimum, value)
	return minimum


def _bullets(lines: list[str]) -> str:
	return "<ul style='margin:0'>" + "".join(f"<li>{html.escape(line)}</li>" for line in lines) + "</ul>"


class AnalysisPage(BasePage):
	"""Analyses d'aide au diagnostic."""

	title = "Analyses"
	subtitle = (
		"Indications calculées à partir des mesures récentes (moteur chaud, boucle fermée). Elles orientent le "
		"diagnostic sans le remplacer."
	)

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.battery = BatteryCheck()

		columns = QHBoxLayout()
		left = QVBoxLayout()
		right = QVBoxLayout()
		columns.addLayout(left, 1)
		columns.addLayout(right, 1)
		self.content.addLayout(columns, 1)

		lam = QGroupBox("Sondes lambda et catalyseur (dernière minute)")
		lam_layout = QVBoxLayout(lam)
		button = QPushButton("Analyser les sondes")
		button.clicked.connect(self._lambda)
		lam_layout.addWidget(button)
		self.lambda_label = QLabel("Moteur chaud au ralenti ou à vitesse stabilisée, puis lancez l'analyse.")
		self.lambda_label.setWordWrap(True)
		self.lambda_label.setTextFormat(Qt.TextFormat.RichText)
		lam_layout.addWidget(self.lambda_label)
		left.addWidget(lam)

		trims = QGroupBox("Corrections de richesse par plage de régime (session)")
		trims_layout = QVBoxLayout(trims)
		button = QPushButton("Analyser les corrections")
		button.clicked.connect(self._trims)
		trims_layout.addWidget(button)
		self.trim_table = make_table(["Plage", "Court terme", "Long terme", "Total", "Mesures"], stretch=0)
		self.trim_table.setMaximumHeight(140)
		trims_layout.addWidget(self.trim_table)
		self.trim_label = QLabel("Roulez quelques minutes à différents régimes pour remplir les plages.")
		self.trim_label.setWordWrap(True)
		self.trim_label.setTextFormat(Qt.TextFormat.RichText)
		trims_layout.addWidget(self.trim_label)
		left.addWidget(trims)
		left.addStretch(1)

		battery = QGroupBox("Test batterie et charge (tension mesurée par l'adaptateur)")
		bat_layout = QVBoxLayout(battery)
		steps = QGridLayout()
		self._bat_labels: dict[str, QLabel] = {}
		for row, (key, label, hint) in enumerate(
			(
				("rest", "1. Repos", "Contact mis, moteur arrêté depuis quelques minutes."),
				("crank_min", "2. Démarrage", "Cliquez puis démarrez le moteur dans les 10 secondes."),
				("idle", "3. Ralenti", "Moteur au ralenti, consommateurs coupés."),
				("load", "4. Consommateurs", "Ralenti avec phares, dégivrage et ventilation à fond."),
			)
		):
			button = QPushButton(label)
			button.setToolTip(hint)
			button.clicked.connect(lambda _c=False, k=key: self._battery_step(k))
			value = QLabel("—")
			note = QLabel(hint)
			note.setObjectName("pageSubtitle")
			note.setWordWrap(True)
			steps.addWidget(button, row, 0)
			steps.addWidget(value, row, 1)
			steps.addWidget(note, row, 2)
			self._bat_labels[key] = value
		steps.setColumnStretch(2, 1)
		bat_layout.addLayout(steps)
		self.battery_label = QLabel()
		self.battery_label.setWordWrap(True)
		self.battery_label.setTextFormat(Qt.TextFormat.RichText)
		bat_layout.addWidget(self.battery_label)
		warning = QLabel(
			"La mesure de l'adaptateur (broche 16) inclut la chute de tension des câbles : comptez 0,1 à 0,3 V de "
			"moins qu'aux bornes de la batterie. Les clones ELM327 sont parfois imprécis."
		)
		warning.setWordWrap(True)
		warning.setObjectName("pageSubtitle")
		bat_layout.addWidget(warning)
		right.addWidget(battery)
		right.addStretch(1)

	# ------------------------------------------------------------ analyses
	def _lambda(self) -> None:
		result = analyse_lambda(self.acq.store)
		lines = []
		if result.upstream_hz is not None:
			lines.append(
				f"Sonde amont : {result.upstream_hz:.2f} cycle/s, de {result.upstream_min:.2f} à {result.upstream_max:.2f} V"
			)
		if result.downstream_hz is not None:
			lines.append(f"Sonde aval : {result.downstream_hz:.2f} cycle/s")
		if result.ratio is not None:
			lines.append(f"Rapport aval / amont : {result.ratio:.2f} (proche de 0 = catalyseur efficace)")
		self.lambda_label.setText(_bullets(lines + result.verdicts))

	def _trims(self) -> None:
		cells, hints = analyse_fuel_trims(self.acq.store)
		self.trim_table.setRowCount(0)
		for cell in cells:
			total = cell.total
			color = theme.COLOR_WARN if total is not None and abs(total) > 10 else None
			add_row(
				self.trim_table,
				[
					cell.label,
					"—" if cell.short_mean is None else f"{cell.short_mean:+.1f} %",
					"—" if cell.long_mean is None else f"{cell.long_mean:+.1f} %",
					"—" if total is None else f"{total:+.1f} %",
					cell.samples or "—",
				],
				color,
				3,
			)
		self.trim_label.setText(_bullets(hints))

	def _battery_step(self, key: str) -> None:
		if not self.ctx.service.link.is_open:
			self.battery_label.setText("Connectez-vous à l'adaptateur d'abord.")
			return
		label = self._bat_labels[key]
		label.setText("mesure…")

		def done(value: float | None) -> None:
			setattr(self.battery, key, value)
			label.setText("—" if value is None else f"{value:.2f} V")
			self.battery_label.setText(_bullets(self.battery.verdicts()))

		if key == "crank_min":
			job = capture_crank
		else:

			def job(link: ElmLink) -> float | None:
				values = [v for v in (link.read_voltage() for _ in range(5)) if v is not None]
				return sum(values) / len(values) if values else None

		self.ctx.bridge.run(
			self.ctx.service.submit(job, "test batterie", priority=1), done, lambda e: label.setText(e.user_message)
		)

	# --------------------------------------------------------- visibilité
	def page_shown(self) -> None:
		self.acq.require("analysis", [k for k in KEYS if pids.get(k)])

	def page_hidden(self) -> None:
		self.acq.require("analysis", [])
