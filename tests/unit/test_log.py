"""Tests de la journalisation par session."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from obdtool.core import log as applog


def _fake_session(directory: Path, stamp: str, size: int, pid: int = 1, backups: int = 0) -> None:
	(directory / f"obdtool_{stamp}_{pid}.log").write_bytes(b"x" * size)
	for i in range(1, backups + 1):
		(directory / f"obdtool_{stamp}_{pid}.log.{i}").write_bytes(b"y" * size)


def test_setup_creates_new_file_per_session(tmp_path: Path) -> None:
	first = applog.setup_logging(directory=tmp_path, console=False)
	logging.getLogger("test").info("bonjour")
	time.sleep(1.1)
	second = applog.setup_logging(directory=tmp_path, console=False)
	assert first != second
	assert "bonjour" in first.read_text(encoding="utf-8")
	sessions = applog.list_sessions(tmp_path)
	assert len(sessions) == 2
	assert sessions[0].is_current
	assert sessions[0].files[0] == second


def test_list_groups_rotated_files(tmp_path: Path) -> None:
	_fake_session(tmp_path, "20260101_100000", 10, backups=2)
	(tmp_path / "autre.txt").write_text("ignoré")
	sessions = applog.list_sessions(tmp_path)
	assert len(sessions) == 1
	assert len(sessions[0].files) == 3
	assert sessions[0].size == 30


def test_purge_by_count_and_size(tmp_path: Path) -> None:
	for day in range(1, 6):
		_fake_session(tmp_path, f"202601{day:02d}_100000", 1024)
	assert applog.purge_logs(keep_sessions=3, max_total_mb=100, directory=tmp_path) == 2
	remaining = [s.key for s in applog.list_sessions(tmp_path)]
	assert remaining == ["20260105_100000_1", "20260104_100000_1", "20260103_100000_1"]
	applog.purge_logs(keep_sessions=10, max_total_mb=2048 / (1024 * 1024), directory=tmp_path)
	assert len(applog.list_sessions(tmp_path)) == 2


def test_current_session_never_deleted(tmp_path: Path) -> None:
	_fake_session(tmp_path, "20200101_100000", 10)
	current = applog.setup_logging(directory=tmp_path, console=False)
	applog.delete_old_logs(tmp_path)
	assert current.exists()
	assert [s.is_current for s in applog.list_sessions(tmp_path)] == [True]


def test_setup_purges_keeping_room_for_new_session(tmp_path: Path) -> None:
	for day in range(1, 6):
		_fake_session(tmp_path, f"202601{day:02d}_100000", 10)
	applog.setup_logging(keep_sessions=3, directory=tmp_path, console=False)
	assert len(applog.list_sessions(tmp_path)) == 3


def test_read_log_truncates(tmp_path: Path) -> None:
	path = tmp_path / "obdtool_20260101_100000_1.log"
	path.write_text("ligne\n" * 1000, encoding="utf-8")
	text = applog.read_log(path, max_bytes=100)
	assert text.startswith("[… début tronqué")
	assert len(text) < 150


def test_format_size() -> None:
	assert applog.format_size(512) == "512 o"
	assert applog.format_size(2048) == "2.0 Ko"
	assert applog.format_size(5 * 1024 * 1024) == "5.0 Mo"


def test_set_level_debug_enables_obd() -> None:
	applog.set_level("DEBUG")
	assert logging.getLogger("obd").level == logging.DEBUG
	applog.set_level("INFO")
	assert logging.getLogger("obd").level == logging.WARNING
