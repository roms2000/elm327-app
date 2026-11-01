"""Thèmes clair et sombre (style Fusion)."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_dark_active = False


def is_dark() -> bool:
	"""Indique si le thème sombre est actif."""
	return _dark_active


def apply_theme(app: QApplication, theme: str) -> None:
	"""Applique le thème `system`, `light` ou `dark`."""
	global _dark_active
	app.setStyle("Fusion")
	dark = theme == "dark"
	if theme == "system":
		dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
	_dark_active = dark
	app.setPalette(_dark_palette() if dark else _light_palette())
	pg.setConfigOptions(
		antialias=True, background="#1e1f22" if dark else "w", foreground="#d0d0d0" if dark else "#303030"
	)
	app.setStyleSheet(_STYLE)


def _light_palette() -> QPalette:
	pal = QPalette()
	colors = {
		QPalette.ColorRole.Window: "#f3f3f3",
		QPalette.ColorRole.WindowText: "#1c1c1c",
		QPalette.ColorRole.Base: "#ffffff",
		QPalette.ColorRole.AlternateBase: "#f0f3f7",
		QPalette.ColorRole.ToolTipBase: "#ffffe0",
		QPalette.ColorRole.ToolTipText: "#1c1c1c",
		QPalette.ColorRole.Text: "#1c1c1c",
		QPalette.ColorRole.Button: "#e8e8e8",
		QPalette.ColorRole.ButtonText: "#1c1c1c",
		QPalette.ColorRole.Highlight: "#2f6fd6",
		QPalette.ColorRole.HighlightedText: "#ffffff",
		QPalette.ColorRole.Link: "#1f5fbf",
		QPalette.ColorRole.PlaceholderText: "#808080",
	}
	for role, color in colors.items():
		pal.setColor(role, QColor(color))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#a0a0a0"))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#a0a0a0"))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#a0a0a0"))
	return pal


def _dark_palette() -> QPalette:
	pal = QPalette()
	colors = {
		QPalette.ColorRole.Window: "#2b2d30",
		QPalette.ColorRole.WindowText: "#dfe1e5",
		QPalette.ColorRole.Base: "#1e1f22",
		QPalette.ColorRole.AlternateBase: "#26282b",
		QPalette.ColorRole.ToolTipBase: "#3c3f41",
		QPalette.ColorRole.ToolTipText: "#dfe1e5",
		QPalette.ColorRole.Text: "#dfe1e5",
		QPalette.ColorRole.Button: "#3c3f41",
		QPalette.ColorRole.ButtonText: "#dfe1e5",
		QPalette.ColorRole.Highlight: "#3d6fc4",
		QPalette.ColorRole.HighlightedText: "#ffffff",
		QPalette.ColorRole.Link: "#6ea8ff",
		QPalette.ColorRole.PlaceholderText: "#7f8288",
	}
	for role, color in colors.items():
		pal.setColor(role, QColor(color))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6f7277"))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6f7277"))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#6f7277"))
	return pal


# Couleurs d'état, lisibles dans les deux thèmes.
COLOR_OK = "#2e9d4f"
COLOR_WARN = "#d58b00"
COLOR_ERROR = "#d03b3b"
COLOR_INFO = "#3d7fd6"
COLOR_MUTED = "#8a8d93"

_STYLE = """
QListWidget#navigation { border: none; font-size: 11pt; }
QListWidget#navigation::item { padding: 8px 10px; }
QLabel#pageTitle { font-size: 16pt; font-weight: 600; }
QLabel#pageSubtitle { color: palette(placeholder-text); }
QGroupBox { font-weight: 600; }
QLabel.badge { border-radius: 4px; padding: 2px 6px; }
"""
