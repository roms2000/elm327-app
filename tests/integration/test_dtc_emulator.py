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
