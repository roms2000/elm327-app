"""Barre d'état permanente : liaison, protocole, tension, fréquence, enregistrement."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QStatusBar, QWidget

from obdtool.core.service import LinkState, StateEvent
from obdtool.ui import theme
from obdtool.ui.context import AppContext

_STATE_COLORS = {
	LinkState.DISCONNECTED: theme.COLOR_MUTED,
	LinkState.CONNECTING: theme.COLOR_INFO,
	LinkState.RECONNECTING: theme.COLOR_WARN,
	LinkState.ADAPTER_ONLY: theme.COLOR_WARN,
	LinkState.CONNECTED: theme.COLOR_OK,
}


class AppStatusBar(QStatusBar):
	"""Barre d'état de la fenêtre principale."""

	def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.state = QLabel()
		self.protocol = QLabel()
		self.voltage = QLabel()
		self.rate = QLabel()
		self.recording = QLabel()
		for label in (self.state, self.protocol, self.voltage, self.rate, self.recording):
			label.setContentsMargins(8, 0, 8, 0)
			self.addPermanentWidget(label)
		self.set_state(StateEvent(LinkState.DISCONNECTED))
		self.set_voltage(None)
		self.set_rate(None)
		self.set_recording(False)
		ctx.bridge.state_changed.connect(self.set_state)
		ctx.bridge.voltage.connect(self.set_voltage)
		ctx.status_message.connect(self.showMessage)

	def set_state(self, event: StateEvent) -> None:
		color = _STATE_COLORS.get(event.state, theme.COLOR_MUTED)
		kind = self.ctx.service.settings.kind if self.ctx.service.settings else ""
		suffix = " (démo)" if kind == "demo" and event.state != LinkState.DISCONNECTED else ""
		self.state.setText(f"<span style='color:{color}'>●</span> {event.state.value}{suffix}")
		self.state.setToolTip(event.message)
		if event.info is not None and event.info.protocol_name:
			self.protocol.setText(event.info.protocol_name)
		elif event.state in (LinkState.DISCONNECTED, LinkState.RECONNECTING):
			self.protocol.setText("Protocole : —")
		elif event.state == LinkState.ADAPTER_ONLY:
			self.protocol.setText("Protocole : non déterminé")
		if event.info is not None:
			self.set_voltage(event.info.voltage)
		elif event.state == LinkState.DISCONNECTED:
			self.set_voltage(None)
		if event.state != LinkState.CONNECTED:
			self.set_rate(None)

	def set_voltage(self, voltage: float | None) -> None:
		if voltage is None:
			self.voltage.setText("Tension : —")
			return
		color = theme.COLOR_ERROR if voltage < 11.8 or voltage > 15.0 else theme.COLOR_OK if voltage >= 13.2 else ""
		style = f" style='color:{color}'" if color else ""
		self.voltage.setText(f"Tension : <span{style}>{voltage:.1f} V</span>")

	def set_rate(self, hz: float | None) -> None:
		self.rate.setText("Rafraîchissement : —" if hz is None else f"Rafraîchissement : {hz:.1f} Hz")

	def set_recording(self, active: bool, name: str = "") -> None:
		if active:
			self.recording.setText(f"<span style='color:{theme.COLOR_ERROR}'>● REC</span> {name}")
		else:
			self.recording.setText("Enregistrement : non")
