"""Tests du garde-fou de la console."""

from __future__ import annotations

import pytest

from obdtool.core.console_guard import Level, changes_format, classify, is_reset, normalize


@pytest.mark.parametrize(
	("command", "level"),
	[
		("ATI", Level.ALLOWED),
		("at rv", Level.ALLOWED),
		("ATZ", Level.ALLOWED),
		("ATSH 7E0", Level.ALLOWED),
		("ATCRA7E8", Level.ALLOWED),
		("ATPP 0C SV 23", Level.EXPERT),
		("ATPPFFOFF", Level.EXPERT),
		("AT@3 ABC", Level.EXPERT),
		("ATBRD 23", Level.EXPERT),
		("ATMA", Level.INVALID),
		("STI", Level.EXPERT),
		("010C", Level.ALLOWED),
		("01 0C", Level.ALLOWED),
		("03", Level.ALLOWED),
		("0902", Level.ALLOWED),
		("22F190", Level.ALLOWED),
		("1A80", Level.ALLOWED),
		("04", Level.CONFIRM),
		("14FFFFFF", Level.CONFIRM),
		("1001", Level.ALLOWED),
		("1002", Level.EXPERT),
		("1003", Level.EXPERT),
		("2E F190 31", Level.EXPERT),
		("3101FF00", Level.EXPERT),
		("27 01", Level.EXPERT),
		("3B 01 02", Level.EXPERT),
		("34", Level.EXPERT),
		("11 01", Level.EXPERT),
		("0801", Level.EXPERT),
		("BA", Level.EXPERT),
		("01C", Level.INVALID),
		("XYZ", Level.INVALID),
		("", Level.INVALID),
	],
)
def test_classification(command: str, level: Level) -> None:
	assert classify(command).level == level


def test_helpers() -> None:
	assert normalize(" at sh 7e0 \r") == "ATSH7E0"
	assert is_reset("atz") and is_reset("ATWS") and not is_reset("ATZZ")
	assert changes_format("ATH0") and changes_format("ATSP6") and not changes_format("ATI")
	assert "criture" in classify("2EF19031").reason
