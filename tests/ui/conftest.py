"""Les boîtes de dialogue modales bloqueraient les tests : elles sont journalisées au lieu d'être affichées."""

from __future__ import annotations

import logging

import pytest
from PySide6.QtWidgets import QMessageBox

log = logging.getLogger("tests.ui")


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
	shown: list[tuple[str, str]] = []

	def fake(kind: str, answer: QMessageBox.StandardButton):
		def _show(
			_parent: object, title: str, text: str, *args: object, **kwargs: object
		) -> QMessageBox.StandardButton:
			shown.append((title, text))
			log.warning("Boîte %s ignorée : %s — %s", kind, title, text)
			return answer

		return _show

	monkeypatch.setattr(QMessageBox, "warning", fake("warning", QMessageBox.StandardButton.Ok))
	monkeypatch.setattr(QMessageBox, "critical", fake("critical", QMessageBox.StandardButton.Ok))
	monkeypatch.setattr(QMessageBox, "information", fake("information", QMessageBox.StandardButton.Ok))
	monkeypatch.setattr(QMessageBox, "question", fake("question", QMessageBox.StandardButton.No))
	return shown
