"""Thèmes clair et sombre (style Fusion)."""

from __future__ import annotations

from pathlib import Path

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
		antialias=True, background="#1a1a19" if dark else "#fcfcfb", foreground="#c3c2b7" if dark else "#52514e"
	)
	app.setStyleSheet(_STYLE + _indicator_style(dark))


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
		# Rôles utilisés par Fusion pour les contours (cases à cocher, cadres) : à éclaircir en sombre.
		QPalette.ColorRole.Light: "#5c6066",
		QPalette.ColorRole.Midlight: "#4a4d52",
		QPalette.ColorRole.Mid: "#6a6e75",
		QPalette.ColorRole.Dark: "#7a7e85",
		QPalette.ColorRole.Shadow: "#101113",
	}
	for role, color in colors.items():
		pal.setColor(role, QColor(color))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6f7277"))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6f7277"))
	pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#6f7277"))
	return pal


# Palette catégorielle des courbes (ordre fixe, validée daltonisme ; variantes claire / sombre).
SERIES_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
SERIES_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767")


def series_color(index: int) -> str:
	"""Couleur de la courbe n° `index` (0 à 7) pour le thème actif."""
	palette = SERIES_DARK if _dark_active else SERIES_LIGHT
	return palette[index % len(palette)]


def text_color() -> str:
	"""Couleur du texte des graphiques."""
	return "#c3c2b7" if _dark_active else "#52514e"


def grid_alpha() -> float:
	return 0.18 if _dark_active else 0.25


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


_RESOURCES = Path(__file__).parent / "resources"


def _indicator_style(dark: bool) -> str:
	"""Cases à cocher lisibles dans les deux thèmes (Fusion les rend presque invisibles en sombre)."""
	border = "#8a8d93" if dark else "#8c8c8c"
	base = "#1e1f22" if dark else "#ffffff"
	accent = "#3d6fc4" if dark else "#2f6fd6"
	check = (_RESOURCES / "check.svg").as_posix()
	partial = (_RESOURCES / "partial.svg").as_posix()
	selectors = (
		"QCheckBox::indicator, QTreeView::indicator, QListView::indicator, QTableView::indicator, QGroupBox::indicator"
	)
	checked = ", ".join(f"{s.strip()}:checked" for s in selectors.split(","))
	indeterminate = ", ".join(f"{s.strip()}:indeterminate" for s in selectors.split(","))
	disabled = ", ".join(f"{s.strip()}:disabled" for s in selectors.split(","))
	return f"""
{selectors} {{ width: 13px; height: 13px; border: 1px solid {border}; border-radius: 3px; background: {base}; }}
{checked} {{ background: {accent}; border-color: {accent}; image: url({check}); }}
{indeterminate} {{ background: {accent}; border-color: {accent}; image: url({partial}); }}
{disabled} {{ border-color: {COLOR_MUTED}; }}
"""
