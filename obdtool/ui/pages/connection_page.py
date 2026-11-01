"""Page Connexion : choix de l'adaptateur, test et informations."""

from __future__ import annotations

import html
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
	QButtonGroup,
	QCheckBox,
	QComboBox,
	QDoubleSpinBox,
	QFormLayout,
	QGridLayout,
	QGroupBox,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QMessageBox,
	QPushButton,
	QRadioButton,
	QSpinBox,
	QStackedWidget,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import transport
from obdtool.core.adapter_info import AdapterInfo, VehicleState, assess_voltage
from obdtool.core.emulator import DEFAULT_SCENARIO, SCENARIO_LABELS, emulator_available
from obdtool.core.errors import ObdError
from obdtool.core.protocols import PROTOCOLS
from obdtool.core.service import LinkState, StateEvent
from obdtool.ui import theme
from obdtool.ui.context import AppContext
from obdtool.ui.pages.base import BasePage

log = logging.getLogger(__name__)

_KINDS = [("serial", "USB / série / Bluetooth"), ("wifi", "Wi-Fi"), ("demo", "Démo (émulateur)")]

_BT_HELP = (
	"Bluetooth : appairez l'adaptateur (code souvent 1234 ou 0000), puis liez-le à un port série :<br>"
	"<code>sudo rfcomm bind 0 AA:BB:CC:DD:EE:FF 1</code> → <code>/dev/rfcomm0</code>"
)


class ConnectionPage(BasePage):
	"""Configuration et état de la connexion à l'adaptateur."""

	title = "Connexion"
	subtitle = "Choisissez l'adaptateur ELM327, testez la liaison et consultez ses caractéristiques."

	def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self._testing = False
		s = ctx.settings.connection

		columns = QHBoxLayout()
		left = QVBoxLayout()
		right = QVBoxLayout()
		columns.addLayout(left, 1)
		columns.addLayout(right, 1)
		self.content.addLayout(columns, 1)

		# --- type de connexion ---
		kind_box = QGroupBox("Type de connexion")
		kind_layout = QHBoxLayout(kind_box)
		self.kind_group = QButtonGroup(self)
		for index, (key, label) in enumerate(_KINDS):
			radio = QRadioButton(label)
			radio.setProperty("kind", key)
			self.kind_group.addButton(radio, index)
			kind_layout.addWidget(radio)
			if key == s.kind:
				radio.setChecked(True)
		if self.kind_group.checkedButton() is None:
			self.kind_group.button(0).setChecked(True)
		self.kind_group.idToggled.connect(self._kind_changed)
		left.addWidget(kind_box)

		# --- paramètres propres au type ---
		self.kind_stack = QStackedWidget()
		self.kind_stack.addWidget(self._build_serial(s.port, s.baudrate))
		self.kind_stack.addWidget(self._build_wifi(s.wifi_host, s.wifi_port))
		self.kind_stack.addWidget(self._build_demo(s.demo_scenario))
		left.addWidget(self.kind_stack)

		# --- protocole et options ---
		options = QGroupBox("Protocole et options")
		form = QFormLayout(options)
		self.protocol = QComboBox()
		for proto in PROTOCOLS:
			label = proto.name if proto.code == "0" else f"{proto.code} — {proto.name}"
			self.protocol.addItem(label, proto.code)
		self.protocol.setCurrentIndex(max(self.protocol.findData(s.protocol.upper()), 0))
		form.addRow("Protocole :", self.protocol)
		self.fast = QCheckBox("Mode rapide (nombre de réponses attendu ajouté aux requêtes)")
		self.fast.setChecked(s.fast)
		form.addRow("", self.fast)
		self.timeout = QDoubleSpinBox()
		self.timeout.setRange(0.5, 30.0)
		self.timeout.setSingleStep(0.5)
		self.timeout.setSuffix(" s")
		self.timeout.setValue(s.timeout_s)
		form.addRow("Délai de réponse :", self.timeout)
		self.retries = QSpinBox()
		self.retries.setRange(0, 10)
		self.retries.setValue(s.retries)
		form.addRow("Tentatives supplémentaires :", self.retries)
		self.auto_reconnect = QCheckBox("Reconnexion automatique après une perte de liaison")
		self.auto_reconnect.setChecked(s.auto_reconnect)
		form.addRow("", self.auto_reconnect)
		left.addWidget(options)

		# --- boutons ---
		buttons = QHBoxLayout()
		self.connect_btn = QPushButton("Se connecter")
		self.connect_btn.setDefault(True)
		self.connect_btn.clicked.connect(self._connect)
		self.test_btn = QPushButton("Tester la connexion")
		self.test_btn.clicked.connect(self._test)
		self.disconnect_btn = QPushButton("Déconnecter")
		self.disconnect_btn.clicked.connect(self._disconnect)
		self.retry_btn = QPushButton("Réessayer le véhicule")
		self.retry_btn.setToolTip("Après avoir mis le contact, relance la détection du protocole.")
		self.retry_btn.clicked.connect(self._retry_vehicle)
		for button in (self.connect_btn, self.test_btn, self.disconnect_btn, self.retry_btn):
			buttons.addWidget(button)
		buttons.addStretch(1)
		left.addLayout(buttons)
		left.addStretch(1)

		# --- état et informations ---
		state_box = QGroupBox("État")
		state_layout = QVBoxLayout(state_box)
		self.state_label = QLabel()
		self.state_label.setWordWrap(True)
		self.state_label.setTextFormat(Qt.TextFormat.RichText)
		state_layout.addWidget(self.state_label)
		right.addWidget(state_box)

		info_box = QGroupBox("Adaptateur et véhicule")
		self.info_grid = QGridLayout(info_box)
		self.info_grid.setColumnStretch(1, 1)
		self._info_labels: dict[str, QLabel] = {}
		rows = [
			("version", "Version (ATI)"),
			("description", "Description (AT@1)"),
			("chip", "Puce"),
			("protocol", "Protocole (ATDPN)"),
			("voltage", "Tension (ATRV)"),
			("vehicle", "Véhicule"),
			("ignition", "Entrée IgnMon (ATIGN)"),
			("probes", "Commandes testées"),
		]
		for row, (key, label) in enumerate(rows):
			name = QLabel(label + " :")
			name.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
			value = QLabel("—")
			value.setWordWrap(True)
			value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
			self.info_grid.addWidget(name, row, 0)
			self.info_grid.addWidget(value, row, 1)
			self._info_labels[key] = value
		right.addWidget(info_box)

		self.warnings = QLabel()
		self.warnings.setWordWrap(True)
		self.warnings.setTextFormat(Qt.TextFormat.RichText)
		self.warnings.hide()
		right.addWidget(self.warnings)
		right.addStretch(1)

		ctx.bridge.state_changed.connect(self._on_state)
		ctx.bridge.voltage.connect(self._on_voltage)
		self._kind_changed()
		self._on_state(StateEvent(ctx.service.state))

	# ---------------------------------------------------------- construction
	def _build_serial(self, port: str, baudrate: int) -> QWidget:
		box = QGroupBox("Port série")
		form = QFormLayout(box)
		row = QHBoxLayout()
		self.port = QComboBox()
		self.port.setEditable(True)
		self.port.setMinimumWidth(260)
		self.port.currentTextChanged.connect(self._check_access)
		row.addWidget(self.port, 1)
		refresh = QPushButton("Actualiser")
		refresh.clicked.connect(self.refresh_ports)
		row.addWidget(refresh)
		form.addRow("Port :", row)
		self.baud = QComboBox()
		self.baud.addItem("Automatique", 0)
		for value in transport.BAUDRATES:
			self.baud.addItem(str(value), value)
		self.baud.setCurrentIndex(max(self.baud.findData(baudrate), 0))
		form.addRow("Débit :", self.baud)
		self.access_label = QLabel()
		self.access_label.setWordWrap(True)
		self.access_label.setTextFormat(Qt.TextFormat.RichText)
		form.addRow(self.access_label)
		bt = QLabel(_BT_HELP)
		bt.setWordWrap(True)
		bt.setObjectName("pageSubtitle")
		form.addRow(bt)
		self._initial_port = port
		self.refresh_ports()
		return box

	def _build_wifi(self, host: str, port: int) -> QWidget:
		box = QGroupBox("Adaptateur Wi-Fi")
		form = QFormLayout(box)
		self.wifi_host = QLineEdit(host)
		self.wifi_host.setPlaceholderText(transport.DEFAULT_WIFI_HOST)
		form.addRow("Adresse IP :", self.wifi_host)
		self.wifi_port = QSpinBox()
		self.wifi_port.setRange(1, 65535)
		self.wifi_port.setValue(port)
		form.addRow("Port TCP :", self.wifi_port)
		hint = QLabel("Connectez d'abord l'ordinateur au réseau Wi-Fi de l'adaptateur (souvent « WiFi_OBDII »).")
		hint.setWordWrap(True)
		hint.setObjectName("pageSubtitle")
		form.addRow(hint)
		return box

	def _build_demo(self, scenario: str) -> QWidget:
		box = QGroupBox("Mode démo")
		form = QFormLayout(box)
		self.scenario = QComboBox()
		for key, label in SCENARIO_LABELS.items():
			self.scenario.addItem(label, key)
		self.scenario.setCurrentIndex(max(self.scenario.findData(scenario or DEFAULT_SCENARIO), 0))
		self.scenario.currentIndexChanged.connect(self._scenario_changed)
		form.addRow("Scénario :", self.scenario)
		text = (
			"Utilise ELM327-emulator : toute l'application fonctionne sans voiture, avec des valeurs simulées."
			if emulator_available()
			else f"<span style='color:{theme.COLOR_ERROR}'>ELM327-emulator n'est pas installé :</span> "
			"<code>pip install 'obdtool[demo]'</code>"
		)
		label = QLabel(text)
		label.setWordWrap(True)
		form.addRow(label)
		return box

	# ------------------------------------------------------------- actions
	def refresh_ports(self) -> None:
		"""Actualise la liste des ports série."""
		current = self.port.currentText() or self._initial_port
		self.port.blockSignals(True)
		self.port.clear()
		for info in transport.list_ports():
			label = info.device + (f"  ({info.description})" if info.description else "")
			self.port.addItem(label, info.device)
		index = self.port.findData(current)
		if index >= 0:
			self.port.setCurrentIndex(index)
		else:
			self.port.setEditText(current)
		self.port.blockSignals(False)
		self._check_access()

	def selected_port(self) -> str:
		"""Port saisi ou sélectionné."""
		index = self.port.currentIndex()
		if index >= 0 and self.port.itemText(index) == self.port.currentText():
			return str(self.port.itemData(index))
		return self.port.currentText().split("  (")[0].strip()

	def _check_access(self, *_args: object) -> None:
		device = self.selected_port()
		if not device.startswith("/dev/"):
			self.access_label.setText("")
			return
		access = transport.check_port_access(device)
		if access.ok:
			self.access_label.setText(f"<span style='color:{theme.COLOR_OK}'>✔ Accès au port autorisé.</span>")
		else:
			advice = html.escape(access.advice).replace("\n", "<br>")
			self.access_label.setText(f"<span style='color:{theme.COLOR_ERROR}'>✖</span> {advice}")

	def _scenario_changed(self) -> None:
		"""Change le scénario de l'émulateur en cours d'exécution (mode démo)."""
		scenario = str(self.scenario.currentData())
		self.ctx.settings.connection.demo_scenario = scenario
		self.ctx.save_settings()
		emulator = self.ctx.service.emulator
		if emulator is not None and emulator.running:
			self.ctx.service.submit(lambda _link: emulator.set_scenario(scenario), "scénario démo", needs_link=False)
			self.ctx.notify(f"Scénario démo : {SCENARIO_LABELS.get(scenario, scenario)}")

	def _kind(self) -> str:
		button = self.kind_group.checkedButton()
		return str(button.property("kind")) if button else "serial"

	def _kind_changed(self, *_args: object) -> None:
		self.kind_stack.setCurrentIndex([k for k, _ in _KINDS].index(self._kind()))

	def _store_settings(self) -> None:
		s = self.ctx.settings.connection
		s.kind = self._kind()
		s.port = self.selected_port()
		s.baudrate = int(self.baud.currentData())
		s.wifi_host = self.wifi_host.text().strip() or transport.DEFAULT_WIFI_HOST
		s.wifi_port = self.wifi_port.value()
		s.demo_scenario = str(self.scenario.currentData())
		s.protocol = str(self.protocol.currentData())
		s.fast = self.fast.isChecked()
		s.timeout_s = self.timeout.value()
		s.retries = self.retries.value()
		s.auto_reconnect = self.auto_reconnect.isChecked()
		self.ctx.save_settings()

	def _connect(self) -> None:
		self._testing = False
		self.start_connection()

	def start_connection(self) -> None:
		"""Enregistre la configuration et lance la connexion."""
		self._store_settings()
		self.warnings.hide()
		self.ctx.bridge.run(self.ctx.service.connect(self.ctx.settings.connection), self._connected, self._failed)

	def _test(self) -> None:
		self._testing = True
		self.start_connection()

	def _connected(self, info: AdapterInfo) -> None:
		self._show_info(info)
		if self._testing:
			self._testing = False
			self.ctx.service.disconnect()
			vehicle = info.vehicle_state.value
			QMessageBox.information(
				self,
				"Test de connexion",
				f"Adaptateur : {info.version or '?'}\nVéhicule : {vehicle}\n\n"
				+ ("\n".join(info.warnings) if info.warnings else "Aucune anomalie détectée sur l'adaptateur."),
			)

	def _failed(self, error: ObdError) -> None:
		self._testing = False
		QMessageBox.warning(self, "Connexion impossible", error.user_message)

	def _disconnect(self) -> None:
		self.ctx.service.disconnect()

	def _retry_vehicle(self) -> None:
		self.ctx.bridge.run(
			self.ctx.service.retry_vehicle(),
			lambda ok: self._show_info(self.ctx.service.link.info),
			lambda err: self.ctx.notify(err.user_message),
		)

	# ------------------------------------------------------------- affichage
	def _on_state(self, event: StateEvent) -> None:
		state = event.state
		colors = {
			LinkState.CONNECTED: theme.COLOR_OK,
			LinkState.ADAPTER_ONLY: theme.COLOR_WARN,
			LinkState.RECONNECTING: theme.COLOR_WARN,
			LinkState.CONNECTING: theme.COLOR_INFO,
		}
		color = colors.get(state, theme.COLOR_MUTED)
		text = f"<b style='color:{color}'>{state.value}</b>"
		if event.message and state != LinkState.CONNECTED:
			text += f"<br>{html.escape(event.message)}"
		if state == LinkState.ADAPTER_ONLY:
			text += "<br><br>Mettez le contact (ou démarrez le moteur) puis cliquez sur « Réessayer le véhicule »." + (
				" Une nouvelle tentative est faite automatiquement toutes les 10 s."
				if self.auto_reconnect.isChecked()
				else ""
			)
		self.state_label.setText(text)
		busy = state in (LinkState.CONNECTING,)
		linked = state in (LinkState.CONNECTED, LinkState.ADAPTER_ONLY, LinkState.RECONNECTING)
		self.connect_btn.setEnabled(not busy and not linked)
		self.test_btn.setEnabled(not busy and not linked)
		self.disconnect_btn.setEnabled(linked or busy)
		self.retry_btn.setEnabled(state == LinkState.ADAPTER_ONLY)
		if event.info is not None:
			self._show_info(event.info)

	def _on_voltage(self, voltage: float | None) -> None:
		self._info_labels["voltage"].setText(self._voltage_text(voltage, ""))

	@staticmethod
	def _voltage_text(voltage: float | None, raw: str) -> str:
		if voltage is None:
			return f"{raw or '—'} ({assess_voltage(None)})"
		return f"{voltage:.1f} V — {assess_voltage(voltage)}"

	def _show_info(self, info: AdapterInfo) -> None:
		labels = self._info_labels
		labels["version"].setText(info.version or "—")
		labels["description"].setText(info.description or "—")
		labels["chip"].setText(info.stn or "ELM327 ou compatible")
		labels["protocol"].setText(info.protocol_name or "non déterminé (véhicule muet)")
		labels["voltage"].setText(self._voltage_text(info.voltage, info.voltage_raw))
		state = info.vehicle_state
		color = {
			VehicleState.ENGINE_RUNNING: theme.COLOR_OK,
			VehicleState.IGNITION_ON: theme.COLOR_OK,
			VehicleState.ECU_RESPONDING: theme.COLOR_OK,
			VehicleState.NOT_PLUGGED: theme.COLOR_ERROR,
			VehicleState.ECU_SILENT: theme.COLOR_WARN,
		}.get(state, theme.COLOR_MUTED)
		labels["vehicle"].setText(f"<span style='color:{color}'>{state.value}</span>")
		labels["ignition"].setText(
			f"{info.ignition} (indicatif : cette entrée est rarement câblée sur les adaptateurs OBD)"
			if info.ignition
			else "non disponible"
		)
		probes = ", ".join(f"{cmd} {'✔' if ok else '✖'}" for cmd, ok in info.probes.items())
		labels["probes"].setText(probes or "—")
		if info.warnings:
			items = "".join(f"<li>{html.escape(w)}</li>" for w in info.warnings)
			self.warnings.setText(
				f"<b style='color:{theme.COLOR_WARN}'>⚠ Adaptateur suspect</b><ul style='margin:0'>{items}</ul>"
			)
			self.warnings.show()
		else:
			self.warnings.hide()
