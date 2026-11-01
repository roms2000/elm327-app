"""Tests de fumée de l'interface (plateforme Qt offscreen)."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from obdtool.core.settings import Settings
from obdtool.ui.context import AppContext
from obdtool.ui.log_viewer import LogViewerDialog
from obdtool.ui.main_window import PAGE_TITLES, MainWindow


def test_main_window_has_all_pages(qtbot: QtBot) -> None:
	window = MainWindow(AppContext(Settings()))
	qtbot.addWidget(window)
	try:
		assert window.nav.count() == len(PAGE_TITLES)
		for key, _title in PAGE_TITLES:
			window.show_page(key)
			assert window.stack.currentWidget() is window.pages[key]
	finally:
		window.shutdown()


def test_demo_connection_updates_ui(qtbot: QtBot) -> None:
	from obdtool.core.service import LinkState

	window = MainWindow(AppContext(Settings()))
	qtbot.addWidget(window)
	try:
		window.start_demo()
		qtbot.waitUntil(lambda: window.ctx.service.state == LinkState.CONNECTED, timeout=20000)
		qtbot.waitUntil(lambda: "Connecté" in window.status.state.text(), timeout=5000)
		page = window.pages["connection"]
		qtbot.waitUntil(lambda: page._info_labels["version"].text().startswith("ELM327"), timeout=5000)
		assert not page.connect_btn.isEnabled()
		assert page.disconnect_btn.isEnabled()
	finally:
		window.shutdown()


def test_log_viewer_opens(qtbot: QtBot) -> None:
	from obdtool.core import log as applog

	applog.setup_logging(console=False)
	dialog = LogViewerDialog()
	qtbot.addWidget(dialog)
	assert dialog.sessions.count() >= 1


def test_live_page_receives_values(qtbot: QtBot) -> None:
	from obdtool.core.service import LinkState

	window = MainWindow(AppContext(Settings()))
	qtbot.addWidget(window)
	try:
		window.start_demo()
		qtbot.waitUntil(lambda: window.ctx.service.state == LinkState.CONNECTED, timeout=20000)
		acq = window.acquisition
		qtbot.waitUntil(lambda: acq.supported is not None, timeout=10000)
		qtbot.waitUntil(lambda: acq.stats.get("RPM") is not None, timeout=10000)
		page = window.pages["live"]
		assert "RPM" in page.model.keys
		page._select_none()
		assert acq.live_selection() == []
		page._select_default()
		assert "RPM" in acq.live_selection()
	finally:
		window.shutdown()


def test_dtc_page_reads_demo_faults(qtbot: QtBot) -> None:
	from obdtool.core.service import LinkState

	settings = Settings()
	settings.connection.demo_scenario = "faults"
	window = MainWindow(AppContext(settings))
	qtbot.addWidget(window)
	try:
		window.start_demo()
		qtbot.waitUntil(lambda: window.ctx.service.state == LinkState.CONNECTED, timeout=20000)
		page = window.pages["dtc"]
		page.read_codes()
		qtbot.waitUntil(lambda: page.codes.rowCount() == 3, timeout=10000)
		assert page.codes.item(0, 0).text() == "P0171"
		assert "ALLUMÉ" in page.mil.text()
		assert page.freeze.rowCount() > 5
		assert page.history_table.rowCount() >= 1
	finally:
		window.shutdown()


def test_charts_overlay_cursor_and_csv(qtbot: QtBot, tmp_path) -> None:
	from obdtool.core.service import LinkState

	settings = Settings()
	settings.extra["charts"] = {"curves": ["RPM", "SPEED"], "mode": "overlay", "window": 30}
	window = MainWindow(AppContext(settings))
	qtbot.addWidget(window)
	try:
		window.start_demo()
		qtbot.waitUntil(lambda: window.ctx.service.state == LinkState.CONNECTED, timeout=20000)
		page = window.pages["charts"]
		window.show_page("charts")
		qtbot.waitUntil(lambda: page._latest_time() is not None, timeout=10000)
		qtbot.wait(1500)
		assert len(page._views) == 2
		page._set_cursor(page._latest_time() - 0.5)
		assert "Régime moteur" in page.cursor_label.text()
		bounds = window.acquisition.store.time_range()
		rows = window.acquisition.store.export_csv(tmp_path / "c.csv", page.curves, *bounds)
		assert rows > 0
		page.mode.setCurrentIndex(page.mode.findData("stacked"))
		assert len(page._plots) == 2
	finally:
		window.shutdown()


def test_record_then_replay(qtbot: QtBot) -> None:
	from obdtool.core import sessions
	from obdtool.core.service import LinkState

	window = MainWindow(AppContext(Settings()))
	qtbot.addWidget(window)
	try:
		window.start_demo()
		qtbot.waitUntil(lambda: window.ctx.service.state == LinkState.CONNECTED, timeout=20000)
		qtbot.waitUntil(lambda: window.acquisition.stats.get("RPM") is not None, timeout=10000)
		path = window.sessions.start_recording("test ui")
		qtbot.wait(1500)
		window.sessions.stop_recording()
		info = sessions.session_info(path)
		assert info.duration > 0.5
		page = window.pages["sessions"]
		page.refresh()
		assert page.table.rowCount() == 1
		window.sessions.load(path)
		window.sessions.set_speed(8.0)
		qtbot.waitUntil(lambda: window.sessions.replay_state == "finished", timeout=10000)
		assert window.acquisition.replaying
		assert window.acquisition.store.get("RPM") is not None
		window.sessions.stop_replay()
		assert not window.acquisition.replaying
	finally:
		window.shutdown()
