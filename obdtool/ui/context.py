"""Contexte partagé entre les pages : réglages et notifications globales."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from obdtool.core.settings import Settings


class AppContext(QObject):
	"""Objet unique transmis aux pages."""

	settings_changed = Signal()
	theme_changed = Signal(str)

	def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
		super().__init__(parent)
		self.settings = settings

	def save_settings(self) -> None:
		"""Enregistre les réglages et prévient les abonnés."""
		self.settings.save()
		self.settings_changed.emit()
