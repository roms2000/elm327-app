"""Page Personnalisation : lecture seule de la configuration des calculateurs Renault (UCH, tableau de bord…)."""

from __future__ import annotations

import html
import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
	QCheckBox,
	QComboBox,
	QFileDialog,
	QGroupBox,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QListWidget,
	QListWidgetItem,
	QMessageBox,
	QPushButton,
	QSplitter,
	QVBoxLayout,
	QWidget,
)

from obdtool.core.ddt import reader
from obdtool.core.ddt.database import DdtDatabase
from obdtool.core.ddt.reader import EcuReadout, EcuScanResult
from obdtool.core.errors import ObdError
from obdtool.core.service import LinkState, StateEvent
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.widgets.tables import add_row, make_table

log = logging.getLogger(__name__)

DEMO_DATABASE = Path(__file__).resolve().parents[2] / "core" / "data" / "ddt_demo"
DEFAULT_ADDRESSES = ("26", "51")  # UCH et tableau de bord

_NOTICE = (
	f"<b style='color:{theme.COLOR_INFO}'>Lecture seule.</b> Ce module lit la configuration des calculateurs Renault "
	"(UCH, tableau de bord…) d'après la base de définitions de <b>DDT4All</b> (fichier <code>ecu.zip</code>, à "
	"fournir : il n'est pas distribué avec OBDTool). Seules des requêtes de lecture et d'ouverture de session de "
	"diagnostic sont envoyées ; toute écriture est bloquée. Contact mis (ou moteur tournant), véhicule à l'arrêt."
)


class DdtPage(BasePage):
	"""Lecture de la configuration des calculateurs de carrosserie."""

	title = "Personnalisation (lecture seule)"

	def __init__(self, ctx: AppContext, acquisition: AcquisitionController, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.acq = acquisition
		self.db: DdtDatabase | None = None
		self.scans: list[EcuScanResult] = []
		self.readout: EcuReadout | None = None
		self._progress = ""
		self._was_running = True

		notice = QLabel(_NOTICE)
		notice.setWordWrap(True)
		notice.setTextFormat(Qt.TextFormat.RichText)
		self.content.addWidget(notice)

		db_box = QGroupBox("Base de définitions")
		db_layout = QVBoxLayout(db_box)
		row = QHBoxLayout()
		self.db_path = QLineEdit(str(ctx.settings.extra.get("ddt_database", "")))
		self.db_path.setPlaceholderText("Chemin de ecu.zip (DDT4All) ou d'un dossier extrait")
		row.addWidget(self.db_path, 1)
		choose = QPushButton("Choisir…")
		choose.clicked.connect(self._choose_database)
		row.addWidget(choose)
		demo = QPushButton("Base de démonstration (fictive)")
		demo.clicked.connect(lambda: self._load_database(DEMO_DATABASE))
		row.addWidget(demo)
		load = QPushButton("Charger")
		load.clicked.connect(lambda: self._load_database(Path(self.db_path.text().strip())))
		row.addWidget(load)
		db_layout.addLayout(row)
		row = QHBoxLayout()
		row.addWidget(QLabel("Véhicule (projet) :"))
		self.project = QComboBox()
		self.project.setMinimumWidth(140)
		self.project.currentIndexChanged.connect(self._fill_addresses)
		row.addWidget(self.project)
		self.db_info = QLabel("Aucune base chargée.")
		self.db_info.setObjectName("pageSubtitle")
		row.addWidget(self.db_info, 1)
		db_layout.addLayout(row)
		self.content.addWidget(db_box)

		splitter = QSplitter(Qt.Orientation.Horizontal)
		left = QWidget()
		left_layout = QVBoxLayout(left)
		left_layout.setContentsMargins(0, 0, 0, 0)
		left_layout.addWidget(QLabel("<b>Calculateurs à identifier</b>"))
		self.addresses = QListWidget()
		self.addresses.setMaximumHeight(150)
		left_layout.addWidget(self.addresses)
		self.scan_btn = QPushButton("Identifier les calculateurs cochés")
		self.scan_btn.clicked.connect(self._scan)
		left_layout.addWidget(self.scan_btn)
		self.scan_table = make_table(["Adresse", "Calculateur", "Identification", "Définition", "État"], stretch=4)
		self.scan_table.itemSelectionChanged.connect(self._update_buttons)
		left_layout.addWidget(self.scan_table, 1)
		row = QHBoxLayout()
		self.only_config = QCheckBox("Configuration seulement")
		self.only_config.setChecked(True)
		self.only_config.setToolTip("Requêtes de lecture de configuration (« LC_… ») ; décochez pour tout lire.")
		row.addWidget(self.only_config)
		self.read_btn = QPushButton("Lire le calculateur sélectionné")
		self.read_btn.clicked.connect(self._read)
		row.addWidget(self.read_btn)
		left_layout.addLayout(row)
		splitter.addWidget(left)

		right = QWidget()
		right_layout = QVBoxLayout(right)
		right_layout.setContentsMargins(0, 0, 0, 0)
		row = QHBoxLayout()
		self.filter = QLineEdit()
		self.filter.setPlaceholderText("Filtrer (ex. clignot, bip, verrou…)")
		self.filter.textChanged.connect(self._show_values)
		row.addWidget(self.filter, 1)
		self.interesting = QCheckBox("Personnalisations courantes seulement")
		self.interesting.toggled.connect(self._show_values)
		row.addWidget(self.interesting)
		export = QPushButton("Sauvegarder (CSV)…")
		export.setToolTip("Garder une trace de la configuration actuelle avant toute modification future.")
		export.clicked.connect(self._export)
		row.addWidget(export)
		right_layout.addLayout(row)
		self.values = make_table(["Paramètre", "Valeur", "Unité", "Requête", "Description"], stretch=4)
		right_layout.addWidget(self.values, 1)
		self.read_info = QLabel()
		self.read_info.setWordWrap(True)
		self.read_info.setObjectName("pageSubtitle")
		right_layout.addWidget(self.read_info)
		splitter.addWidget(right)
		splitter.setSizes([520, 760])
		self.content.addWidget(splitter, 1)

		self.status = QLabel()
		self.status.setWordWrap(True)
		self.status.setTextFormat(Qt.TextFormat.RichText)
		self.content.addWidget(self.status)
		self._timer = QTimer(self)
		self._timer.setInterval(200)
		self._timer.timeout.connect(lambda: self._progress and self.status.setText(html.escape(self._progress)))

		ctx.bridge.state_changed.connect(self._on_state)
		if self.db_path.text().strip():
			self._load_database(Path(self.db_path.text().strip()), quiet=True)
		self._on_state(StateEvent(ctx.service.state))

	# ------------------------------------------------------------ base
	def _choose_database(self) -> None:
		path, _ = QFileDialog.getOpenFileName(self, "Base DDT4All", str(Path.home()), "Base DDT4All (ecu.zip *.zip)")
		if path:
			self.db_path.setText(path)
			self._load_database(Path(path))

	def _load_database(self, path: Path, quiet: bool = False) -> None:
		try:
			self.db = DdtDatabase(path)
		except (ValueError, OSError) as exc:
			self.db = None
			self.db_info.setText(f"Base non chargée : {exc}")
			if not quiet:
				QMessageBox.warning(self, "Base DDT4All", str(exc))
			self._update_buttons()
			return
		if path != DEMO_DATABASE:
			self.ctx.settings.extra["ddt_database"] = str(path)
			self.ctx.save_settings()
		self.db_path.setText(str(path))
		projects = self.db.projects()
		self.project.blockSignals(True)
		self.project.clear()
		self.project.addItem("Tous", "")
		for project in projects:
			self.project.addItem(project, project)
		wanted = str(self.ctx.settings.extra.get("ddt_project", ""))
		for candidate in (wanted, "X85", "DEMO"):
			index = self.project.findData(candidate)
			if candidate and index >= 0:
				self.project.setCurrentIndex(index)
				break
		self.project.blockSignals(False)
		demo = " — base FICTIVE de démonstration" if path == DEMO_DATABASE else ""
		self.db_info.setText(f"{len(self.db.targets)} définitions, {len(projects)} projets véhicule{demo}.")
		self._fill_addresses()

	def _current_project(self) -> str | None:
		return str(self.project.currentData() or "") or None

	def _fill_addresses(self) -> None:
		self.addresses.clear()
		if self.db is None:
			return
		self.ctx.settings.extra["ddt_project"] = self._current_project() or ""
		for address, group in (self.db.groups(self._current_project()) or self.db.groups()).items():
			item = QListWidgetItem(f"{address} — {group}")
			item.setData(Qt.ItemDataRole.UserRole, address)
			item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
			item.setCheckState(Qt.CheckState.Checked if address in DEFAULT_ADDRESSES else Qt.CheckState.Unchecked)
			self.addresses.addItem(item)
		self._update_buttons()

	# ----------------------------------------------------- opérations
	def _begin(self, text: str) -> None:
		self._was_running = self.acq.running
		self.acq.set_running(False)  # l'adaptateur est reconfiguré : pas d'interrogation OBD en parallèle
		self.ctx.service.auto_vehicle_retry = False
		self._progress = text
		self.status.setText(html.escape(text))
		self._timer.start()
		self.scan_btn.setEnabled(False)
		self.read_btn.setEnabled(False)

	def _end(self) -> None:
		self._timer.stop()
		self._progress = ""
		self.ctx.service.auto_vehicle_retry = True
		self.acq.set_running(self._was_running)
		self._update_buttons()

	def _set_progress(self, text: str) -> None:  # appelé depuis le thread de travail : simple affectation
		self._progress = text

	def _scan(self) -> None:
		if self.db is None:
			return
		addresses = [
			self.addresses.item(i).data(Qt.ItemDataRole.UserRole)
			for i in range(self.addresses.count())
			if self.addresses.item(i).checkState() == Qt.CheckState.Checked
		]
		if not addresses:
			QMessageBox.information(self, "Personnalisation", "Cochez au moins un calculateur.")
			return
		db, project = self.db, self._current_project()
		self._begin("Identification en cours…")
		self.ctx.bridge.run(
			self.ctx.service.submit(
				lambda link: reader.scan_addresses(link, db, addresses, project, self._set_progress),
				"identification Renault",
				priority=1,
			),
			self._on_scan,
			self._on_error,
		)

	def _on_scan(self, scans: list[EcuScanResult]) -> None:
		self._end()
		self.scans = scans
		self.scan_table.setRowCount(0)
		for scan in scans:
			ident = scan.ident
			text = (
				f"diag {ident.diagversion}, {ident.supplier.strip()}, logiciel {ident.soft}, version {ident.version}"
				if ident
				else "—"
			)
			color = theme.COLOR_OK if scan.target else theme.COLOR_WARN if scan.ident else theme.COLOR_MUTED
			add_row(
				self.scan_table,
				[scan.address, scan.group, text, scan.target.name if scan.target else "—", scan.status],
				color,
				4,
			)
		found = [s for s in scans if s.target]
		self.status.setText(
			f"{len(found)} calculateur(s) identifié(s) sur {len(scans)}. Sélectionnez-en un puis lisez-le."
		)
		if found:
			self.scan_table.selectRow(scans.index(found[0]))

	def _selected_scan(self) -> EcuScanResult | None:
		row = self.scan_table.currentRow()
		return self.scans[row] if 0 <= row < len(self.scans) else None

	def _read(self) -> None:
		scan = self._selected_scan()
		if self.db is None or scan is None or scan.target is None:
			return
		db, only = self.db, self.only_config.isChecked()
		self._begin(f"Lecture de {scan.group}…")
		self.ctx.bridge.run(
			self.ctx.service.submit(
				lambda link: reader.read_ecu(link, db, scan, only, self._set_progress), "lecture Renault", priority=1
			),
			self._on_read,
			self._on_error,
		)

	def _on_read(self, readout: EcuReadout) -> None:
		self._end()
		self.readout = readout
		self._show_values()
		parts = [f"{len(readout.values)} valeur(s) lue(s) sur {readout.scan.group} ({readout.scan.target.name})."]
		if readout.failed:
			parts.append(f"{len(readout.failed)} requête(s) refusée(s) : " + ", ".join(readout.failed))
		if readout.skipped:
			parts.append(f"{len(readout.skipped)} requête(s) nécessitant un paramètre non lue(s).")
		self.read_info.setText(" ".join(parts))
		self.status.setText(f"Lecture terminée à {datetime.now():%H:%M:%S}.")

	def _show_values(self, *_args: object) -> None:
		self.values.setRowCount(0)
		if self.readout is None:
			return
		needle = self.filter.text().strip().lower()
		for value in self.readout.values:
			interesting = reader.is_interesting(value)
			if self.interesting.isChecked() and not interesting:
				continue
			text = f"{value.name} {value.display} {value.description} {value.request}".lower()
			if needle and needle not in text:
				continue
			row = add_row(self.values, [value.name, value.display, value.unit, value.request, value.description])
			if interesting:
				for column in range(2):
					item = self.values.item(row, column)
					font = QFont(item.font())
					font.setBold(True)
					item.setFont(font)
					item.setForeground(QBrush(QColor(theme.COLOR_INFO)))

	def _on_error(self, error: ObdError) -> None:
		self._end()
		self.status.setText(f"<span style='color:{theme.COLOR_ERROR}'>{html.escape(error.user_message)}</span>")

	def _export(self) -> None:
		if self.readout is None:
			QMessageBox.information(self, "Personnalisation", "Lisez d'abord un calculateur.")
			return
		default = Path.home() / f"config_{self.readout.scan.group.replace(' ', '_')}_{datetime.now():%Y%m%d_%H%M}.csv"
		path, _ = QFileDialog.getSaveFileName(self, "Sauvegarder la configuration", str(default), "CSV (*.csv)")
		if path:
			count = reader.export_csv(self.readout, Path(path))
			self.ctx.notify(f"{count} valeurs sauvegardées : {path}")

	# ------------------------------------------------------------ état
	def _update_buttons(self) -> None:
		linked = self.ctx.service.state in (LinkState.CONNECTED, LinkState.ADAPTER_ONLY)
		busy = bool(self._progress)
		self.scan_btn.setEnabled(linked and self.db is not None and not busy)
		scan = self._selected_scan()
		self.read_btn.setEnabled(linked and not busy and scan is not None and scan.target is not None)

	def _on_state(self, event: StateEvent) -> None:
		self._update_buttons()
		if event.state == LinkState.CONNECTED and self.db is None and self.ctx.service.emulator is not None:
			self._load_database(DEMO_DATABASE, quiet=True)
		if event.state == LinkState.DISCONNECTED and not self._progress:
			self.status.setText("Connectez-vous à l'adaptateur pour identifier les calculateurs.")
