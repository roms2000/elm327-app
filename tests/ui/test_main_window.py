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
	assert window.nav.count() == len(PAGE_TITLES)
	for key, _title in PAGE_TITLES:
		window.show_page(key)
		assert window.stack.currentWidget() is window.pages[key]


def test_log_viewer_opens(qtbot: QtBot) -> None:
	from obdtool.core import log as applog

	applog.setup_logging(console=False)
	dialog = LogViewerDialog()
	qtbot.addWidget(dialog)
	assert dialog.sessions.count() >= 1
