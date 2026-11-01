"""Tests des réglages persistants."""

from __future__ import annotations

from pathlib import Path

from obdtool.core import paths
from obdtool.core.settings import Settings


def test_defaults_when_missing(tmp_path: Path) -> None:
	s = Settings.load(tmp_path / "absent.toml")
	assert s.connection.port == "/dev/ttyUSB0"
	assert s.connection.wifi_port == 35000
	assert s.general.first_launch_ack is False


def test_roundtrip(tmp_path: Path) -> None:
	path = tmp_path / "config.toml"
	s = Settings()
	s.connection.kind = "wifi"
	s.connection.timeout_s = 3.5
	s.polling.selected_pids = ["RPM", "SPEED"]
	s.extra["layouts"] = {"Conduite": {"gauges": 3}}
	s.save(path)
	loaded = Settings.load(path)
	assert loaded.connection.kind == "wifi"
	assert loaded.connection.timeout_s == 3.5
	assert loaded.polling.selected_pids == ["RPM", "SPEED"]
	assert loaded.extra["layouts"]["Conduite"]["gauges"] == 3


def test_invalid_values_fall_back(tmp_path: Path) -> None:
	path = tmp_path / "config.toml"
	path.write_text(
		'[connection]\nbaudrate = "rapide"\ntimeout_s = 2\nunknown = 1\n[general]\nfirst_launch_ack = 1\n',
		encoding="utf-8",
	)
	s = Settings.load(path)
	assert s.connection.baudrate == 0
	assert s.connection.timeout_s == 2.0
	assert isinstance(s.connection.timeout_s, float)
	assert s.general.first_launch_ack is False


def test_corrupted_file(tmp_path: Path) -> None:
	path = tmp_path / "config.toml"
	path.write_text("ceci n'est pas du toml [[[", encoding="utf-8")
	assert Settings.load(path).connection.kind == "serial"


def test_xdg_paths(isolated_dirs: Path) -> None:
	assert paths.config_dir() == isolated_dirs / "config" / "obdtool"
	assert paths.log_dir() == isolated_dirs / "config" / "obdtool" / "logs"
	assert paths.data_dir() == isolated_dirs / "data" / "obdtool"
	paths.ensure_dirs()
	assert paths.sessions_dir().is_dir()
