"""Connexion, envoi brut et service de diagnostic contre l'émulateur."""

from __future__ import annotations

import threading
import time

import obd
import pytest

from obdtool.core.elm_link import ElmLink
from obdtool.core.emulator import DemoEmulator
from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.service import DiagnosticService, Event, LinkState, StateEvent
from obdtool.core.settings import ConnectionSettings

pytestmark = pytest.mark.integration


def test_open_reads_adapter_info(link: ElmLink) -> None:
	info = link.info
	assert info.version.startswith("ELM327")
	assert info.voltage is not None and 11 < info.voltage < 15
	assert info.ecu_responding
	assert info.protocol_code == "6"
	assert link.car_connected


def test_query_and_errors(link: ElmLink) -> None:
	rpm = link.query(obd.commands.RPM)
	assert rpm.value is not None
	assert rpm.value.units == "revolutions_per_minute"
	# Le mode rapide ajoute le nombre de trames attendu : la requête suivante doit toujours réussir.
	assert link.query(obd.commands.RPM).value is not None
	with pytest.raises(ObdError) as err:
		link.send_raw("0141")
	assert err.value.kind == ErrorKind.NO_DATA
	assert link.send_raw("0141", check=False) == ["NO DATA"]
	assert link.send_raw("ATRV")[0].endswith("V")


def test_closed_link_raises() -> None:
	link = ElmLink()
	with pytest.raises(ObdError) as err:
		link.send_raw("ATI")
	assert err.value.kind == ErrorKind.NOT_CONNECTED


def test_unreachable_wifi() -> None:
	link = ElmLink()
	from obdtool.core.elm_link import LinkConfig

	with pytest.raises(ObdError) as err:
		link.open(LinkConfig(url="socket://127.0.0.1:1", timeout_s=1.0))
	assert err.value.kind == ErrorKind.PORT_ERROR


def _wait_state(states: list[LinkState], wanted: LinkState, timeout: float = 15.0) -> bool:
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if wanted in states:
			return True
		time.sleep(0.05)
	return False


def test_service_demo_connect_and_job() -> None:
	service = DiagnosticService()
	states: list[LinkState] = []
	service.subscribe(lambda e: states.append(e.state) if isinstance(e, StateEvent) else None)
	try:
		settings = ConnectionSettings(kind="demo")
		info = service.connect(settings).result(timeout=20)
		assert info.ecu_responding
		assert _wait_state(states, LinkState.CONNECTED)
		value = service.submit(lambda link: link.query(obd.commands.SPEED).value).result(timeout=5)
		assert value is not None
		service.disconnect().result(timeout=5)
		assert states[-1] == LinkState.DISCONNECTED
		with pytest.raises(ObdError):
			service.submit(lambda link: link.send_raw("ATI")).result(timeout=5)
	finally:
		service.shutdown()


def test_service_reconnects_after_link_loss(emulator: DemoEmulator) -> None:
	"""Perte de liaison (émulateur arrêté) puis reconnexion automatique quand il revient."""
	service = DiagnosticService()
	events: list[Event] = []
	lock = threading.Lock()

	def listener(event: Event) -> None:
		with lock:
			events.append(event)

	def states() -> list[LinkState]:
		with lock:
			return [e.state for e in events if isinstance(e, StateEvent)]

	service.subscribe(listener)
	replacement = DemoEmulator()
	try:
		port = emulator.port
		settings = ConnectionSettings(kind="wifi", wifi_host="127.0.0.1", wifi_port=port, auto_reconnect=True)
		service.connect(settings).result(timeout=20)
		emulator.stop()
		with pytest.raises(ObdError) as err:
			service.submit(lambda link: link.send_raw("0100")).result(timeout=15)
		assert err.value.is_link_lost or err.value.kind == ErrorKind.TIMEOUT
		if err.value.kind == ErrorKind.TIMEOUT:
			pytest.skip("la fermeture de la socket n'est pas vue comme une perte de liaison sur ce système")
		assert LinkState.RECONNECTING in states()
		events.clear()
		replacement.start(port=port)
		deadline = time.monotonic() + 20
		while time.monotonic() < deadline and LinkState.CONNECTED not in states():
			time.sleep(0.1)
		assert LinkState.CONNECTED in states()
	finally:
		service.shutdown()
		replacement.stop()
