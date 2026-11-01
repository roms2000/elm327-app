"""Tests des codes défaut : décodage, descriptions, historique, rapport."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from obdtool.core import dtc, paths, pids
from obdtool.core.dtc import DtcEntry, DtcKind, FreezeFrame, MilStatus, describe, parse_dtc_bytes
from obdtool.core.dtc_history import DtcHistory, HistoryRecord
from obdtool.core.report import DtcReport, to_html, to_text


def test_parse_dtc_bytes() -> None:
	assert parse_dtc_bytes(bytes.fromhex("01710300")) == ["P0171", "P0300"]
	assert parse_dtc_bytes(bytes.fromhex("0000")) == []
	assert parse_dtc_bytes(bytes.fromhex("4123C035810A")) == ["C0123", "U0035", "B010A"]
	assert parse_dtc_bytes(bytes.fromhex("1234")) == ["P1234"]


def test_payload_with_and_without_count_byte() -> None:
	with_count = SimpleNamespace(data=bytearray.fromhex("4302 0171 0300"))
	without = SimpleNamespace(data=bytearray.fromhex("47 0420"))
	empty = SimpleNamespace(data=bytearray.fromhex("4300"))
	assert parse_dtc_bytes(dtc._payload(with_count, 0x43)) == ["P0171", "P0300"]
	assert parse_dtc_bytes(dtc._payload(without, 0x47)) == ["P0420"]
	assert parse_dtc_bytes(dtc._payload(empty, 0x43)) == []
	assert dtc._payload(with_count, 0x47) == b""


def test_describe_sources() -> None:
	french = describe("P0171")
	assert french.source == "fr" and "pauvre" in french.text and french.verified
	english = describe("P0700") if describe("P0700").source == "sae_en" else describe("P2101")
	assert english.source in ("sae_en", "fr")
	unknown = describe("P1ABC")
	assert unknown.source == "générique" and not unknown.verified
	assert "constructeur" in unknown.text
	assert "(non vérifié)" in unknown.display


def test_custom_descriptions_override(isolated_dirs: Path) -> None:
	paths.config_dir().mkdir(parents=True, exist_ok=True)
	(paths.config_dir() / "dtc_custom.json").write_text(
		json.dumps({"renault": {"P1525": {"description": "Test constructeur", "verified": False}}, "P0171": "Perso"}),
		encoding="utf-8",
	)
	assert describe("P1525").text == "Test constructeur"
	assert describe("P1525").display.endswith("(non vérifié)")
	assert describe("P0171").text == "Perso"


def test_french_database_coverage() -> None:
	for code in ("P0100", "P0130", "P0300", "P0301", "P0420", "P0455", "P0500", "P0562", "U0100", "P0335"):
		assert describe(code).source == "fr", code
	assert "banc 1 capteur 2" in describe("P0137").text


def test_code_category() -> None:
	assert "constructeur" in dtc.code_category("P1234")
	assert "allumage" in dtc.code_category("P0301")
	assert "Réseau" in dtc.code_category("U0100")
	assert dtc.code_category("X") == "Code invalide"


def test_history_recurring(tmp_path: Path) -> None:
	history = DtcHistory(tmp_path / "h.sqlite")
	history.add(HistoryRecord(timestamp=1, stored=["P0171", "P0300"], vin="VF1"))
	history.add(HistoryRecord(timestamp=2, event="clear", stored=["P0171", "P0300"], vin="VF1"))
	history.add(HistoryRecord(timestamp=3, stored=[], vin="VF1"))
	history.add(HistoryRecord(timestamp=4, pending=["P0171"], vin="VF1"))
	history.add(HistoryRecord(timestamp=5, stored=["P0171"], vin="VF1"))
	assert history.recurring_codes("VF1") == {"P0171": 1}
	records = history.records()
	assert [r.timestamp for r in records] == [5, 4, 3, 2, 1]
	assert records[-1].stored == ["P0171", "P0300"]
	history.delete(records[0].id or 0)
	assert len(history.records()) == 4
	history.clear_all()
	assert history.records() == []


def test_report_text_and_html() -> None:
	entry = DtcEntry("P0171", DtcKind.STORED, describe("P0171"))
	freeze = FreezeFrame(dtc="P0171", description=describe("P0171"), values=[(pids.CATALOG["RPM"], 800.0)])
	report = DtcReport(
		vin="VF1BR0B0H12345678",
		mil=MilStatus(True, 1, "spark"),
		entries={DtcKind.STORED: [entry], DtcKind.PENDING: [], DtcKind.PERMANENT: None},
		freeze=freeze,
		recurring={"P0171": 2},
		distance_since_clear=120.0,
	)
	text = to_text(report)
	assert "P0171" in text and "ALLUMÉ" in text and "revenu après 2" in text
	assert "Non supporté" in text and "800 tr/min" in text
	page = to_html(report)
	assert page.startswith("<!doctype html>") and "VF1BR0B0H12345678" in page
	assert "<script" not in page
