"""Codes défaut et freeze frame contre l'émulateur (scénario avec défauts)."""

from __future__ import annotations

import pytest

from obdtool.core import dtc
from obdtool.core.elm_link import ElmLink, LinkConfig
from obdtool.core.emulator import FAULTS_SCENARIO, DemoEmulator

pytestmark = pytest.mark.integration


@pytest.fixture
def faulty_link():
	emu = DemoEmulator(FAULTS_SCENARIO)
	emu.start()
	link = ElmLink()
	link.open(LinkConfig(url=emu.url, timeout_s=3.0))
	yield link, emu
	link.close()
	emu.stop()


def test_read_freeze_and_clear(faulty_link: tuple[ElmLink, DemoEmulator]) -> None:
	link, emu = faulty_link
	stored = dtc.read_dtcs(link, dtc.DtcKind.STORED)
	pending = dtc.read_dtcs(link, dtc.DtcKind.PENDING)
	assert [e.code for e in stored or []] == ["P0171", "P0300"]
	assert [e.code for e in pending or []] == ["P0420"]
	assert dtc.read_dtcs(link, dtc.DtcKind.PERMANENT) == []
	status = dtc.read_mil_status(link)
	assert status is not None and status.mil_on and status.dtc_count == 2
	freeze = dtc.read_freeze_frame(link)
	assert freeze is not None and freeze.dtc == "P0171"
	values = {d.key: v for d, v in freeze.values}
	assert values["RPM"] == pytest.approx(800.0)
	assert values["COOLANT_TEMP"] == pytest.approx(87.0)
	assert values["SHORT_FUEL_TRIM_1"] == pytest.approx(25.0)

	dtc.clear_dtcs(link)
	assert dtc.read_dtcs(link, dtc.DtcKind.STORED) == []
	assert not dtc.read_mil_status(link).mil_on
	cleared = dtc.read_freeze_frame(link)
	assert cleared is not None and cleared.dtc == ""

	emu.set_scenario("car")
	assert dtc.read_dtcs(link, dtc.DtcKind.STORED) == []


def test_readiness_tests_and_vehicle_info(faulty_link: tuple[ElmLink, DemoEmulator]) -> None:
	from obdtool.core import onboard_tests, readiness, vehicle_info

	link, _emu = faulty_link
	report = readiness.read_readiness(link, 0x01)
	assert report is not None and report.mil_on and not report.ready_for_inspection()
	assert readiness.read_readiness(link, 0x41) is None  # non supporté par l'émulateur
	tests = onboard_tests.read_mode06(link)
	assert not tests.unavailable and len(tests.results) > 5
	assert onboard_tests.read_mode05(link).unavailable.startswith("Mode 05 non disponible en CAN")
	info = vehicle_info.read_vehicle_info(link)
	assert info.vin and len(info.vin) == 17
	assert info.ecu_name == "ECM-EngineControl"
	assert info.ipt and info.ipt["OBDCOND"] > 0
	assert info.mode01["OBD_COMPLIANCE"] == "EOBD (Europe)"
	dtc.clear_dtcs(link)
	cleared = readiness.read_readiness(link, 0x01)
	assert cleared is not None and len(cleared.incomplete_non_continuous) == 4


def test_clear_during_engine_stop(faulty_link: tuple[ElmLink, DemoEmulator]) -> None:
	"""Véhicule sans clé : refus moteur tournant, effacement accepté dès la coupure du moteur."""
	import threading
	import time

	from obdtool.core.errors import ErrorKind, ObdError

	link, emu = faulty_link
	emu.refuse_clear_while_running = True
	with pytest.raises(ObdError) as err:
		dtc.clear_dtcs(link)
	assert "0x22" in err.value.user_message
	states: list[str] = []
	threading.Timer(0.8, lambda: setattr(emu, "engine_running", False)).start()
	start = time.monotonic()
	attempts = dtc.clear_during_engine_stop(link, on_attempt=lambda n, state: states.append(state), window_s=10)
	assert attempts > 1 and time.monotonic() - start >= 0.7
	assert set(states) == {"refusé (moteur tournant)"}
	assert dtc.read_dtcs(link, dtc.DtcKind.STORED) == []
	assert link.send_raw("ATI")  # délai ELM327 rétabli, liaison saine
	stop = threading.Event()
	threading.Timer(0.3, stop.set).start()
	emu.engine_running = True
	with pytest.raises(ObdError) as cancelled:
		dtc.clear_during_engine_stop(link, should_stop=stop.is_set, window_s=10)
	assert cancelled.value.kind == ErrorKind.CANCELLED


def test_renault_read_only_session_on_demo_ecus() -> None:
	"""UCH et tableau de bord fictifs : identification, lecture, refus d'écriture, retour au mode OBD."""
	from pathlib import Path

	from obdtool.core.ddt import reader
	from obdtool.core.ddt.database import DdtDatabase
	from obdtool.core.ddt.session import RenaultSession
	from obdtool.core.errors import ErrorKind, ObdError

	emu = DemoEmulator()
	emu.start()
	link = ElmLink()
	link.open(LinkConfig(url=emu.url, timeout_s=3.0))
	try:
		db = DdtDatabase(Path(__file__).resolve().parents[2] / "obdtool" / "core" / "data" / "ddt_demo")
		scans = {s.address: s for s in reader.scan_addresses(link, db, ["26", "51"], "DEMO")}
		assert scans["26"].status == "identifié" and scans["51"].status == "identifié"
		readout = reader.read_ecu(link, db, scans["51"], only_config=True)
		values = {v.name: v.display for v in readout.values}
		assert values["Volume du bip de réserve carburant"] == "Fort"
		assert values["Seuil d'alerte réserve carburant"] == "7"
		with RenaultSession(link, "745", "765") as session, pytest.raises(ObdError) as err:
			session.request("3B0101")
		assert err.value.kind == ErrorKind.BLOCKED
		assert link.car_connected and link.query_bytes("010C")  # configuration OBD rétablie
	finally:
		link.close()
		emu.stop()
