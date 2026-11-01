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
