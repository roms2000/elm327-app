"""Tests des moniteurs, des tests embarqués et des informations véhicule (décodage pur)."""

from __future__ import annotations

from obdtool.core.onboard_tests import MID_NAMES, TestResult
from obdtool.core.readiness import MonitorState, decode_status
from obdtool.core.vehicle_info import decode_ipt, describe_o2_sensors, ipt_ratios


def test_decode_status_spark() -> None:
	# MIL allumé, 2 codes ; continus supportés et terminés ; catalyseur, EVAP, sondes, réchauffage supportés ;
	# EVAP non terminé.
	report = decode_status(bytes([0x82, 0x07, 0x65, 0x04]))
	assert report.mil_on and report.dtc_count == 2 and report.ignition == "spark"
	states = {m.name: m.state for m in report.monitors}
	assert states["Catalyseur"] == MonitorState.COMPLETE
	assert states["Système antiévaporation (EVAP)"] == MonitorState.INCOMPLETE
	assert states["Catalyseur chauffé"] == MonitorState.UNSUPPORTED
	assert [m.name for m in report.incomplete_non_continuous] == ["Système antiévaporation (EVAP)"]
	assert not report.ready_for_inspection()
	ok = decode_status(bytes([0x00, 0x07, 0x65, 0x04]))
	assert not ok.ready_for_inspection() and ok.ready_for_inspection(tolerance=1)
	assert decode_status(bytes([0x00, 0x07, 0x65, 0x00])).ready_for_inspection()


def test_decode_status_compression_and_pid41() -> None:
	report = decode_status(bytes([0x00, 0x08 | 0x07, 0x40, 0x40]), source="41")
	assert report.ignition == "compression"
	names = [m.name for m in report.monitors]
	assert "Filtre à particules" in names and "(réservé)" not in names
	assert not report.mil_on  # l'octet A n'a pas ce sens dans le PID 41


def test_continuous_incomplete() -> None:
	report = decode_status(bytes([0x00, 0x17, 0x00, 0x00]))
	misfire = report.monitors[0]
	assert misfire.continuous and misfire.state == MonitorState.INCOMPLETE


def test_mid_names_and_status() -> None:
	assert MID_NAMES[0x01] == "Sonde lambda banc 1 capteur 1"
	assert MID_NAMES[0x21] == "Catalyseur banc 1"
	assert MID_NAMES[0xA2] == "Ratés d'allumage : cylindre 1"
	result = TestResult(1, "x", 1, "y", 0.5, 0.0, 1.0, "V", False)
	assert result.status == "KO"
	assert TestResult(1, "x", 1, "y", None, None, None, "", None).status == "—"


def test_o2_sensor_layout() -> None:
	assert describe_o2_sensors(0x13, 0x03) == "banc 1 capteur 1 (amont), banc 1 capteur 2 (aval)"
	assert "banc 2 capteur 1" in describe_o2_sensors(0x1D, 0x04)
	assert describe_o2_sensors(0x13, 0x00) == "aucune"


def test_ipt() -> None:
	data = bytes.fromhex("0018 009A 0011 0018 0000 0000 0014 0018".replace(" ", ""))
	counters = decode_ipt(data)
	assert counters["OBDCOND"] == 24 and counters["IGNCNTR"] == 154
	rows = ipt_ratios(counters)
	assert ("Catalyseur banc 1", 17, 24, 0.708) in rows
	assert all(label != "Catalyseur banc 2" for label, *_ in rows)
