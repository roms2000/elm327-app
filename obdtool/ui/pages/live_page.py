"""Page Valeurs : PID supportés par catégorie, sélection et tableau des valeurs en direct."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
	QCheckBox,
	QHBoxLayout,
	QHeaderView,
	QLabel,
	QLineEdit,
	QPushButton,
	QSpinBox,
	QSplitter,
	QTableView,
	QTreeWidget,
	QTreeWidgetItem,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import pids, units
from obdtool.core.samples import SampleBatch
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage

_COLUMNS = ("Nom", "Valeur", "Unité", "Min", "Max", "Moyenne", "PID", "État")


class LiveTableModel(QAbstractTableModel):
	"""Modèle du tableau des valeurs en direct."""

	def __init__(self, acquisition: AcquisitionController, ctx: AppContext) -> None:
		super().__init__()
		self.acq = acquisition
		self.ctx = ctx
		self.keys: list[str] = []

	def set_keys(self, keys: list[str]) -> None:
		self.beginResetModel()
		self.keys = list(keys)
		self.endResetModel()

	def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
		return 0 if parent.isValid() else len(self.keys)

	def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
		return 0 if parent.isValid() else len(_COLUMNS)

	def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> object:
		if role != Qt.ItemDataRole.DisplayRole:
			return None
		if orientation == Qt.Orientation.Horizontal:
			return _COLUMNS[section]
		return None

	def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
		if not index.isValid():
			return None
		key = self.keys[index.row()]
		definition = pids.get(key)
		stats = self.acq.stats.get(key)
		system = self.ctx.settings.general.units
		unit = definition.unit if definition else ""
		column = index.column()
		if role == Qt.ItemDataRole.DisplayRole:
			if column == 0:
				return definition.name if definition else key
			if column == 1:
				if stats is None:
					return "…"
				return pids.format_value(units.convert(stats.last, unit, system), unit)
			if column == 2:
				return units.display_unit(unit, system)
			if column in (3, 4, 5):
				if stats is None or not stats.count:
					return "—"
				raw = (stats.minimum, stats.maximum, stats.mean)[column - 3]
				return pids.format_value(units.convert(raw, unit, system), unit)
			if column == 6:
				return definition.request[2:] if definition and definition.request else "—"
			if column == 7:
				if not self.acq.is_available(key):
					return "non supporté"
				return stats.error if stats and stats.error else ("OK" if stats else "en attente")
		if role == Qt.ItemDataRole.TextAlignmentRole and column in (1, 3, 4, 5):
			return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
		if (
			role == Qt.ItemDataRole.ForegroundRole
			and column == 7
			and ((stats and stats.error) or not self.acq.is_available(key))
		):
			return QBrush(QColor(theme.COLOR_WARN))
		if role == Qt.ItemDataRole.ToolTipRole:
			if column == 1 and stats is not None:
				return pids.format_value(units.convert(stats.last, unit, system), unit)
			return f"{definition.name if definition else key} — clé {key}"
		return None

	def refresh(self) -> None:
		if self.keys:
			self.dataChanged.emit(self.index(0, 0), self.index(len(self.keys) - 1, len(_COLUMNS) - 1))


class LivePage(BasePage):
	"""Valeurs en direct (Mode 01)."""

	title = "Valeurs en direct"
	subtitle = "Moins il y a de valeurs cochées, plus elles sont rafraîchies souvent."

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition

		splitter = QSplitter(Qt.Orientation.Horizontal)
		self.content.addWidget(splitter, 1)

		# --- liste des PID ---
		left = QWidget()
		left_layout = QVBoxLayout(left)
		left_layout.setContentsMargins(0, 0, 0, 0)
		self.search = QLineEdit()
		self.search.setPlaceholderText("Rechercher un PID…")
		self.search.setClearButtonEnabled(True)
		self.search.textChanged.connect(self._filter_tree)
		left_layout.addWidget(self.search)
		self.show_unsupported = QCheckBox("Afficher les PID non supportés")
		self.show_unsupported.toggled.connect(self._filter_tree)
		left_layout.addWidget(self.show_unsupported)
		self.tree = QTreeWidget()
		self.tree.setHeaderLabels(["Valeur", "PID"])
		self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
		self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
		self.tree.itemChanged.connect(self._item_changed)
		left_layout.addWidget(self.tree, 1)
		buttons = QHBoxLayout()
		for label, slot in (
			("Par défaut", self._select_default),
			("Tout", self._select_all),
			("Aucun", self._select_none),
		):
			button = QPushButton(label)
			button.clicked.connect(slot)
			buttons.addWidget(button)
		left_layout.addLayout(buttons)
		splitter.addWidget(left)

		# --- tableau ---
		right = QWidget()
		right_layout = QVBoxLayout(right)
		right_layout.setContentsMargins(0, 0, 0, 0)
		toolbar = QHBoxLayout()
		self.run_btn = QPushButton()
		self.run_btn.setCheckable(True)
		self.run_btn.toggled.connect(self._toggle_running)
		toolbar.addWidget(self.run_btn)
		toolbar.addWidget(QLabel("Intervalle minimal d'un cycle :"))
		self.interval = QSpinBox()
		self.interval.setRange(0, 10000)
		self.interval.setSingleStep(100)
		self.interval.setSuffix(" ms")
		self.interval.setSpecialValueText("au plus vite")
		self.interval.setValue(ctx.settings.polling.interval_ms)
		self.interval.valueChanged.connect(self.acq.set_interval)
		toolbar.addWidget(self.interval)
		reset = QPushButton("Remettre à zéro min/max")
		reset.clicked.connect(self._reset_stats)
		toolbar.addWidget(reset)
		toolbar.addStretch(1)
		self.rate_label = QLabel()
		toolbar.addWidget(self.rate_label)
		right_layout.addLayout(toolbar)
		self.model = LiveTableModel(acquisition, ctx)
		self.table = QTableView()
		self.table.setModel(self.model)
		self.table.setAlternatingRowColors(True)
		self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
		self.table.verticalHeader().hide()
		self.table.verticalHeader().setDefaultSectionSize(26)
		header = self.table.horizontalHeader()
		header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
		header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
		header.resizeSection(1, 150)
		right_layout.addWidget(self.table, 1)
		self.notice = QLabel()
		self.notice.setObjectName("pageSubtitle")
		self.notice.setWordWrap(True)
		right_layout.addWidget(self.notice)
		splitter.addWidget(right)
		left.setMinimumWidth(340)
		splitter.setStretchFactor(0, 0)
		splitter.setStretchFactor(1, 1)
		splitter.setSizes([420, 860])

		self._items: dict[str, QTreeWidgetItem] = {}
		self._build_tree()
		self._dirty = False
		self._timer = QTimer(self)
		self._timer.setInterval(150)
		self._timer.timeout.connect(self._refresh)
		self._timer.start()
		acquisition.batch.connect(self._on_batch)
		acquisition.supported_changed.connect(self._on_supported)
		acquisition.running_changed.connect(self._update_run_button)
		acquisition.rates_changed.connect(self._on_rates)
		ctx.settings_changed.connect(self.model.refresh)
		self.run_btn.setChecked(acquisition.running)
		self._update_run_button(acquisition.running)
		self._sync_table()
		self._on_supported()

	# -------------------------------------------------------------- arbre
	def _build_tree(self) -> None:
		self.tree.blockSignals(True)
		self.tree.clear()
		selected = set(self.acq.live_selection())
		categories: dict[str, QTreeWidgetItem] = {}
		for category in pids.CATEGORIES:
			defs = [d for d in pids.CATALOG.values() if d.category == category]
			if not defs:
				continue
			parent = QTreeWidgetItem([category])
			parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
			self.tree.addTopLevelItem(parent)
			categories[category] = parent
			for definition in sorted(defs, key=lambda d: (d.pid if d.pid >= 0 else 999, d.key)):
				pid_text = (
					definition.request[2:] if definition.request else "ATRV" if definition.source == "atrv" else ""
				)
				item = QTreeWidgetItem([definition.name, pid_text])
				item.setData(0, Qt.ItemDataRole.UserRole, definition.key)
				item.setToolTip(0, f"{definition.name} ({definition.unit or 'texte'}) — clé {definition.key}")
				item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
				item.setCheckState(0, Qt.CheckState.Checked if definition.key in selected else Qt.CheckState.Unchecked)
				parent.addChild(item)
				self._items[definition.key] = item
			parent.setExpanded(True)
		self.tree.blockSignals(False)

	def _filter_tree(self, *_args: object) -> None:
		needle = self.search.text().strip().lower()
		show_all = self.show_unsupported.isChecked()
		for index in range(self.tree.topLevelItemCount()):
			parent = self.tree.topLevelItem(index)
			visible_children = 0
			for child_index in range(parent.childCount()):
				child = parent.child(child_index)
				key = child.data(0, Qt.ItemDataRole.UserRole)
				available = self.acq.is_available(key)
				text = f"{child.text(0)} {child.text(1)} {key}".lower()
				visible = (not needle or needle in text) and (available or show_all)
				child.setHidden(not visible)
				child.setDisabled(not available)
				visible_children += visible
			parent.setHidden(visible_children == 0)

	def _checked_keys(self) -> list[str]:
		keys = []
		for key, item in self._items.items():
			if item.checkState(0) == Qt.CheckState.Checked:
				keys.append(key)
		# Conserver l'ordre de la sélection existante, puis ajouter les nouvelles valeurs.
		previous = [k for k in self.acq.live_selection() if k in keys]
		return previous + [k for k in keys if k not in previous]

	def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
		if column != 0:
			return
		self.acq.set_live_selection(self._checked_keys())
		self._sync_table()

	def _set_checked(self, keys: set[str]) -> None:
		self.tree.blockSignals(True)
		for key, item in self._items.items():
			item.setCheckState(0, Qt.CheckState.Checked if key in keys else Qt.CheckState.Unchecked)
		self.tree.blockSignals(False)
		self.acq.set_live_selection([k for k in self._items if k in keys])
		self._sync_table()

	def _select_default(self) -> None:
		self._set_checked(set(pids.DEFAULT_SELECTION))

	def _select_all(self) -> None:
		self._set_checked({k for k in self._items if self.acq.is_available(k)})

	def _select_none(self) -> None:
		self._set_checked(set())

	# ------------------------------------------------------------ tableau
	def _sync_table(self) -> None:
		self.model.set_keys(self.acq.live_selection())
		self._update_notice()

	def _update_notice(self) -> None:
		if self.acq.supported is None:
			self.notice.setText("PID supportés inconnus : connectez-vous au véhicule (ou au mode démo) pour les lire.")
			return
		unsupported = [k for k in self.acq.live_selection() if not self.acq.is_available(k)]
		obd_count = len([d for d in pids.available(self.acq.supported) if d.source == "obd"])
		text = f"{len(self.acq.supported)} PID supportés par le calculateur ({obd_count} valeurs affichables)."
		if unsupported:
			text += f" {len(unsupported)} valeur(s) cochée(s) non supportée(s) par ce véhicule : ignorée(s)."
		self.notice.setText(text)

	def _on_supported(self) -> None:
		self._filter_tree()
		self._update_notice()
		self.model.refresh()

	def _on_batch(self, _batch: SampleBatch) -> None:
		self._dirty = True

	def _refresh(self) -> None:
		if self._dirty and self.isVisible():
			self._dirty = False
			self.model.refresh()

	def _on_rates(self, query_rate: float, cycle_rate: float) -> None:
		self.rate_label.setText(f"{query_rate:.1f} requêtes/s — {cycle_rate:.2f} cycles/s")

	def _toggle_running(self, running: bool) -> None:
		self.acq.set_running(running)

	def _update_run_button(self, running: bool) -> None:
		self.run_btn.blockSignals(True)
		self.run_btn.setChecked(running)
		self.run_btn.blockSignals(False)
		self.run_btn.setText("⏸ Suspendre l'acquisition" if running else "▶ Reprendre l'acquisition")

	def _reset_stats(self) -> None:
		self.acq.reset_stats()
		self.model.refresh()
