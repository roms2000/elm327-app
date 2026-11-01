"""Page Moniteurs : état de préparation des diagnostics embarqués (readiness)."""

from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QVBoxLayout, QWidget

from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ObdError
from obdtool.core.readiness import MonitorState, ReadinessReport, read_readiness
from obdtool.core.service import LinkState, StateEvent
from obdtool.ui import theme
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage
from obdtool.ui.widgets.tables import add_row, make_table

_COLORS = {
	MonitorState.COMPLETE: theme.COLOR_OK,
	MonitorState.INCOMPLETE: theme.COLOR_WARN,
	MonitorState.UNSUPPORTED: theme.COLOR_MUTED,
}
_ICONS = {MonitorState.COMPLETE: "✔", MonitorState.INCOMPLETE: "✖", MonitorState.UNSUPPORTED: "—"}


def read_both(link: ElmLink) -> tuple[ReadinessReport | None, ReadinessReport | None]:
	return read_readiness(link, 0x01), read_readiness(link, 0x41)


class MonitorsPage(BasePage):
	"""Moniteurs de préparation (PID 01 depuis l'effacement, PID 41 cycle en cours)."""

	title = "Moniteurs de préparation"
	subtitle = (
		"Les moniteurs vérifient périodiquement les systèmes antipollution. Après un effacement des codes, ils "
		"repassent à « non terminé » jusqu'à un cycle de conduite complet (ville + route, moteur chaud)."
	)

	def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		bar = QHBoxLayout()
		self.read_btn = QPushButton("Lire les moniteurs")
		self.read_btn.clicked.connect(self.read)
		bar.addWidget(self.read_btn)
		self.auto = QCheckBox("Actualiser toutes les 10 s")
		self.auto.toggled.connect(lambda on: self._timer.start() if on else self._timer.stop())
		bar.addWidget(self.auto)
		bar.addStretch(1)
		self.content.addLayout(bar)

		self.summary = QLabel("Connectez-vous au véhicule puis lisez les moniteurs.")
		self.summary.setWordWrap(True)
		self.summary.setTextFormat(Qt.TextFormat.RichText)
		self.content.addWidget(self.summary)

		splitter = QSplitter(Qt.Orientation.Horizontal)
		for attribute, title in (
			("since_clear", "Depuis l'effacement des codes (PID 01)"),
			("this_cycle", "Cycle de conduite en cours (PID 41)"),
		):
			box = QWidget()
			layout = QVBoxLayout(box)
			layout.setContentsMargins(0, 0, 0, 0)
			label = QLabel(f"<b>{title}</b>")
			layout.addWidget(label)
			table = make_table(["Moniteur", "Type", "État"])
			layout.addWidget(table)
			note = QLabel()
			note.setObjectName("pageSubtitle")
			note.setWordWrap(True)
			layout.addWidget(note)
			setattr(self, attribute, table)
			setattr(self, f"{attribute}_note", note)
			splitter.addWidget(box)
		self.content.addWidget(splitter, 1)

		self._timer = QTimer(self)
		self._timer.setInterval(10_000)
		self._timer.timeout.connect(self.read)
		ctx.bridge.state_changed.connect(self._on_state)
		self._on_state(StateEvent(ctx.service.state))

	def read(self) -> None:
		if self.ctx.service.state != LinkState.CONNECTED:
			return
		self.read_btn.setEnabled(False)
		self.ctx.bridge.run(self.ctx.service.submit(read_both, "moniteurs", priority=1), self._show, self._error)

	def _error(self, error: ObdError) -> None:
		self.read_btn.setEnabled(True)
		self.summary.setText(f"<span style='color:{theme.COLOR_ERROR}'>{html.escape(error.user_message)}</span>")

	def _fill(self, table: object, note: QLabel, report: ReadinessReport | None, source: str) -> None:
		table.setRowCount(0)  # type: ignore[attr-defined]
		if report is None:
			note.setText(f"PID {source} non supporté par ce calculateur.")
			return
		for monitor in report.monitors:
			add_row(
				table,  # type: ignore[arg-type]
				[
					monitor.name,
					"continu" if monitor.continuous else "non continu",
					f"{_ICONS[monitor.state]} {monitor.state.value}",
				],
				_COLORS[monitor.state],
				2,
			)
		ignition = "allumage commandé (essence)" if report.ignition == "spark" else "allumage par compression (diesel)"
		note.setText(f"Moteur à {ignition}. {len(report.supported)} moniteur(s) supporté(s).")

	def _show(self, reports: tuple[ReadinessReport | None, ReadinessReport | None]) -> None:
		self.read_btn.setEnabled(True)
		since, cycle = reports
		self._fill(self.since_clear, self.since_clear_note, since, "01")
		self._fill(self.this_cycle, self.this_cycle_note, cycle, "41")
		stamp = f"Lecture du {datetime.now():%d/%m/%Y %H:%M:%S}."
		if since is None:
			self.summary.setText(f"{stamp} Le calculateur ne fournit pas l'état des moniteurs (PID 01).")
			return
		incomplete = since.incomplete_non_continuous
		if since.ready_for_inspection():
			verdict = f"<b style='color:{theme.COLOR_OK}'>✔ Prêt pour un contrôle anti-pollution : OUI</b>"
		else:
			verdict = f"<b style='color:{theme.COLOR_ERROR}'>✖ Prêt pour un contrôle anti-pollution : NON</b>"
		reasons = []
		if since.mil_on:
			reasons.append(f"voyant moteur allumé ({since.dtc_count} défaut(s))")
		if incomplete:
			reasons.append("moniteur(s) non terminé(s) : " + ", ".join(m.name for m in incomplete))
		detail = (" — " + " ; ".join(reasons)) if reasons else ""
		tolerance = ""
		if not since.mil_on and len(incomplete) == 1:
			tolerance = (
				"<br><i>Selon la règle américaine (EPA, véhicules 2001 et plus), un moniteur non terminé est toléré ; "
				"les règles européennes varient selon les pays.</i>"
			)
		self.summary.setText(f"{stamp}<br>{verdict}{html.escape(detail)}{tolerance}")

	def _on_state(self, event: StateEvent) -> None:
		connected = event.state == LinkState.CONNECTED
		self.read_btn.setEnabled(connected)
		if connected and self.isVisible():
			self.read()

	def page_shown(self) -> None:
		if self.ctx.service.state == LinkState.CONNECTED and self.since_clear.rowCount() == 0:
			self.read()
