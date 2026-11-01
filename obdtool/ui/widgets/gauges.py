"""Jauges dessinées avec QPainter : cadran circulaire, barre et valeur numérique."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from obdtool.core import pids, units
from obdtool.core.dashboard import GaugeConfig

_START_ANGLE = 225.0  # degrés (sens trigonométrique), début du cadran en bas à gauche
_SPAN = 270.0


class GaugeWidget(QWidget):
	"""Jauge configurable ; double-clic → signal d'édition."""

	edit_requested = Signal()

	def __init__(self, config: GaugeConfig, unit_system: str = "metric", parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.config = config
		self.unit_system = unit_system
		self.value: float | str | None = None
		self.error = ""
		self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
		self.setMinimumSize(160, 120 if config.kind != "dial" else 160)

	def sizeHint(self) -> QSize:
		return QSize(260, 220 if self.config.kind == "dial" else 140)

	def set_value(self, value: float | str | None, error: str = "") -> None:
		if value != self.value or error != self.error:
			self.value = value
			self.error = error
			self.update()

	def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
		self.edit_requested.emit()
		super().mouseDoubleClickEvent(event)

	# ---------------------------------------------------------- utilitaires
	def _unit(self) -> str:
		definition = pids.get(self.config.key)
		return definition.unit if definition else ""

	def _converted(self, value: float) -> float:
		converted = units.convert(value, self._unit(), self.unit_system)
		return float(converted) if isinstance(converted, int | float) else value

	def _bounds(self) -> tuple[float, float]:
		low, high = self.config.bounds()
		return self._converted(low), self._converted(high)

	def _text(self) -> str:
		if self.value is None:
			return "—"
		if isinstance(self.value, str):
			return self.value
		unit = self._unit()
		return pids.format_value(self._converted(self.value), unit)

	def _colors(self) -> tuple[QColor, QColor, QColor]:
		palette = self.palette()
		return (
			palette.color(QPalette.ColorRole.Base),
			palette.color(QPalette.ColorRole.Text),
			palette.color(QPalette.ColorRole.PlaceholderText),
		)

	def _value_color(self, default: QColor) -> QColor:
		if isinstance(self.value, int | float) and not isinstance(self.value, bool):
			color = self.config.zone_color(float(self.value))
			if color:
				return QColor(color)
		return default

	# --------------------------------------------------------------- dessin
	def paintEvent(self, _event: object) -> None:
		painter = QPainter(self)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing)
		base, text, muted = self._colors()
		rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
		painter.setPen(QPen(muted.darker(140) if base.lightness() < 128 else muted.lighter(150), 1))
		painter.setBrush(base)
		painter.drawRoundedRect(rect, 8, 8)
		inner = rect.adjusted(10, 8, -10, -8)
		title_font = QFont(self.font())
		title_font.setPointSizeF(max(8.0, min(12.0, inner.height() / 16)))
		painter.setFont(title_font)
		painter.setPen(muted)
		title_height = painter.fontMetrics().height() + 4
		painter.drawText(
			QRectF(inner.left(), inner.top(), inner.width(), title_height),
			Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
			painter.fontMetrics().elidedText(self.config.title(), Qt.TextElideMode.ElideRight, int(inner.width())),
		)
		body = QRectF(inner.left(), inner.top() + title_height, inner.width(), inner.height() - title_height)
		if self.config.kind == "dial":
			self._paint_dial(painter, body, text, muted)
		elif self.config.kind == "bar":
			self._paint_bar(painter, body, text, muted)
		else:
			self._paint_value(painter, body, text, muted)
		painter.end()

	def _value_font(self, height: float, factor: float) -> QFont:
		font = QFont(self.font())
		font.setPointSizeF(min(44.0, max(10.0, height * factor)))
		font.setBold(True)
		return font

	def _paint_unit_and_error(self, painter: QPainter, area: QRectF, muted: QColor) -> None:
		unit = units.display_unit(self._unit(), self.unit_system)
		small = QFont(self.font())
		small.setPointSizeF(min(12.0, max(8.0, area.height() / 12)))
		painter.setFont(small)
		painter.setPen(muted)
		label = self.error if self.error else unit
		painter.drawText(area, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, label)

	def _paint_dial(self, painter: QPainter, area: QRectF, text: QColor, muted: QColor) -> None:
		size = min(area.width(), area.height() * 1.15)
		center = QPointF(area.center().x(), area.top() + size / 2)
		radius = size / 2 - 6
		arc = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
		low, high = self._bounds()
		thickness = max(6.0, radius * 0.12)

		def angle_for(value: float) -> float:
			ratio = (value - low) / (high - low)
			return _START_ANGLE - _SPAN * min(max(ratio, 0.0), 1.0)

		track = QPen(muted, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
		track.setColor(QColor(muted.red(), muted.green(), muted.blue(), 70))
		painter.setPen(track)
		painter.drawArc(arc, int(_START_ANGLE * 16), int(-_SPAN * 16))
		for zone in self.config.zones:
			start = angle_for(self._converted(zone.start))
			end = angle_for(self._converted(zone.end))
			color = QColor(self.config.zone_color((zone.start + zone.end) / 2) or "#d03b3b")
			color.setAlpha(170)
			painter.setPen(QPen(color, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
			painter.drawArc(arc, int(start * 16), int((end - start) * 16))
		# Graduations principales et libellés des bornes.
		painter.setPen(QPen(muted, 1.5))
		for step in range(11):
			angle = math.radians(_START_ANGLE - _SPAN * step / 10)
			outer = radius - thickness / 2 - 2
			inner_r = outer - (8 if step % 5 == 0 else 4)
			painter.drawLine(
				QPointF(center.x() + inner_r * math.cos(angle), center.y() - inner_r * math.sin(angle)),
				QPointF(center.x() + outer * math.cos(angle), center.y() - outer * math.sin(angle)),
			)
		small = QFont(self.font())
		small.setPointSizeF(min(11.0, max(7.0, radius / 11)))
		painter.setFont(small)
		for value, angle_deg in ((low, _START_ANGLE), (high, _START_ANGLE - _SPAN)):
			angle = math.radians(angle_deg)
			r = radius - thickness - 18
			point = QPointF(center.x() + r * math.cos(angle), center.y() - r * math.sin(angle))
			painter.drawText(QRectF(point.x() - 30, point.y() - 8, 60, 16), Qt.AlignmentFlag.AlignCenter, f"{value:g}")
		# Aiguille.
		if isinstance(self.value, int | float) and not isinstance(self.value, bool):
			angle = math.radians(angle_for(self._converted(float(self.value))))
			needle = QColor(self._value_color(QColor("#3d7fd6")))
			painter.setPen(QPen(needle, max(2.5, radius / 40), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
			tip = QPointF(
				center.x() + (radius - thickness) * math.cos(angle), center.y() - (radius - thickness) * math.sin(angle)
			)
			painter.drawLine(center, tip)
			painter.setBrush(needle)
			painter.drawEllipse(center, radius * 0.06, radius * 0.06)
		painter.setFont(self._value_font(radius, 0.28))
		painter.setPen(text)
		value_rect = QRectF(center.x() - radius, center.y() + radius * 0.18, 2 * radius, radius * 0.45)
		painter.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, self._text())
		self._paint_unit_and_error(
			painter, QRectF(area.left(), area.top(), area.width(), min(area.height(), size)), muted
		)

	def _paint_bar(self, painter: QPainter, area: QRectF, text: QColor, muted: QColor) -> None:
		low, high = self._bounds()
		painter.setFont(self._value_font(area.height(), 0.22))
		painter.setPen(text)
		value_height = area.height() * 0.42
		painter.drawText(
			QRectF(area.left(), area.top(), area.width(), value_height), Qt.AlignmentFlag.AlignCenter, self._text()
		)
		bar = QRectF(
			area.left(), area.top() + value_height + 4, area.width(), min(28.0, max(10.0, area.height() * 0.14))
		)
		track = QColor(muted.red(), muted.green(), muted.blue(), 60)
		painter.setPen(Qt.PenStyle.NoPen)
		painter.setBrush(track)
		painter.drawRoundedRect(bar, 4, 4)
		for zone in self.config.zones:
			x0 = bar.left() + bar.width() * min(max((self._converted(zone.start) - low) / (high - low), 0), 1)
			x1 = bar.left() + bar.width() * min(max((self._converted(zone.end) - low) / (high - low), 0), 1)
			color = QColor(self.config.zone_color((zone.start + zone.end) / 2) or "#d03b3b")
			color.setAlpha(60)
			painter.setBrush(color)
			painter.drawRect(QRectF(x0, bar.top(), x1 - x0, bar.height()))
		if isinstance(self.value, int | float) and not isinstance(self.value, bool):
			ratio = min(max((self._converted(float(self.value)) - low) / (high - low), 0.0), 1.0)
			fill = QRectF(bar.left(), bar.top(), bar.width() * ratio, bar.height())
			path = QPainterPath()
			path.addRoundedRect(fill, 4, 4)
			painter.setBrush(self._value_color(QColor("#3d7fd6")))
			painter.drawPath(path)
		small = QFont(self.font())
		small.setPointSizeF(min(10.0, max(7.0, area.height() / 14)))
		painter.setFont(small)
		painter.setPen(muted)
		labels = QRectF(bar.left(), bar.bottom() + 2, bar.width(), painter.fontMetrics().height())
		painter.drawText(labels, Qt.AlignmentFlag.AlignLeft, f"{low:g}")
		painter.drawText(labels, Qt.AlignmentFlag.AlignRight, f"{high:g}")
		self._paint_unit_and_error(painter, area, muted)

	def _paint_value(self, painter: QPainter, area: QRectF, text: QColor, muted: QColor) -> None:
		factor = 0.32 if not isinstance(self.value, str) else 0.12
		painter.setFont(self._value_font(area.height(), factor))
		painter.setPen(self._value_color(text))
		label = self._text()
		metrics = painter.fontMetrics()
		painter.drawText(
			area.adjusted(0, 0, 0, -metrics.height() * 0.3),
			Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
			label,
		)
		self._paint_unit_and_error(painter, area, muted)
