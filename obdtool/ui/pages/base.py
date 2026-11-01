"""Classe de base des pages."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class BasePage(QWidget):
	"""Page avec un titre, un sous-titre et une zone de contenu."""

	title = "Page"
	subtitle = ""

	def __init__(self, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self._layout = QVBoxLayout(self)
		self._layout.setContentsMargins(16, 12, 16, 12)
		title = QLabel(self.title)
		title.setObjectName("pageTitle")
		self._layout.addWidget(title)
		if self.subtitle:
			sub = QLabel(self.subtitle)
			sub.setObjectName("pageSubtitle")
			sub.setWordWrap(True)
			self._layout.addWidget(sub)

	@property
	def content(self) -> QVBoxLayout:
		"""Disposition dans laquelle ajouter le contenu de la page."""
		return self._layout

	def page_shown(self) -> None:
		"""Appelée quand la page devient visible."""

	def page_hidden(self) -> None:
		"""Appelée quand la page est masquée."""


class PlaceholderPage(BasePage):
	"""Page en attente d'implémentation."""

	def __init__(self, title: str, parent: QWidget | None = None) -> None:
		self.title = title
		super().__init__(parent)
		label = QLabel("Cet écran sera disponible dans une prochaine étape.")
		label.setObjectName("pageSubtitle")
		self.content.addWidget(label)
		self.content.addStretch(1)
