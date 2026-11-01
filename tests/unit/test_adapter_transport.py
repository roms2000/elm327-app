"""Tests de l'identification de l'adaptateur et de la découverte des ports."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from obdtool.core import transport
from obdtool.core.adapter_info import (
	AdapterInfo,
	VehicleState,
	analyse_clone,
	assess_vehicle_state,
	assess_voltage,
	parse_version,
	parse_voltage,
)


def test_parse_version_and_voltage() -> None:
	assert parse_version("ELM327 v1.4b") == "1.4b"
	assert parse_version("ELM327 V2.1") == "2.1"
	assert parse_version("garbage") == ""
	assert parse_voltage("12.6V") == 12.6
	assert parse_voltage("14,1V") == 14.1
	assert parse_voltage("--.-V") is None


def _info(version: str, **probes: bool) -> AdapterInfo:
	return AdapterInfo(version=version, probes=dict(probes))


def test_genuine_like_adapter_has_no_warning() -> None:
	# Réponses relevées sur l'adaptateur réel de test (v1.4, sans véhicule).
	info = _info("ELM327 v1.4", ATPPS=True, ATCS=True, ATIGN=True, ATRD=True, STI=False)
	assert analyse_clone(info) == []


def test_fake_versions_detected() -> None:
	assert any("clone" in w for w in analyse_clone(_info("ELM327 v1.5")))
	assert any("v2.1" in w for w in analyse_clone(_info("ELM327 v2.1")))
	warnings = analyse_clone(_info("ELM327 v2.2", ATPPS=True, ATCS=True, ATIGN=False, ATRD=False))
	assert any("ATIGN" in w and "ATRD" in w for w in warnings)


def test_stn_chip_trusted() -> None:
	info = AdapterInfo(version="ELM327 v1.5", stn="STN1110 v4.0")
	assert analyse_clone(info) == []


@pytest.mark.parametrize(
	("voltage", "ecu", "state"),
	[
		(28.3, False, VehicleState.NOT_PLUGGED),
		(None, False, VehicleState.UNKNOWN),
		(12.4, False, VehicleState.ECU_SILENT),
		(12.4, True, VehicleState.IGNITION_ON),
		(14.1, True, VehicleState.ENGINE_RUNNING),
		(None, True, VehicleState.ECU_RESPONDING),
	],
)
def test_vehicle_state(voltage: float | None, ecu: bool, state: VehicleState) -> None:
	assert assess_vehicle_state(voltage, ecu) == state


def test_assess_voltage_messages() -> None:
	assert "incohérente" in assess_voltage(28.3)
	assert "faible" in assess_voltage(11.2)
	assert "arrêté" in assess_voltage(12.6)
	assert "alternateur" in assess_voltage(14.0)


def test_wifi_helpers() -> None:
	assert transport.wifi_url(" 192.168.0.10 ", 35000) == "socket://192.168.0.10:35000"
	assert transport.validate_wifi("192.168.0.10", 35000) is None
	assert transport.validate_wifi("", 35000)
	assert transport.validate_wifi("192.168.0.10", 0)
	assert transport.validate_wifi("bad host!", 35000)


def test_port_access_missing() -> None:
	access = transport.check_port_access("/dev/ttyUSB_inexistant")
	assert not access.exists and not access.ok
	assert "n'existe pas" in access.advice


@pytest.mark.skipif(os.geteuid() == 0, reason="root a tous les droits")
def test_port_access_denied(tmp_path: Path) -> None:
	fake = tmp_path / "ttyFAKE"
	fake.write_text("")
	fake.chmod(0o000)
	access = transport.check_port_access(str(fake))
	assert access.exists and not access.ok
	assert "usermod -aG" in access.advice or "session" in access.advice
	fake.chmod(0o600)
	assert transport.check_port_access(str(fake)).ok


def test_serial_group_name() -> None:
	assert transport.serial_group_name() in ("dialout", "uucp", "tty")
