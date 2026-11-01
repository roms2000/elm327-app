"""Tests de l'analyse des réponses d'erreur et des protocoles."""

from __future__ import annotations

import pytest

from obdtool.core.errors import ErrorKind, ObdError, classify, clean_lines
from obdtool.core.protocols import PROTOCOLS, describe_dpn, is_can, parse_dpn, protocol_by_code


@pytest.mark.parametrize(
	("lines", "expected"),
	[
		(["NO DATA"], ErrorKind.NO_DATA),
		(["SEARCHING...", "UNABLE TO CONNECT"], ErrorKind.UNABLE_TO_CONNECT),
		(["CAN ERROR"], ErrorKind.CAN_ERROR),
		(["BUS INIT: ...ERROR"], ErrorKind.BUS_INIT),
		(["STOPPED"], ErrorKind.STOPPED),
		(["?"], ErrorKind.UNKNOWN_COMMAND),
		(["BUFFER FULL"], ErrorKind.BUFFER_FULL),
		(["ERR94"], ErrorKind.BUS_ERROR),
		(["LV RESET"], ErrorKind.LOW_VOLTAGE),
		(["7E8 06 41 00 BE 3F A8 13"], None),
		(["SEARCHING...", "7E8 06 41 00 BE 3F A8 13"], None),
		(["ELM327 v1.4"], None),
		([], None),
	],
)
def test_classify(lines: list[str], expected: ErrorKind | None) -> None:
	assert classify(lines) == expected


def test_clean_lines_removes_noise() -> None:
	assert clean_lines(["SEARCHING...", "", "BUS INIT: ...OK", "41 00 BE 3F A8 13"]) == ["41 00 BE 3F A8 13"]
	assert clean_lines(["SEARCHING...7E8 03 41 05 5F"]) == ["7E8 03 41 05 5F"]


def test_error_message_is_french() -> None:
	err = ObdError(ErrorKind.NO_DATA, "(commande 0141)")
	assert "NO DATA" in err.user_message
	assert "0141" in str(err)
	assert ObdError(ErrorKind.ADAPTER_LOST).is_link_lost
	assert not err.is_link_lost


def test_protocols_complete() -> None:
	assert [p.code for p in PROTOCOLS] == list("0123456789ABC")
	assert protocol_by_code("a").code == "A"
	assert is_can("6") and not is_can("5")


def test_dpn() -> None:
	assert parse_dpn("A6") == ("6", True)
	assert parse_dpn("6") == ("6", False)
	assert parse_dpn("A") == ("A", False)
	assert "500" in describe_dpn("A6") and "auto" in describe_dpn("A6")
	assert "inconnu" in describe_dpn("Z")
