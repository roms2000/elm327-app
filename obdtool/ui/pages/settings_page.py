"""Page Paramètres : interface, unités, journaux."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
	QCheckBox,
	QComboBox,
	QDoubleSpinBox,
	QFormLayout,
	QGroupBox,
	QHBoxLayout,
	QHeaderView,
	QLabel,
	QPushButton,
	QSpinBox,
	QTableWidget,
	QTableWidgetItem,
	QVBoxLayout,
	QWidget,
)

from obdtool.core import log as applog
from obdtool.core import pids
from obdtool.core.alerts import AlertRule, default_rules
from obdtool.ui.alert_control import AlertController
from obdtool.ui.context import AppContext
from obdtool.ui.log_viewer import LogViewerDialog, confirm_delete_old_logs, open_log_folder
from obdtool.ui.pages.base import BasePage
from obdtool.ui.qtcompat import emit

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

	def __init__(self, ctx: AppContext, alerts: AlertController | None = None, parent: QWidget | None = None) -> None:
		super().__init__(parent)
		self.ctx = ctx
		self.alerts = alerts
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
		if alerts is not None:
			self.content.addWidget(self._build_alerts())
		self.content.addStretch(1)

	# ---------------------------------------------------------------- alertes
	def _build_alerts(self) -> QGroupBox:
		box = QGroupBox("Alertes")
		layout = QVBoxLayout(box)
		hint = QLabel(
			"Une alerte se déclenche quand la valeur dépasse le seuil pendant la durée indiquée ; elle cesse "
			"quand la valeur revient en deçà du seuil diminué de la marge. Les valeurs surveillées sont interrogées "
			"en permanence."
		)
		hint.setWordWrap(True)
		hint.setObjectName("pageSubtitle")
		layout.addWidget(hint)
		self.alert_table = QTableWidget(0, 7)
		self.alert_table.setHorizontalHeaderLabels(
			["Active", "Valeur", "Condition", "Seuil", "Durée (s)", "Marge", "Son"]
		)
		self.alert_table.verticalHeader().hide()
		header = self.alert_table.horizontalHeader()
		header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
		header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
		self.alert_table.setMinimumHeight(170)
		layout.addWidget(self.alert_table)
		row = QHBoxLayout()
		add = QPushButton("Ajouter une alerte")
		add.clicked.connect(lambda: self._add_alert_row(AlertRule("RPM", ">", 6000.0)))
		remove = QPushButton("Supprimer")
		remove.clicked.connect(lambda: self.alert_table.removeRow(self.alert_table.currentRow()))
		save = QPushButton("Appliquer")
		save.clicked.connect(self._apply_alerts)
		defaults = QPushButton("Valeurs par défaut")
		defaults.clicked.connect(self._default_alerts)
		for button in (add, remove, save, defaults):
			row.addWidget(button)
		row.addStretch(1)
		layout.addLayout(row)
		for rule in self.alerts.rules if self.alerts else []:
			self._add_alert_row(rule)
		return box

	def _add_alert_row(self, rule: AlertRule) -> None:
		table = self.alert_table
		row = table.rowCount()
		table.insertRow(row)
		enabled = QCheckBox()
		enabled.setChecked(rule.enabled)
		table.setCellWidget(row, 0, enabled)
		key = QComboBox()
		for definition in sorted(pids.CATALOG.values(), key=lambda d: d.name):
			if definition.numeric:
				key.addItem(f"{definition.name} ({definition.unit})", definition.key)
		key.setCurrentIndex(max(key.findData(rule.key), 0))
		table.setCellWidget(row, 1, key)
		operator = QComboBox()
		operator.addItem("supérieure à", ">")
		operator.addItem("inférieure à", "<")
		operator.setCurrentIndex(0 if rule.operator == ">" else 1)
		table.setCellWidget(row, 2, operator)
		for column, value, decimals, maximum in (
			(3, rule.threshold, 2, 100000.0),
			(4, rule.delay_s, 1, 600.0),
			(5, rule.hysteresis, 2, 10000.0),
		):
			spin = QDoubleSpinBox()
			spin.setRange(-100000.0 if column == 3 else 0.0, maximum)
			spin.setDecimals(decimals)
			spin.setValue(value)
			table.setCellWidget(row, column, spin)
		sound = QCheckBox()
		sound.setChecked(rule.sound)
		table.setCellWidget(row, 6, sound)
		item = QTableWidgetItem()
		item.setData(Qt.ItemDataRole.UserRole, rule.message)
		table.setItem(row, 1, item)

	def _apply_alerts(self) -> None:
		if self.alerts is None:
			return
		rules = []
		table = self.alert_table
		for row in range(table.rowCount()):
			item = table.item(row, 1)
			key = str(table.cellWidget(row, 1).currentData())
			message = item.data(Qt.ItemDataRole.UserRole) if item else ""
			previous = next((r for r in self.alerts.rules if r.key == key and r.message == message), None)
			rules.append(
				AlertRule(
					key=key,
					operator=str(table.cellWidget(row, 2).currentData()),
					threshold=table.cellWidget(row, 3).value(),
					enabled=table.cellWidget(row, 0).isChecked(),
					delay_s=table.cellWidget(row, 4).value(),
					hysteresis=table.cellWidget(row, 5).value(),
					sound=table.cellWidget(row, 6).isChecked(),
					message=message if previous else "",
				)
			)
		self.alerts.set_rules(rules)
		self.ctx.notify(f"{len(rules)} alerte(s) enregistrée(s).")

	def _default_alerts(self) -> None:
		self.alert_table.setRowCount(0)
		for rule in default_rules():
			self._add_alert_row(rule)

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
		emit(self.ctx.theme_changed, self.theme.currentData())

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
