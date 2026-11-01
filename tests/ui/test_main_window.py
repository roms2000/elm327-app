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
