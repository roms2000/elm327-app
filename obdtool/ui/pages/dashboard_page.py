"""Page Tableau de bord : jauges configurables, mises en page nommées, plein écran."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
	QComboBox,
	QDialog,
	QDialogButtonBox,
	QDoubleSpinBox,
	QFormLayout,
	QGridLayout,
	QHBoxLayout,
	QHeaderView,
	QInputDialog,
	QLabel,
	QLineEdit,
	QMenu,
	QMessageBox,
	QPushButton,
	QSpinBox,
	QTableWidget,
	QTableWidgetItem,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import pids
from obdtool.core.dashboard import (
	GAUGE_KINDS,
	ZONE_COLORS,
	DashboardLayout,
	GaugeConfig,
	Zone,
	dump_layouts,
	load_layouts,
)
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.widgets.gauges import GaugeWidget

log = logging.getLogger(__name__)


class GaugeDialog(QDialog):
	"""Édition d'une jauge : valeur, type, bornes, zones colorées."""

	def __init__(self, config: GaugeConfig, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.setWindowTitle("Jauge")
		layout = QVBoxLayout(self)
		form = QFormLayout()
		self.key = QComboBox()
		for definition in sorted(pids.CATALOG.values(), key=lambda d: (pids.CATEGORIES.index(d.category), d.name)):
			suffix = "" if acquisition.is_available(definition.key) else " — non supporté"
			self.key.addItem(f"{definition.category} › {definition.name}{suffix}", definition.key)
		self.key.setCurrentIndex(max(self.key.findData(config.key), 0))
		self.key.currentIndexChanged.connect(self._key_changed)
		form.addRow("Valeur :", self.key)
		self.kind = QComboBox()
		for key, label in GAUGE_KINDS.items():
			self.kind.addItem(label, key)
		self.kind.setCurrentIndex(max(self.kind.findData(config.kind), 0))
		form.addRow("Type :", self.kind)
		self.label = QLineEdit(config.label)
		self.label.setPlaceholderText("Nom du PID par défaut")
		form.addRow("Titre :", self.label)
		low, high = config.bounds()
		self.vmin = QDoubleSpinBox()
		self.vmax = QDoubleSpinBox()
		for spin, value in ((self.vmin, low), (self.vmax, high)):
			spin.setRange(-100000, 100000)
			spin.setDecimals(2)
			spin.setValue(value)
		form.addRow("Minimum (unité métrique) :", self.vmin)
		form.addRow("Maximum (unité métrique) :", self.vmax)
		layout.addLayout(form)
		layout.addWidget(QLabel("Zones colorées (bornes en unité métrique) :"))
		self.zones = QTableWidget(0, 3)
		self.zones.setHorizontalHeaderLabels(["De", "À", "Couleur"])
		self.zones.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
		self.zones.verticalHeader().hide()
		for zone in config.zones:
			self._add_zone(zone)
		layout.addWidget(self.zones)
		row = QHBoxLayout()
		add = QPushButton("Ajouter une zone")
		add.clicked.connect(lambda: self._add_zone(Zone(self.vmax.value() * 0.8, self.vmax.value(), "rouge")))
		remove = QPushButton("Retirer la zone")
		remove.clicked.connect(lambda: self.zones.removeRow(self.zones.currentRow()))
		row.addWidget(add)
		row.addWidget(remove)
		row.addStretch(1)
		layout.addLayout(row)
		buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
		buttons.accepted.connect(self.accept)
		buttons.rejected.connect(self.reject)
		layout.addWidget(buttons)
		self.resize(520, 480)

	def _add_zone(self, zone: Zone) -> None:
		row = self.zones.rowCount()
		self.zones.insertRow(row)
		self.zones.setItem(row, 0, QTableWidgetItem(f"{zone.start:g}"))
		self.zones.setItem(row, 1, QTableWidgetItem(f"{zone.end:g}"))
		combo = QComboBox()
		combo.addItems(list(ZONE_COLORS))
		combo.setCurrentText(zone.color if zone.color in ZONE_COLORS else "rouge")
		self.zones.setCellWidget(row, 2, combo)

	def _key_changed(self) -> None:
		definition = pids.get(str(self.key.currentData()))
		if definition:
			self.vmin.setValue(definition.vmin)
			self.vmax.setValue(definition.vmax)
			if not definition.numeric:
				self.kind.setCurrentIndex(self.kind.findData("value"))

	def config(self) -> GaugeConfig:
		zones = []
		for row in range(self.zones.rowCount()):
			try:
				start = float(self.zones.item(row, 0).text().replace(",", "."))
				end = float(self.zones.item(row, 1).text().replace(",", "."))
			except (AttributeError, ValueError):
				continue
			combo = self.zones.cellWidget(row, 2)
			zones.append(Zone(min(start, end), max(start, end), combo.currentText() if combo else "rouge"))
		return GaugeConfig(
			key=str(self.key.currentData()),
			kind=str(self.kind.currentData()),
			vmin=self.vmin.value(),
			vmax=self.vmax.value(),
			zones=zones,
			label=self.label.text().strip(),
		)


class DashboardView(QWidget):
	"""Grille de jauges d'une mise en page."""

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.layout_config: DashboardLayout | None = None
		self.gauges: list[GaugeWidget] = []
		self.grid = QGridLayout(self)
		self.grid.setSpacing(10)
		self.on_edit = None
		self.on_context = None

	def set_layout_config(self, layout: DashboardLayout) -> None:
		self.layout_config = layout
		for gauge in self.gauges:
			gauge.setParent(None)
			gauge.deleteLater()
		self.gauges.clear()
		for index, config in enumerate(layout.gauges):
			gauge = GaugeWidget(config, self.ctx.settings.general.units, self)
			gauge.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
			gauge.edit_requested.connect(lambda i=index: self.on_edit and self.on_edit(i))
			gauge.customContextMenuRequested.connect(
				lambda pos, i=index, g=gauge: self.on_context and self.on_context(i, g, pos)
			)
			self.grid.addWidget(gauge, index // layout.columns, index % layout.columns)
			self.gauges.append(gauge)
		self.refresh()

	def refresh(self) -> None:
		for gauge in self.gauges:
			gauge.unit_system = self.ctx.settings.general.units
			stats = self.acq.stats.get(gauge.config.key)
			if stats is None:
				error = "" if self.acq.is_available(gauge.config.key) else "non supporté"
				gauge.set_value(None, error)
			else:
				gauge.set_value(stats.last, stats.error)


class FullscreenDashboard(QWidget):
	"""Tableau de bord en plein écran (Échap ou F11 pour quitter)."""

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, layout: DashboardLayout, on_close) -> None:
		super().__init__()
		self.setWindowTitle(f"Tableau de bord — {layout.name}")
		self._on_close = on_close
		box = QVBoxLayout(self)
		hint = QLabel("Échap pour quitter le plein écran — ne manipulez pas l'application en conduisant.")
		hint.setObjectName("pageSubtitle")
		hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self.view = DashboardView(ctx, acquisition, self)
		self.view.set_layout_config(layout)
		box.addWidget(self.view, 1)
		box.addWidget(hint)
		self._timer = QTimer(self)
		self._timer.setInterval(100)
		self._timer.timeout.connect(self.view.refresh)
		self._timer.start()

	def keyPressEvent(self, event: QKeyEvent) -> None:
		if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F11):
			self.close()
			return
		super().keyPressEvent(event)

	def closeEvent(self, event: object) -> None:
		self._timer.stop()
		self._on_close()
		super().closeEvent(event)  # type: ignore[arg-type]


class DashboardPage(BasePage):
	"""Tableau de bord configurable."""

	title = "Tableau de bord"
	subtitle = "Double-cliquez sur une jauge pour la modifier ; clic droit pour la déplacer ou la supprimer."

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.layouts = load_layouts(ctx.settings.extra.get("dashboards"))
		self.fullscreen: FullscreenDashboard | None = None
		self._visible = False

		bar = QHBoxLayout()
		bar.addWidget(QLabel("Mise en page :"))
		self.selector = QComboBox()
		self.selector.setMinimumWidth(200)
		self.selector.currentIndexChanged.connect(self._show_selected)
		bar.addWidget(self.selector)
		for label, slot in (
			("Nouvelle…", self._new_layout),
			("Dupliquer", self._duplicate),
			("Renommer…", self._rename),
			("Supprimer", self._delete_layout),
		):
			button = QPushButton(label)
			button.clicked.connect(slot)
			bar.addWidget(button)
		bar.addWidget(QLabel("Colonnes :"))
		self.columns = QSpinBox()
		self.columns.setRange(1, 6)
		self.columns.valueChanged.connect(self._columns_changed)
		bar.addWidget(self.columns)
		add = QPushButton("Ajouter une jauge…")
		add.clicked.connect(self._add_gauge)
		bar.addWidget(add)
		bar.addStretch(1)
		full = QPushButton("Plein écran")
		full.clicked.connect(self.show_fullscreen)
		bar.addWidget(full)
		self.content.addLayout(bar)

		self.view = DashboardView(ctx, acquisition, self)
		self.view.on_edit = self._edit_gauge
		self.view.on_context = self._context_menu
		self.content.addWidget(self.view, 1)

		self._timer = QTimer(self)
		self._timer.setInterval(100)
		self._timer.timeout.connect(self.view.refresh)
		action = QAction(self)
		action.setShortcut(QKeySequence("Ctrl+Shift+F"))
		action.triggered.connect(self.show_fullscreen)
		self.addAction(action)
		ctx.theme_changed.connect(lambda _name: self.view.update())
		self._fill_selector(str(ctx.settings.extra.get("dashboard_current", "")))

	# ------------------------------------------------------- mises en page
	def current(self) -> DashboardLayout:
		index = max(self.selector.currentIndex(), 0)
		return self.layouts[min(index, len(self.layouts) - 1)]

	def _fill_selector(self, select: str = "") -> None:
		self.selector.blockSignals(True)
		self.selector.clear()
		for layout in self.layouts:
			self.selector.addItem(layout.name)
		index = self.selector.findText(select)
		self.selector.setCurrentIndex(index if index >= 0 else 0)
		self.selector.blockSignals(False)
		self._show_selected()

	def _show_selected(self) -> None:
		layout = self.current()
		self.columns.blockSignals(True)
		self.columns.setValue(layout.columns)
		self.columns.blockSignals(False)
		self.view.set_layout_config(layout)
		self.ctx.settings.extra["dashboard_current"] = layout.name
		self._update_requirements()

	def _save(self) -> None:
		self.ctx.settings.extra["dashboards"] = dump_layouts(self.layouts)
		self.ctx.settings.extra["dashboard_current"] = self.current().name
		self.ctx.save_settings()
		self.view.set_layout_config(self.current())
		self._update_requirements()

	def _ask_name(self, title: str, default: str) -> str | None:
		name, ok = QInputDialog.getText(self, title, "Nom de la mise en page :", text=default)
		name = name.strip()
		if not ok or not name:
			return None
		if any(layout.name == name for layout in self.layouts):
			QMessageBox.warning(self, title, f"Une mise en page « {name} » existe déjà.")
			return None
		return name

	def _new_layout(self) -> None:
		name = self._ask_name("Nouvelle mise en page", "Ma mise en page")
		if name:
			self.layouts.append(DashboardLayout(name, 3, [GaugeConfig("RPM"), GaugeConfig("SPEED")]))
			self._save()
			self._fill_selector(name)

	def _duplicate(self) -> None:
		source = self.current()
		name = self._ask_name("Dupliquer", f"{source.name} (copie)")
		if name:
			copy = DashboardLayout.from_dict(source.to_dict())
			copy.name = name
			self.layouts.append(copy)
			self._save()
			self._fill_selector(name)

	def _rename(self) -> None:
		layout = self.current()
		name = self._ask_name("Renommer", layout.name)
		if name:
			layout.name = name
			self._save()
			self._fill_selector(name)

	def _delete_layout(self) -> None:
		if len(self.layouts) <= 1:
			QMessageBox.information(self, "Tableau de bord", "Il faut conserver au moins une mise en page.")
			return
		layout = self.current()
		if QMessageBox.question(self, "Supprimer", f"Supprimer la mise en page « {layout.name} » ?") == (
			QMessageBox.StandardButton.Yes
		):
			self.layouts.remove(layout)
			self._save()
			self._fill_selector()

	def _columns_changed(self, value: int) -> None:
		self.current().columns = value
		self._save()

	# --------------------------------------------------------------- jauges
	def _add_gauge(self) -> None:
		dialog = GaugeDialog(GaugeConfig("SPEED"), self.acq, self)
		if dialog.exec() == QDialog.DialogCode.Accepted:
			self.current().gauges.append(dialog.config())
			self._save()

	def _edit_gauge(self, index: int) -> None:
		layout = self.current()
		if not 0 <= index < len(layout.gauges):
			return
		dialog = GaugeDialog(layout.gauges[index], self.acq, self)
		if dialog.exec() == QDialog.DialogCode.Accepted:
			layout.gauges[index] = dialog.config()
			self._save()

	def _context_menu(self, index: int, gauge: GaugeWidget, pos: object) -> None:
		layout = self.current()
		menu = QMenu(self)
		menu.addAction("Modifier…", lambda: self._edit_gauge(index))
		if index > 0:
			menu.addAction("Déplacer avant", lambda: self._move(index, -1))
		if index < len(layout.gauges) - 1:
			menu.addAction("Déplacer après", lambda: self._move(index, 1))
		menu.addSeparator()
		menu.addAction("Supprimer la jauge", lambda: self._remove(index))
		menu.exec(gauge.mapToGlobal(pos))  # type: ignore[arg-type]

	def _move(self, index: int, delta: int) -> None:
		gauges = self.current().gauges
		gauges[index], gauges[index + delta] = gauges[index + delta], gauges[index]
		self._save()

	def _remove(self, index: int) -> None:
		del self.current().gauges[index]
		self._save()

	# ------------------------------------------------------------ affichage
	def show_fullscreen(self) -> None:
		if self.fullscreen is not None:
			self.fullscreen.raise_()
			return
		self.fullscreen = FullscreenDashboard(self.ctx, self.acq, self.current(), self._fullscreen_closed)
		self.fullscreen.showFullScreen()
		self._update_requirements()

	def _fullscreen_closed(self) -> None:
		self.fullscreen = None
		self._update_requirements()

	def _update_requirements(self) -> None:
		active = self._visible or self.fullscreen is not None
		self.acq.require("dashboard", self.current().keys() if active else [])

	def page_shown(self) -> None:
		self._visible = True
		self._update_requirements()
		self._timer.start()

	def page_hidden(self) -> None:
		self._visible = False
		self._update_requirements()
		self._timer.stop()
