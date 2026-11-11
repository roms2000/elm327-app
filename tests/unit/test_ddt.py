"""Tests du module de lecture des calculateurs Renault (base DDT4All, décodage, garde-fou de lecture seule)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from obdtool.core.ddt import reader
from obdtool.core.ddt.database import (
	AutoIdent,
	DdtData,
	DdtDatabase,
	DdtDataItem,
	display_value,
	extract_bits,
	parse_ecu,
)
from obdtool.core.ddt.session import check_read_only, parse_ident_2180, reassemble
from obdtool.core.errors import ErrorKind, ObdError

DEMO = Path(__file__).resolve().parents[2] / "obdtool" / "core" / "data" / "ddt_demo"


def test_extract_bits_big_and_little_endian() -> None:
	response = bytes.fromhex("6101 68 01 1234")
	assert extract_bits(response, DdtDataItem("a", 3, 0), 1, "Big") == 0
	assert extract_bits(response, DdtDataItem("b", 3, 1), 1, "Big") == 1
	assert extract_bits(response, DdtDataItem("c", 3, 4), 2, "Big") == 2  # 0x68 = 0110 1000
	assert extract_bits(response, DdtDataItem("d", 5), 16, "Big") == 0x1234
	assert extract_bits(response, DdtDataItem("e", 5), 16, "Little") == 0x3412
	assert extract_bits(response, DdtDataItem("f", 3, 0), 1, "Little") == 0  # bit de poids faible de 0x68
	assert extract_bits(response, DdtDataItem("g", 3, 3), 1, "Little") == 1
	assert extract_bits(response, DdtDataItem("h", 9), 8, "Big") is None  # hors trame


def test_display_value() -> None:
	assert display_value(DdtData("x", bitscount=2, lists={2: "60 s"}), 2) == "60 s"
	assert display_value(DdtData("t", bitscount=8, scaled=True, step=0.5, offset=-40), 100) == "10"
	assert display_value(DdtData("v", bitscount=16, scaled=True, step=1, divideby=10, fmt="0.0"), 125) == "12,5"
	assert display_value(DdtData("s", bitscount=8, scaled=True, signed=True), 0xFE) == "-2"
	assert display_value(DdtData("a", bitscount=24, bytesascii=True), int.from_bytes(b"VF1", "big")) == "VF1"
	assert display_value(DdtData("h", bitscount=8), 0xAB) == "AB"
	assert display_value(DdtData("z", bitscount=8, scaled=True, divideby=0), 3) == "3"


def test_autoident_matching() -> None:
	reference = AutoIdent("12", "DEM", "0123", "0001")
	assert reference.matches(AutoIdent("12", "DEM", "0123", "0001"))
	assert reference.matches(AutoIdent("12", "DEMX", "01234", "00010"))  # préfixes, comme DDT4All
	assert not reference.matches(AutoIdent("13", "DEM", "0123", "0001"))
	assert reference.approximately(AutoIdent("99", "DEM", "0123", "FFFF"))


def test_demo_database_and_readable_requests() -> None:
	db = DdtDatabase(DEMO)
	assert db.projects() == ["DEMO"]
	assert db.groups("DEMO") == {"26": "UCH", "51": "Tableau de bord"}
	assert db.can_ids("26") == ("745", "765")
	assert db.can_ids("7A") == ("7E0", "7E8")  # table connue
	target, exact = db.match("26", AutoIdent("12", "DEM", "0123", "0001"))
	assert target is not None and exact
	ecu = db.load_ecu(target)
	requests, skipped = reader.readable_requests(ecu)
	names = [r.name for r in requests]
	assert names == ["LC_Configuration habitacle"]  # identification sans données ; écriture 3B exclue
	assert "Ecriture configuration habitacle" not in names + skipped
	values = ecu.decode(ecu.requests["LC_Configuration habitacle"], bytes.fromhex("61016801"))
	shown = {v.name: v.display for v in values}
	assert shown["Éclairage d'accompagnement"] == "60 s"
	assert shown["Clignotants impulsionnels (3 clignotements)"] == "Inactif"
	assert all(reader.is_interesting(v) for v in values)


def test_zip_database(tmp_path: Path) -> None:
	archive = tmp_path / "ecu.zip"
	with zipfile.ZipFile(archive, "w") as zf:
		for path in DEMO.iterdir():
			zf.write(path, path.name)
	db = DdtDatabase(archive)
	assert len(db.targets) == 2
	with pytest.raises(ValueError):
		DdtDatabase(tmp_path / "absent.zip")
	(tmp_path / "pasunzip.zip").write_text("x")
	with pytest.raises(ValueError):
		DdtDatabase(tmp_path / "pasunzip.zip")


def test_parse_ecu_tolerates_bad_entries() -> None:
	ecu = parse_ecu(
		json.dumps(
			{
				"requests": [{"sentbytes": "2101"}, {"name": "ok", "sentbytes": "21 02"}],
				"data": {"x": "pas un dict", "y": {"bitscount": 4, "lists": {"a": "b", "1": "Un"}}},
			}
		)
	)
	assert list(ecu.requests) == ["ok"] and ecu.requests["ok"].sentbytes == "2102"
	assert ecu.data["y"].lists == {1: "Un"}


def test_read_only_guard() -> None:
	for allowed in ("2180", "21 01", "22F190", "10C0", "1081", "1003", "1A80", "3E00"):
		check_read_only(allowed)
	for blocked in ("3B0101", "2E F190 00", "3101", "1002", "1101", "2701", "04", "14FF00", "10"):
		with pytest.raises(ObdError) as err:
			check_read_only(blocked)
		assert err.value.kind == ErrorKind.BLOCKED


def test_isotp_reassembly_and_ident() -> None:
	lines = [
		"765 10 1A 61 80 00 00 00 00",
		"765 21 00 0C 44 45 4D 00 00",
		"765 22 00 00 00 01 23 00 01",
		"765 23 00 00 00 00 00 00 00",
		"7E8 03 41 0D 00",
	]
	response = reassemble(lines, "765")
	assert len(response) == 0x1A and response[:2] == b"\x61\x80"
	assert parse_ident_2180(response) == AutoIdent("12", "DEM", "0123", "0001")
	assert reassemble(["765 02 50 C0"], "765") == b"\x50\xc0"
	assert reassemble(["NO DATA"], "765") == b""
	assert parse_ident_2180(b"\x61\x80") is None


def test_renault_fault_list_and_status() -> None:
	from obdtool.core.ddt.faults import RenaultFault, parse_fault_list

	faults = parse_fault_list(bytes.fromhex("57 02 0010E0 012320 000000".replace(" ", "")))
	assert [f.code for f in faults] == ["P0010", "P0123"]
	assert faults[0].present and "voyant" in faults[0].state
	assert not faults[1].present and faults[1].state == "mémorisé, absent actuellement"
	assert parse_fault_list(bytes.fromhex("5700")) == []
	assert RenaultFault("0010", 0x40).state == "intermittent / en cours de validation"
	with pytest.raises(ObdError):
		parse_fault_list(bytes.fromhex("7F1711"))


def test_clear_request_stays_outside_read_only_path() -> None:
	from obdtool.core.ddt.session import CLEAR_ALL_FAULTS

	with pytest.raises(ObdError) as err:
		check_read_only("14FF00")  # l'effacement ne passe jamais par request()
	assert err.value.kind == ErrorKind.BLOCKED
	assert bytes.fromhex("14FF00") == CLEAR_ALL_FAULTS
	for read in ("17FF00", "1802FF00"):
		check_read_only(read)


class _RefusingLink:
	"""Calculateur qui refuse 14 FF 00 (7F 14 22) et reste éveillé ; la tension chute à l'arrêt du moteur."""

	def __init__(self, voltages: list[float]) -> None:
		self.voltages = voltages
		self.clears = 0

	def send_raw(self, command: str, check: bool = True, timeout: float | None = None, clean: bool = True) -> list[str]:
		if command.startswith("AT"):
			return ["OK"]
		if command == "14FF00":
			self.clears += 1
			return ["7E8 03 7F 14 22"]
		return {"10C0": ["7E8 02 50 C0"], "1081": ["7E8 02 50 81"]}[command]

	def read_voltage(self) -> float:
		return self.voltages.pop(0) if len(self.voltages) > 1 else self.voltages[0]

	def restore_defaults(self) -> bool:
		return True


def test_clear_refused_after_engine_stop_ends_with_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
	from obdtool.core.ddt import faults

	monkeypatch.setattr(faults, "VOLTAGE_PERIOD_S", 0.0)
	link = _RefusingLink([14.4, 14.3, 12.4])
	with pytest.raises(ObdError) as err:
		faults.clear_faults(link, during_engine_stop=True, window_s=5.0, stop_grace_s=0.2)  # type: ignore[arg-type]
	assert err.value.kind == ErrorKind.NEGATIVE_RESPONSE
	assert "Moteur coupé" in err.value.user_message and "contact mis" in err.value.user_message
	assert link.clears >= 2

	# Moteur jamais coupé : le délai s'écoule, sans prétendre que le moteur tourne encore par erreur de diagnostic.
	running = _RefusingLink([14.4])
	with pytest.raises(ObdError) as err:
		faults.clear_faults(running, during_engine_stop=True, window_s=0.3)  # type: ignore[arg-type]
	assert err.value.kind == ErrorKind.TIMEOUT and "7F 14 22" in err.value.user_message

	# Demande simple : le refus explique la condition « contact mis, moteur arrêté ».
	with pytest.raises(ObdError) as err:
		faults.clear_faults(_RefusingLink([14.4]))  # type: ignore[arg-type]
	assert err.value.kind == ErrorKind.NEGATIVE_RESPONSE and "contact mis" in err.value.user_message
