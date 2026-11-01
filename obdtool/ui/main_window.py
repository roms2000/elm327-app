"""Fenêtre principale : navigation latérale et pages."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
	QApplication,
	QHBoxLayout,
	QListWidget,
	QListWidgetItem,
	QMainWindow,
	QMessageBox,
	QStackedWidget,
	QVBoxLayout,
	QWidget,
)

from obdtool import APP_DISPLAY_NAME, __version__
from obdtool.core import transport
from obdtool.core.service import ErrorEvent
from obdtool.ui import theme
from obdtool.ui.acquisition import AcquisitionController
from obdtool.ui.alert_control import AlertBanner, AlertController
from obdtool.ui.context import AppContext
from obdtool.ui.log_viewer import LogViewerDialog, open_log_folder
from obdtool.ui.pages.base import BasePage, PlaceholderPage
from obdtool.ui.pages.charts_page import ChartsPage
from obdtool.ui.pages.connection_page import ConnectionPage
from obdtool.ui.pages.dashboard_page import DashboardPage
from obdtool.ui.pages.dtc_page import DtcPage
from obdtool.ui.pages.live_page import LivePage
from obdtool.ui.pages.monitors_page import MonitorsPage
from obdtool.ui.pages.sessions_page import SessionsPage
from obdtool.ui.pages.settings_page import SettingsPage
from obdtool.ui.pages.tests_page import TestsPage
from obdtool.ui.pages.vehicle_page import VehiclePage
from obdtool.ui.session_control import SessionController
from obdtool.ui.status_bar import AppStatusBar

log = logging.getLogger(__name__)

PAGE_TITLES = [
	("connection", "Connexion"),
	("dashboard", "Tableau de bord"),
	("live", "Valeurs"),
	("charts", "Graphiques"),
	("dtc", "Défauts"),
	("monitors", "Moniteurs"),
	("tests", "Tests embarqués"),
	("vehicle", "Infos véhicule"),
	("performance", "Performances"),
	("sessions", "Sessions"),
	("console", "Console"),
	("settings", "Paramètres"),
]


class MainWindow(QMainWindow):
	"""Fenêtre principale de l'application."""

	def __init__(self, ctx: AppContext) -> None:
		super().__init__()
		self.ctx = ctx
		self.setWindowTitle(f"{APP_DISPLAY_NAME} — diagnostic OBD-II")
		self.resize(1280, 800)
		self.pages: dict[str, BasePage] = {}
		self.acquisition = AcquisitionController(ctx, self)
		self.sessions = SessionController(ctx, self.acquisition, self)
		self.alerts = AlertController(ctx, self.acquisition, self)

		central = QWidget()
		outer = QVBoxLayout(central)
		outer.setContentsMargins(0, 0, 0, 0)
		outer.setSpacing(0)
		self.banner = AlertBanner(self.alerts)
		outer.addWidget(self.banner)
		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(0)
		outer.addLayout(layout, 1)
		self.nav = QListWidget()
		self.nav.setObjectName("navigation")
		self.nav.setFixedWidth(190)
		self.nav.setIconSize(QSize(18, 18))
		self.stack = QStackedWidget()
		layout.addWidget(self.nav)
		layout.addWidget(self.stack, 1)
		self.setCentralWidget(central)

		for key, title in PAGE_TITLES:
			self.add_page(key, title, self._create_page(key, title))
		self.nav.currentRowChanged.connect(self._switch_page)
		self.nav.setCurrentRow(0)
		self.status = AppStatusBar(ctx, self)
		self.setStatusBar(self.status)
		self.acquisition.rates_changed.connect(lambda q, _c: self.status.set_rate(q))
		self.sessions.recording_changed.connect(self.status.set_recording)
		self.sessions.replay_changed.connect(self._on_replay_state)
		self._build_menu()
		ctx.theme_changed.connect(self._apply_theme)
		ctx.bridge.error.connect(self._on_error)

	def _create_page(self, key: str, title: str) -> BasePage:
		if key == "settings":
			return SettingsPage(self.ctx, self.alerts)
		if key == "dashboard":
			return DashboardPage(self.ctx, self.acquisition)
		if key == "connection":
			return ConnectionPage(self.ctx)
		if key == "live":
			return LivePage(self.ctx, self.acquisition)
		if key == "dtc":
			return DtcPage(self.ctx)
		if key == "charts":
			return ChartsPage(self.ctx, self.acquisition)
		if key == "monitors":
			return MonitorsPage(self.ctx)
		if key == "tests":
			return TestsPage(self.ctx)
		if key == "vehicle":
			return VehiclePage(self.ctx)
		if key == "sessions":
			return SessionsPage(self.ctx, self.sessions)
		return PlaceholderPage(title)

	def add_page(self, key: str, title: str, page: BasePage) -> None:
		"""Ajoute une page à la navigation."""
		self.pages[key] = page
		self.stack.addWidget(page)
		item = QListWidgetItem(title)
		item.setData(Qt.ItemDataRole.UserRole, key)
		self.nav.addItem(item)

	def show_page(self, key: str) -> None:
		"""Affiche la page identifiée par `key`."""
		for row in range(self.nav.count()):
			if self.nav.item(row).data(Qt.ItemDataRole.UserRole) == key:
				self.nav.setCurrentRow(row)
				return

	def _switch_page(self, row: int) -> None:
		previous = self.stack.currentWidget()
		if isinstance(previous, BasePage):
			previous.page_hidden()
		self.stack.setCurrentIndex(row)
		current = self.stack.currentWidget()
		if isinstance(current, BasePage):
			current.page_shown()

	def _build_menu(self) -> None:
		bar = self.menuBar()
		file_menu = bar.addMenu("&Fichier")
		self.record_action = QAction("Démarrer l'enregistrement", self)
		self.record_action.setShortcut(QKeySequence("Ctrl+R"))
		self.record_action.triggered.connect(self.toggle_recording)
		file_menu.addAction(self.record_action)
		file_menu.addSeparator()
		quit_action = QAction("&Quitter", self)
		quit_action.setShortcut(QKeySequence.StandardKey.Quit)
		quit_action.triggered.connect(self.close)
		file_menu.addAction(quit_action)

		view_menu = bar.addMenu("&Affichage")
		full = QAction("Plein écran", self)
		full.setShortcut(QKeySequence("F11"))
		full.triggered.connect(self.toggle_fullscreen)
		view_menu.addAction(full)

		help_menu = bar.addMenu("&Aide")
		logs = QAction("Journaux…", self)
		logs.triggered.connect(lambda: LogViewerDialog(self).exec())
		help_menu.addAction(logs)
		folder = QAction("Ouvrir le dossier des journaux", self)
		folder.triggered.connect(open_log_folder)
		help_menu.addAction(folder)
		help_menu.addSeparator()
		about = QAction("À propos", self)
		about.triggered.connect(self._about)
		help_menu.addAction(about)

	def toggle_recording(self) -> None:
		"""Démarre ou arrête l'enregistrement de session."""
		if self.sessions.recording:
			self.sessions.stop_recording()
		elif self.ctx.service.link.car_connected and self.sessions.replay is None:
			self.sessions.start_recording()
		else:
			self.ctx.notify("Enregistrement impossible : pas de connexion au véhicule (ou relecture en cours).")
		self.record_action.setText(
			"Arrêter l'enregistrement" if self.sessions.recording else "Démarrer l'enregistrement"
		)

	def _on_replay_state(self, state: str) -> None:
		if state == "stopped":
			self.status.set_recording(self.sessions.recording)
		elif self.sessions.replay is not None:
			self.status.recording.setText(f"▶ Relecture : {self.sessions.replay.path.stem}")

	def toggle_fullscreen(self) -> None:
		"""Bascule le mode plein écran."""
		if self.isFullScreen():
			self.showNormal()
		else:
			self.showFullScreen()

	def _apply_theme(self, name: str) -> None:
		app = QApplication.instance()
		if isinstance(app, QApplication):
			theme.apply_theme(app, name)

	def _about(self) -> None:
		QMessageBox.about(
			self,
			f"À propos de {APP_DISPLAY_NAME}",
			f"<b>{APP_DISPLAY_NAME} {__version__}</b><br>"
			"Diagnostic OBD-II générique pour adaptateurs ELM327.<br><br>"
			"Licence GPL-3.0-or-later. Utilise python-OBD, PySide6 et pyqtgraph.<br>"
			"<i>Ne manipulez pas l'application en conduisant.</i>",
		)

	def start_demo(self) -> None:
		"""Se connecte directement à l'émulateur (option --demo)."""
		page = self.pages["connection"]
		assert isinstance(page, ConnectionPage)
		self.ctx.settings.connection.kind = "demo"
		for button in page.kind_group.buttons():
			button.setChecked(button.property("kind") == "demo")
		page.start_connection()

	def check_serial_access(self) -> None:
		"""Au démarrage, signale un port série inaccessible et explique comment corriger."""
		conn = self.ctx.settings.connection
		if conn.kind != "serial" or not conn.port.startswith("/dev/"):
			return
		access = transport.check_port_access(conn.port)
		if access.exists and not access.ok:
			log.warning("Port %s inaccessible : %s", conn.port, access.advice)
			QMessageBox.warning(self, "Droits sur le port série", access.advice)

	def _on_error(self, event: ErrorEvent) -> None:
		text = event.error.user_message
		if event.context:
			text = f"{event.context} : {text}"
		self.ctx.notify(text, 8000)

	def shutdown(self) -> None:
		"""Ferme l'enregistrement en cours, arrête les minuteries des écrans et le service de diagnostic."""
		for page in self.pages.values():
			page.page_hidden()
		self.sessions.shutdown()
		self.ctx.shutdown()

	def closeEvent(self, event: QCloseEvent) -> None:
		self.ctx.save_settings()
		log.info("Fermeture de la fenêtre principale")
		self.shutdown()
		super().closeEvent(event)
