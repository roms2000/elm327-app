"""Page Graphiques : courbes temps réel empilées ou superposées, curseur, zoom, export PNG/CSV."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
	QComboBox,
	QFileDialog,
	QHBoxLayout,
	QLabel,
	QListWidget,
	QListWidgetItem,
	QMessageBox,
	QPushButton,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import pids, units
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage

log = logging.getLogger(__name__)

MAX_CURVES = 8
WINDOWS_S = (10, 30, 60, 120, 300, 600, 1800)
DEFAULT_CURVES = ["RPM", "SPEED", "INTAKE_PRESSURE"]


def _swatch(color: str) -> QIcon:
	pixmap = QPixmap(14, 14)
	pixmap.fill(QColor(color))
	return QIcon(pixmap)


class ChartsPage(BasePage):
	"""Graphiques temps réel (et relecture)."""

	title = "Graphiques"
	subtitle = "Cliquez dans un graphique pour placer le curseur ; molette et glisser pour zoomer après « Pause »."

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		config = ctx.settings.extra.get("charts", {}) if isinstance(ctx.settings.extra.get("charts"), dict) else {}
		self.curves: list[str] = [k for k in config.get("curves", DEFAULT_CURVES) if pids.get(k)][:MAX_CURVES]
		self.paused = False
		self.cursor_t: float | None = None
		self._plots: list[pg.PlotItem] = []
		self._items: dict[str, pg.PlotDataItem] = {}
		self._views: list[pg.ViewBox] = []
		self._cursors: list[pg.InfiniteLine] = []

		toolbar = QHBoxLayout()
		self.add_combo = QComboBox()
		self.add_combo.setMinimumWidth(200)
		self.add_combo.setMaximumWidth(320)
		toolbar.addWidget(self.add_combo)
		add = QPushButton("Ajouter")
		add.clicked.connect(self._add_curve)
		toolbar.addWidget(add)
		toolbar.addSpacing(16)
		toolbar.addWidget(QLabel("Disposition :"))
		self.mode = QComboBox()
		self.mode.addItem("Empilées", "stacked")
		self.mode.addItem("Superposées", "overlay")
		self.mode.setToolTip(
			"Empilées : un graphique par courbe. Superposées : un seul graphique, un axe Y par courbe."
		)
		self.mode.setCurrentIndex(max(self.mode.findData(config.get("mode", "stacked")), 0))
		self.mode.currentIndexChanged.connect(self._rebuild)
		toolbar.addWidget(self.mode)
		toolbar.addWidget(QLabel("Fenêtre :"))
		self.window = QComboBox()
		for seconds in WINDOWS_S:
			self.window.addItem(f"{seconds} s" if seconds < 60 else f"{seconds // 60} min", seconds)
		self.window.setCurrentIndex(max(self.window.findData(config.get("window", 60)), 0))
		self.window.currentIndexChanged.connect(self._save_config)
		toolbar.addWidget(self.window)
		self.pause_btn = QPushButton("⏸ Pause")
		self.pause_btn.setCheckable(True)
		self.pause_btn.toggled.connect(self._toggle_pause)
		toolbar.addWidget(self.pause_btn)
		toolbar.addStretch(1)
		png = QPushButton("PNG…")
		png.setToolTip("Exporter l'image des graphiques")
		png.clicked.connect(self._export_png)
		csv = QPushButton("CSV…")
		csv.setToolTip("Exporter les données de la plage affichée")
		csv.clicked.connect(self._export_csv)
		clear = QPushButton("Effacer")
		clear.setToolTip("Effacer les données mémorisées de la session")
		clear.clicked.connect(self._clear)
		for button in (png, csv, clear):
			toolbar.addWidget(button)
		self.content.addLayout(toolbar)

		body = QHBoxLayout()
		side = QVBoxLayout()
		self.curve_list = QListWidget()
		self.curve_list.setMaximumWidth(260)
		side.addWidget(QLabel("Courbes :"))
		side.addWidget(self.curve_list, 1)
		row = QHBoxLayout()
		for label, slot in (
			("▲", lambda: self._move(-1)),
			("▼", lambda: self._move(1)),
			("Retirer", self._remove_curve),
		):
			button = QPushButton(label)
			button.clicked.connect(slot)
			row.addWidget(button)
		side.addLayout(row)
		self.cursor_label = QLabel("Curseur : cliquez dans un graphique.")
		self.cursor_label.setWordWrap(True)
		self.cursor_label.setTextFormat(Qt.TextFormat.RichText)
		self.cursor_label.setMaximumWidth(260)
		side.addWidget(self.cursor_label)
		body.addLayout(side)
		self.graphics = pg.GraphicsLayoutWidget()
		body.addWidget(self.graphics, 1)
		self.content.addLayout(body, 1)

		self._timer = QTimer(self)
		self._timer.setInterval(50)
		self._timer.timeout.connect(self._refresh)
		acquisition.supported_changed.connect(self._fill_add_combo)
		ctx.theme_changed.connect(lambda _name: self._rebuild())
		ctx.settings_changed.connect(self._units_maybe_changed)
		self._units = ctx.settings.general.units
		self._fill_add_combo()
		self._rebuild()

	# ---------------------------------------------------------- courbes
	def _fill_add_combo(self) -> None:
		self.add_combo.clear()
		for definition in sorted(pids.CATALOG.values(), key=lambda d: (pids.CATEGORIES.index(d.category), d.name)):
			if definition.numeric and self.acq.is_available(definition.key):
				self.add_combo.addItem(f"{definition.name} ({definition.unit})", definition.key)

	def _add_curve(self) -> None:
		key = self.add_combo.currentData()
		if not key or key in self.curves:
			return
		if len(self.curves) >= MAX_CURVES:
			QMessageBox.information(self, "Graphiques", f"Au plus {MAX_CURVES} courbes : retirez-en une d'abord.")
			return
		self.curves.append(str(key))
		self._rebuild()

	def _remove_curve(self) -> None:
		row = self.curve_list.currentRow()
		if 0 <= row < len(self.curves):
			del self.curves[row]
			self._rebuild()

	def _move(self, delta: int) -> None:
		row = self.curve_list.currentRow()
		target = row + delta
		if 0 <= row < len(self.curves) and 0 <= target < len(self.curves):
			self.curves[row], self.curves[target] = self.curves[target], self.curves[row]
			self._rebuild()
			self.curve_list.setCurrentRow(target)

	def _save_config(self, *_args: object) -> None:
		self.ctx.settings.extra["charts"] = {
			"curves": list(self.curves),
			"mode": self.mode.currentData(),
			"window": self.window.currentData(),
		}
		self.ctx.save_settings()

	def _units_maybe_changed(self) -> None:
		if self.ctx.settings.general.units != self._units:
			self._units = self.ctx.settings.general.units
			self._rebuild()

	def _label(self, key: str) -> str:
		definition = pids.get(key)
		if definition is None:
			return key
		unit = units.display_unit(definition.unit, self.ctx.settings.general.units)
		return f"{definition.name} ({unit})" if unit else definition.name

	# ----------------------------------------------------------- graphiques
	def _rebuild(self, *_args: object) -> None:
		"""Reconstruit les graphiques (courbes, disposition ou thème modifiés)."""
		self._update_requirements()
		self._save_config()
		self.curve_list.clear()
		for index, key in enumerate(self.curves):
			item = QListWidgetItem(_swatch(theme.series_color(index)), self._label(key))
			item.setToolTip(key)
			self.curve_list.addItem(item)
		self.graphics.clear()
		self._plots.clear()
		self._items.clear()
		self._views.clear()
		self._cursors.clear()
		self.graphics.setBackground(pg.getConfigOption("background"))
		if not self.curves:
			self.graphics.addLabel("Ajoutez une courbe avec la liste ci-dessus.", color=theme.text_color())
			return
		if self.mode.currentData() == "overlay":
			self._build_overlay()
		else:
			self._build_stacked()
		for plot in self._plots:
			plot.scene().sigMouseClicked.connect(self._scene_clicked)
			break
		self._apply_pause_state()

	def _new_plot(self, row: int, col: int, date_axis: bool) -> pg.PlotItem:
		axis = {"bottom": pg.DateAxisItem(orientation="bottom")} if date_axis else {}
		plot = self.graphics.addPlot(row=row, col=col, axisItems=axis)
		plot.showGrid(x=True, y=True, alpha=theme.grid_alpha())
		plot.setClipToView(True)
		plot.setDownsampling(auto=True, mode="peak")
		for name in ("left", "bottom", "right"):
			ax = plot.getAxis(name)
			ax.setTextPen(pg.mkPen(theme.text_color()))
		cursor = pg.InfiniteLine(
			angle=90, movable=True, pen=pg.mkPen(theme.text_color(), width=1, style=Qt.PenStyle.DashLine)
		)
		cursor.setVisible(False)
		cursor.sigPositionChanged.connect(self._cursor_moved)
		plot.addItem(cursor, ignoreBounds=True)
		self._cursors.append(cursor)
		self._plots.append(plot)
		return plot

	def _build_stacked(self) -> None:
		first: pg.PlotItem | None = None
		for index, key in enumerate(self.curves):
			last = index == len(self.curves) - 1
			plot = self._new_plot(index, 0, date_axis=True)
			plot.getAxis("bottom").setStyle(showValues=last)
			plot.setLabel("left", self._label(key), color=theme.text_color())
			if first is None:
				first = plot
			else:
				plot.setXLink(first)
			color = theme.series_color(index)
			self._items[key] = plot.plot(pen=pg.mkPen(color, width=2), name=self._label(key))
			self._views.append(plot.vb)

	def _build_overlay(self) -> None:
		plot = self._new_plot(0, len(self.curves), date_axis=True)
		legend = plot.addLegend(offset=(10, 10), labelTextColor=theme.text_color())
		for index, key in enumerate(self.curves):
			color = theme.series_color(index)
			pen = pg.mkPen(color, width=2)
			if index == 0:
				view = plot.vb
				axis = plot.getAxis("left")
				item = plot.plot(pen=pen)
			else:
				view = pg.ViewBox()
				axis = pg.AxisItem("left")
				self.graphics.addItem(axis, row=0, col=len(self.curves) - index)
				plot.scene().addItem(view)
				axis.linkToView(view)
				view.setXLink(plot)
				item = pg.PlotDataItem(pen=pen)
				view.addItem(item)
				view.enableAutoRange(axis=pg.ViewBox.YAxis)
			axis.setPen(pg.mkPen(color, width=2))
			axis.setTextPen(pg.mkPen(theme.text_color()))
			axis.setLabel(self._label(key), color=theme.text_color())
			legend.addItem(item, self._label(key))
			self._items[key] = item
			self._views.append(view)

		def sync() -> None:
			for view in self._views[1:]:
				view.setGeometry(plot.vb.sceneBoundingRect())
				view.linkedViewChanged(plot.vb, view.XAxis)

		plot.vb.sigResized.connect(sync)
		sync()

	# ------------------------------------------------------------- données
	def _latest_time(self) -> float | None:
		times = [s.last_time for k in self.curves if (s := self.acq.store.get(k)) is not None and s.last_time]
		return max(times) if times else None

	def _refresh(self) -> None:
		if self.paused or not self._plots:
			return
		t1 = self._latest_time()
		if t1 is None:
			return
		span = float(self.window.currentData())
		t0 = t1 - span
		self._draw(t0, t1)
		for plot in self._plots:
			plot.setXRange(t0, t1, padding=0)
			plot.enableAutoRange(axis=pg.ViewBox.YAxis)

	def _draw(self, t0: float, t1: float) -> None:
		system = self.ctx.settings.general.units
		for key, item in self._items.items():
			series = self.acq.store.get(key)
			if series is None:
				item.setData([], [])
				continue
			t, v = series.window(t0, t1)
			definition = pids.get(key)
			if definition and system != "metric" and v.size:
				v = np.array([units.convert(float(x), definition.unit, system) for x in v], dtype=float)
			item.setData(t, v)
		if self.cursor_t is not None:
			self._update_cursor_label()

	def _toggle_pause(self, paused: bool) -> None:
		self.paused = paused
		self._apply_pause_state()

	def _apply_pause_state(self) -> None:
		self.pause_btn.setText("▶ Reprendre" if self.paused else "⏸ Pause")
		for plot in self._plots:
			plot.setMouseEnabled(x=self.paused, y=self.paused)
		if self.paused:
			# Afficher toutes les données mémorisées : on peut alors défiler et zoomer dans l'historique.
			bounds = self.acq.store.time_range()
			if bounds:
				self._draw(bounds[0], bounds[1])

	# ------------------------------------------------------------- curseur
	def _scene_clicked(self, event: object) -> None:
		if not self._plots:
			return
		pos = event.scenePos()  # type: ignore[attr-defined]
		for plot in self._plots:
			if plot.sceneBoundingRect().contains(pos):
				t = plot.vb.mapSceneToView(pos).x()
				self._set_cursor(t)
				return

	def _set_cursor(self, t: float) -> None:
		self.cursor_t = t
		for cursor in self._cursors:
			cursor.blockSignals(True)
			cursor.setValue(t)
			cursor.setVisible(True)
			cursor.blockSignals(False)
		self._update_cursor_label()

	def _cursor_moved(self, line: pg.InfiniteLine) -> None:
		self._set_cursor(float(line.value()))

	def _update_cursor_label(self) -> None:
		if self.cursor_t is None:
			return
		system = self.ctx.settings.general.units
		lines = [f"<b>{datetime.fromtimestamp(self.cursor_t):%H:%M:%S.%f}"[:-3] + "</b>"]
		for index, key in enumerate(self.curves):
			series = self.acq.store.get(key)
			value = series.value_at(self.cursor_t) if series else None
			definition = pids.get(key)
			unit = definition.unit if definition else ""
			shown = pids.format_value(units.convert(value, unit, system), unit)
			color = theme.series_color(index)
			lines.append(f"<span style='color:{color}'>■</span> {html.escape(self._label(key))} : <b>{shown}</b>")
		self.cursor_label.setText("<br>".join(lines))

	# --------------------------------------------------------------- export
	def _visible_range(self) -> tuple[float, float] | None:
		if not self._plots:
			return None
		(t0, t1), _ = self._plots[0].vb.viewRange()
		return float(t0), float(t1)

	def _export_png(self) -> None:
		default = Path.home() / f"graphiques_{datetime.now():%Y%m%d_%H%M%S}.png"
		path, _ = QFileDialog.getSaveFileName(self, "Exporter en PNG", str(default), "Image PNG (*.png)")
		if path:
			if not self.graphics.grab().save(path, "PNG"):
				QMessageBox.warning(self, "Export", f"Impossible d'enregistrer {path}")
			else:
				self.ctx.notify(f"Image enregistrée : {path}")

	def _export_csv(self) -> None:
		visible = self._visible_range()
		if visible is None or not self.curves:
			return
		default = Path.home() / f"graphiques_{datetime.now():%Y%m%d_%H%M%S}.csv"
		path, _ = QFileDialog.getSaveFileName(self, "Exporter en CSV", str(default), "CSV (*.csv)")
		if not path:
			return
		try:
			rows = self.acq.store.export_csv(Path(path), self.curves, visible[0], visible[1])
		except OSError as exc:
			QMessageBox.warning(self, "Export", str(exc))
			return
		self.ctx.notify(f"{rows} ligne(s) exportée(s) : {path}")

	def _clear(self) -> None:
		if QMessageBox.question(self, "Graphiques", "Effacer toutes les données mémorisées de la session ?") == (
			QMessageBox.StandardButton.Yes
		):
			self.acq.clear_data()
			for item in self._items.values():
				item.setData([], [])

	# ---------------------------------------------------------- visibilité
	def _update_requirements(self) -> None:
		# Les courbes ne sont interrogées que lorsque l'écran est affiché (meilleure fréquence ailleurs).
		self.acq.require("charts", self.curves if self.isVisible() else [])

	def page_shown(self) -> None:
		self._timer.start()
		self.acq.require("charts", self.curves)

	def page_hidden(self) -> None:
		self._timer.stop()
		self.acq.require("charts", [])
