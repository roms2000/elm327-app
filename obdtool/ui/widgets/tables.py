"""Aides pour remplir des tableaux en lecture seule."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem


def make_table(headers: Sequence[str], stretch: int = 0) -> QTableWidget:
	"""Tableau en lecture seule, colonnes ajustées au contenu sauf `stretch`."""
	table = QTableWidget(0, len(headers))
	table.setHorizontalHeaderLabels(list(headers))
	table.verticalHeader().hide()
	table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
	table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
	table.setAlternatingRowColors(True)
	header = table.horizontalHeader()
	header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
	header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
	return table


def add_row(
	table: QTableWidget, values: Sequence[object], color: str | None = None, color_column: int | None = None
) -> int:
	"""Ajoute une ligne ; `color` s'applique à `color_column` (ou à toute la ligne)."""
	row = table.rowCount()
	table.insertRow(row)
	for column, value in enumerate(values):
		item = QTableWidgetItem("" if value is None else str(value))
		if color and (color_column is None or color_column == column):
			item.setForeground(QBrush(QColor(color)))
		table.setItem(row, column, item)
	return row
