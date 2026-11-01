"""Page Infos véhicule : Mode 09 et informations d'état du Mode 01."""

from __future__ import annotations

import html

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget

from obdtool.core import pids
from obdtool.core.errors import ObdError
from obdtool.core.service import LinkState, StateEvent
from obdtool.core.vehicle_info import INFO_PIDS, VehicleInfo, ipt_ratios, read_vehicle_info
from obdtool.ui import theme
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.widgets.tables import add_row, make_table

_NOT = "non supporté"


class VehiclePage(BasePage):
	"""Identification du véhicule et compteurs."""

	title = "Informations véhicule"
	subtitle = "Mode 09 (identification) et compteurs du Mode 01. Beaucoup de véhicules antérieurs à 2008 n'en fournissent qu'une partie."

	def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.info: VehicleInfo | None = None
		bar = QHBoxLayout()
		self.read_btn = QPushButton("Lire les informations")
		self.read_btn.clicked.connect(self.read)
		bar.addWidget(self.read_btn)
		bar.addStretch(1)
		self.content.addLayout(bar)
		self.status = QLabel()
		self.status.setObjectName("pageSubtitle")
		self.content.addWidget(self.status)
		splitter = QSplitter()
		left = QWidget()
		left_layout = QVBoxLayout(left)
		left_layout.setContentsMargins(0, 0, 0, 0)
		left_layout.addWidget(QLabel("<b>Identification et état</b>"))
		self.table = make_table(["Information", "Valeur"], stretch=1)
		left_layout.addWidget(self.table)
		splitter.addWidget(left)
		right = QWidget()
		right_layout = QVBoxLayout(right)
		right_layout.setContentsMargins(0, 0, 0, 0)
		right_layout.addWidget(QLabel("<b>Compteurs de performance en utilisation (Mode 09 PID 08)</b>"))
		self.ipt = make_table(["Moniteur", "Réalisations", "Conditions réunies", "Ratio"], stretch=0)
		right_layout.addWidget(self.ipt)
		self.ipt_note = QLabel(
			"Ratio = nombre de fois où le moniteur a pu s'exécuter / nombre de trajets où les conditions étaient réunies."
		)
		self.ipt_note.setWordWrap(True)
		self.ipt_note.setObjectName("pageSubtitle")
		right_layout.addWidget(self.ipt_note)
		splitter.addWidget(right)
		self.content.addWidget(splitter, 1)
		ctx.bridge.state_changed.connect(self._on_state)
		self._on_state(StateEvent(ctx.service.state))

	def read(self) -> None:
		self.read_btn.setEnabled(False)
		self.status.setText("Lecture en cours…")
		self.ctx.bridge.run(
			self.ctx.service.submit(read_vehicle_info, "infos véhicule", priority=1), self._show, self._error
		)

	def _error(self, error: ObdError) -> None:
		self.read_btn.setEnabled(True)
		self.status.setText(f"<span style='color:{theme.COLOR_ERROR}'>{html.escape(error.user_message)}</span>")

	def _show(self, info: VehicleInfo) -> None:
		self.info = info
		self.read_btn.setEnabled(True)
		link = self.ctx.service.link.info
		rows: list[tuple[str, str]] = [
			("VIN (numéro de série)", info.vin or _NOT),
			("Identifiants de calibration (CAL ID)", ", ".join(info.calibration_ids) if info.calibration_ids else _NOT),
			("Numéros de vérification (CVN)", ", ".join(info.cvns) if info.cvns else _NOT),
			("Nom du calculateur", info.ecu_name or _NOT),
			("Protocole", link.protocol_name or "—"),
			("Sondes lambda présentes", info.o2_sensors or _NOT),
			(
				"PID Mode 09 supportés",
				" ".join(f"{p:02X}" for p in info.mode09_supported)
				if info.mode09_supported
				else "aucun (Mode 09 non supporté)",
			),
		]
		for key in INFO_PIDS:
			definition = pids.CATALOG[key]
			value = info.mode01.get(key)
			text = _NOT if value is None else f"{pids.format_value(value, definition.unit)} {definition.unit}".strip()
			if key in ("RUN_TIME_MIL", "TIME_SINCE_DTC_CLEARED") and isinstance(value, float):
				text = f"{value:.0f} min ({value / 60:.1f} h)"
			rows.append((definition.name, text))
		self.table.setRowCount(0)
		for label, value in rows:
			add_row(self.table, [label, value], theme.COLOR_MUTED if value.startswith(_NOT) else None, 1)
		if info.vin and len(info.vin) == 17:
			self.ctx.settings.extra["vehicle_vin"] = info.vin
		self.ipt.setRowCount(0)
		if not info.ipt:
			self.ipt_note.setText("Compteurs non disponibles (Mode 09 PID 08 non supporté, courant avant 2010).")
		else:
			add_row(self.ipt, ["Cycles de conduite (OBDCOND)", info.ipt.get("OBDCOND", 0), "", ""])
			add_row(self.ipt, ["Démarrages (IGNCNTR)", info.ipt.get("IGNCNTR", 0), "", ""])
			for label, done, conditions, ratio in ipt_ratios(info.ipt):
				add_row(self.ipt, [label, done, conditions, "—" if ratio is None else f"{ratio:.2f}"])
		self.status.setText("Informations lues.")

	def _on_state(self, event: StateEvent) -> None:
		connected = event.state == LinkState.CONNECTED
		self.read_btn.setEnabled(connected)
		if not connected:
			self.status.setText("Connectez-vous au véhicule pour lire ses informations.")
		elif self.isVisible() and self.info is None:
			self.read()

	def page_shown(self) -> None:
		if self.ctx.service.state == LinkState.CONNECTED and self.info is None:
			self.read()
