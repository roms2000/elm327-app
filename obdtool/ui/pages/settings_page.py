"""Page Paramètres : interface, unités, journaux."""

from __future__ import annotations

from PySide6.QtWidgets import (
	QCheckBox,
	QComboBox,
	QDoubleSpinBox,
	QFormLayout,
	QGroupBox,
	QHBoxLayout,
	QLabel,
	QPushButton,
	QSpinBox,
	QWidget,
)

from obdtool.core import log as applog
from obdtool.ui.context import AppContext
from obdtool.ui.log_viewer import LogViewerDialog, confirm_delete_old_logs, open_log_folder
from obdtool.ui.pages.base import BasePage

_THEMES = [("system", "Selon le système"), ("light", "Clair"), ("dark", "Sombre")]
_UNITS = [("metric", "Métrique (km/h, °C, kPa)"), ("imperial", "Impérial (mph, °F, psi)")]
_LEVELS = [
	("DEBUG", "Débogage (trames brutes)"),
	("INFO", "Information"),
	("WARNING", "Avertissements"),
	("ERROR", "Erreurs"),
]


class SettingsPage(BasePage):
	"""Réglages généraux de l'application."""

	title = "Paramètres"
	subtitle = "Les réglages sont enregistrés dans ~/.config/obdtool/config.toml."

	def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		s = ctx.settings

		general = QGroupBox("Interface")
		form = QFormLayout(general)
		self.theme = _combo(_THEMES, s.general.theme)
		self.theme.currentIndexChanged.connect(self._theme_changed)
		form.addRow("Thème :", self.theme)
		self.units = _combo(_UNITS, s.general.units)
		self.units.currentIndexChanged.connect(self._save)
		form.addRow("Unités :", self.units)
		self.sound = QCheckBox("Signal sonore lors des alertes")
		self.sound.setChecked(s.general.alert_sound)
		self.sound.toggled.connect(self._save)
		form.addRow("", self.sound)
		self.content.addWidget(general)

		logs = QGroupBox("Journaux")
		lform = QFormLayout(logs)
		self.level = _combo(_LEVELS, s.logs.level)
		self.level.currentIndexChanged.connect(self._level_changed)
		lform.addRow("Niveau de détail :", self.level)
		self.keep = QSpinBox()
		self.keep.setRange(1, 200)
		self.keep.setValue(s.logs.keep_sessions)
		self.keep.setSuffix(" sessions")
		self.keep.valueChanged.connect(self._save)
		lform.addRow("Conserver au plus :", self.keep)
		self.max_mb = QDoubleSpinBox()
		self.max_mb.setRange(1, 5000)
		self.max_mb.setDecimals(0)
		self.max_mb.setValue(s.logs.max_total_mb)
		self.max_mb.setSuffix(" Mo")
		self.max_mb.valueChanged.connect(self._save)
		lform.addRow("Taille totale maximale :", self.max_mb)
		self.usage = QLabel()
		lform.addRow("Espace utilisé :", self.usage)
		hint = QLabel(
			"Un nouveau fichier est créé à chaque lancement ; les sessions les plus anciennes "
			"sont supprimées au démarrage au-delà des limites ci-dessus."
		)
		hint.setWordWrap(True)
		hint.setObjectName("pageSubtitle")
		lform.addRow(hint)
		row = QHBoxLayout()
		view = QPushButton("Voir les journaux…")
		view.clicked.connect(self._view_logs)
		folder = QPushButton("Ouvrir le dossier")
		folder.clicked.connect(open_log_folder)
		delete = QPushButton("Supprimer les anciens journaux")
		delete.clicked.connect(self._delete_logs)
		for button in (view, folder, delete):
			row.addWidget(button)
		row.addStretch(1)
		lform.addRow(row)
		self.content.addWidget(logs)
		self.content.addStretch(1)

	def page_shown(self) -> None:
		self._update_usage()

	def _update_usage(self) -> None:
		self.usage.setText(applog.format_size(applog.total_size()))

	def _save(self, *_args: object) -> None:
		s = self.ctx.settings
		s.general.theme = self.theme.currentData()
		s.general.units = self.units.currentData()
		s.general.alert_sound = self.sound.isChecked()
		s.logs.level = self.level.currentData()
		s.logs.keep_sessions = self.keep.value()
		s.logs.max_total_mb = self.max_mb.value()
		self.ctx.save_settings()

	def _theme_changed(self) -> None:
		self._save()
		self.ctx.theme_changed.emit(self.theme.currentData())

	def _level_changed(self) -> None:
		self._save()
		applog.set_level(self.level.currentData())

	def _view_logs(self) -> None:
		LogViewerDialog(self).exec()
		self._update_usage()

	def _delete_logs(self) -> None:
		confirm_delete_old_logs(self)
		self._update_usage()


def _combo(items: list[tuple[str, str]], current: str) -> QComboBox:
	combo = QComboBox()
	for key, label in items:
		combo.addItem(label, key)
	index = combo.findData(current)
	combo.setCurrentIndex(max(index, 0))
	return combo
