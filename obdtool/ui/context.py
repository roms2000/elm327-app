"""Contexte partagé entre les pages : réglages, service de diagnostic et notifications globales."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from obdtool.core.service import DiagnosticService
from obdtool.core.settings import Settings
from obdtool.ui.bridge import ServiceBridge


class AppContext(QObject):
	"""Objet unique transmis aux pages."""

	settings_changed = Signal()
	theme_changed = Signal(str)
	status_message = Signal(str, int)  # texte, durée en ms

	def __init__(
		self, settings: Settings, service: DiagnosticService | None = None, parent: QObject | None = None
	) -> None:
		super().__init__(parent)
		self.settings = settings
		self.service = service or DiagnosticService()
		self.bridge = ServiceBridge(self.service, self)

	def save_settings(self) -> None:
		"""Enregistre les réglages et prévient les abonnés."""
		self.settings.save()
		self.settings_changed.emit()

	def notify(self, text: str, timeout_ms: int = 5000) -> None:
		"""Affiche un message temporaire dans la barre d'état."""
		self.status_message.emit(text, timeout_ms)

	def shutdown(self) -> None:
		"""Arrête proprement le service de diagnostic."""
		self.service.shutdown()
