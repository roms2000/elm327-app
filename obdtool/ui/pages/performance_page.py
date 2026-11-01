"""Page Performances : valeurs calculées en direct, chronomètres 0-50 / 0-100 / 80-120, historique."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
	QCheckBox,
	QFrame,
	QGridLayout,
	QGroupBox,
	QHBoxLayout,
	QLabel,
	QMessageBox,
	QPushButton,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import pids, units
from obdtool.core.perf import TESTS, PerfHistory, PerformanceTracker, RunState
from obdtool.core.samples import SampleBatch
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.profile_dialog import ProfileDialog
from obdtool.ui.widgets.tables import add_row, make_table

LIVE_KEYS = (
	"SPEED",
	"RPM",
	"GEAR_DERIVED",
	"POWER_DERIVED",
	"AIRFLOW_DERIVED",
	"FUEL_RATE_DERIVED",
	"FUEL_ECONOMY_DERIVED",
	"FUEL_AVG_DERIVED",
	"FUEL_USED_DERIVED",
	"DISTANCE_DERIVED",
)


class PerformancePage(BasePage):
	"""Valeurs calculées et chronomètres de performance."""

	title = "Performances et valeurs calculées"
	subtitle = (
		"Estimations à partir des mesures et du profil véhicule. Chronométrez uniquement sur circuit ou terrain privé, "
		"jamais sur la voie publique."
	)

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.tracker = PerformanceTracker()
		self.history = PerfHistory()

		bar = QHBoxLayout()
		self.profile_label = QLabel()
		bar.addWidget(self.profile_label, 1)
		edit = QPushButton("Profil véhicule…")
		edit.clicked.connect(self._edit_profile)
		bar.addWidget(edit)
		self.chrono_mode = QCheckBox("Mode chrono")
		self.chrono_mode.setToolTip(
			"N'interroger que la vitesse et le régime : chronos plus précis, autres écrans figés."
		)
		self.chrono_mode.toggled.connect(self._update_requirements)
		bar.addWidget(self.chrono_mode)
		self.content.addLayout(bar)

		values = QGroupBox("Valeurs calculées")
		grid = QGridLayout(values)
		self._labels: dict[str, QLabel] = {}
		for index, key in enumerate(LIVE_KEYS):
			definition = pids.CATALOG[key]
			name = QLabel(definition.name)
			name.setObjectName("pageSubtitle")
			value = QLabel("—")
			font = value.font()
			font.setPointSize(16)
			font.setBold(True)
			value.setFont(font)
			cell = QVBoxLayout()
			cell.addWidget(name)
			cell.addWidget(value)
			frame = QFrame()
			frame.setLayout(cell)
			grid.addWidget(frame, index // 5, index % 5)
			self._labels[key] = value
		note = QLabel(
			"Débit d'air sans débitmètre : méthode speed-density (MAP, température d'admission, régime, cylindrée, "
			"rendement volumétrique). Puissance : estimation à la roue d'après l'accélération, la masse et la résistance "
			"à l'avancement, sensible au vent, à la pente et à la fréquence de mesure de la vitesse (±15 % au mieux)."
		)
		note.setWordWrap(True)
		note.setObjectName("pageSubtitle")
		grid.addWidget(note, 2, 0, 1, 5)
		self.content.addWidget(values)

		timers = QGroupBox("Chronomètres")
		timers_layout = QHBoxLayout(timers)
		self._timer_labels: dict[str, tuple[QLabel, QLabel, QLabel]] = {}
		for test in TESTS:
			box = QVBoxLayout()
			title = QLabel(f"<b>{test.label}</b>")
			state = QLabel()
			state.setWordWrap(True)
			clock = QLabel("—")
			font = clock.font()
			font.setPointSize(26)
			font.setBold(True)
			clock.setFont(font)
			clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
			best = QLabel()
			best.setObjectName("pageSubtitle")
			for widget in (title, clock, state, best):
				box.addWidget(widget)
			timers_layout.addLayout(box, 1)
			self._timer_labels[test.key] = (clock, state, best)
		self.content.addWidget(timers)

		history_row = QHBoxLayout()
		history_row.addWidget(QLabel("<b>Historique</b>"))
		history_row.addStretch(1)
		clear = QPushButton("Effacer l'historique…")
		clear.clicked.connect(self._clear_history)
		history_row.addWidget(clear)
		self.content.addLayout(history_row)
		self.table = make_table(["Date", "Essai", "Temps", "Mesures de vitesse", "Profil", "Source"], stretch=4)
		self.content.addWidget(self.table, 1)

		self._timer = QTimer(self)
		self._timer.setInterval(100)
		self._timer.timeout.connect(self._refresh)
		acquisition.batch.connect(self._on_batch)
		self._update_profile_label()
		self._load_history()
		self._refresh()

	# ------------------------------------------------------------ données
	def _on_batch(self, batch: SampleBatch) -> None:
		for result in self.tracker.process(batch):
			result.profile = self.acq.profile.name
			self.history.add(result)
			self._load_history()
			label = next(t.label for t in TESTS if t.key == result.test)
			self.ctx.notify(f"{label} : {result.seconds:.2f} s", 10000)

	def _refresh(self) -> None:
		system = self.ctx.settings.general.units
		for key, label in self._labels.items():
			stats = self.acq.stats.get(key)
			definition = pids.CATALOG[key]
			value = stats.last if stats else None
			if key == "GEAR_DERIVED":
				label.setText(
					"N / débrayé" if value is None and stats else (f"{value:.0f}e" if isinstance(value, float) else "—")
				)
				continue
			shown = units.convert(value, definition.unit, system)
			unit = units.display_unit(definition.unit, system)
			label.setText(f"{pids.format_value(shown, definition.unit)} {unit}".strip() if value is not None else "—")
		for test in TESTS:
			clock, state_label, best_label = self._timer_labels[test.key]
			state, elapsed = self.tracker.state(test.key)
			color = {RunState.RUNNING: theme.COLOR_INFO, RunState.ARMED: theme.COLOR_OK}.get(state, theme.COLOR_MUTED)
			state_label.setText(f"<span style='color:{color}'>{state.value}</span>")
			if state == RunState.RUNNING or elapsed:
				clock.setText(f"{elapsed:.2f} s")
			best = self.history.best(test.key)
			best_label.setText(f"Meilleur : {best.seconds:.2f} s" if best else "Pas encore de résultat")

	def _load_history(self) -> None:
		self.table.setRowCount(0)
		labels = {t.key: t.label for t in TESTS}
		for result in sorted(self.history.load(), key=lambda r: r.timestamp, reverse=True):
			add_row(
				self.table,
				[
					datetime.fromtimestamp(result.timestamp).strftime("%d/%m/%Y %H:%M"),
					labels.get(result.test, result.test),
					f"{result.seconds:.2f} s",
					f"{result.sample_rate_hz:.1f} /s" if result.sample_rate_hz else "—",
					result.profile,
					"relecture" if result.source == "replay" else "direct",
				],
			)

	def _clear_history(self) -> None:
		if (
			QMessageBox.question(self, "Performances", "Effacer tout l'historique des chronos ?")
			== QMessageBox.StandardButton.Yes
		):
			self.history.clear()
			self._load_history()

	# ------------------------------------------------------------- profil
	def _update_profile_label(self) -> None:
		p = self.acq.profile
		self.profile_label.setText(
			f"Profil : <b>{p.name}</b> — {p.displacement_cc:.0f} cm³, {p.mass_kg:.0f} kg, pneus {p.tyre}, "
			f"rendement volumétrique {p.volumetric_efficiency:.2f}"
		)

	def _edit_profile(self) -> None:
		dialog = ProfileDialog(self.acq.profile, self)
		if dialog.exec():
			self.acq.set_profile(dialog.selected)
			self.ctx.save_settings()
			self._update_profile_label()

	# --------------------------------------------------------- exigences
	def _update_requirements(self, *_args: object) -> None:
		if not self.isVisible():
			self.acq.require("performance", [])
			self.acq.exclusive = None
			return
		if self.chrono_mode.isChecked():
			self.acq.exclusive = ["SPEED", "RPM"]
		else:
			self.acq.exclusive = None
		self.acq.require("performance", list(LIVE_KEYS))

	def page_shown(self) -> None:
		self._timer.start()
		self._update_requirements()

	def page_hidden(self) -> None:
		self._timer.stop()
		self.acq.exclusive = None
		self.acq.require("performance", [])
