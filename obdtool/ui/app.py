"""Lancement de l'interface graphique."""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from types import TracebackType

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QWidget

from obdtool import APP_DISPLAY_NAME, APP_NAME, __version__
from obdtool.core import log as applog
from obdtool.core import paths
from obdtool.core.settings import Settings
from obdtool.ui import theme
from obdtool.ui.context import AppContext
from obdtool.ui.main_window import MainWindow

log = logging.getLogger(__name__)


def parse_args(argv: list[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(prog=APP_NAME, description="Diagnostic OBD-II pour ELM327")
	parser.add_argument("--demo", action="store_true", help="se connecter directement à l'émulateur")
	parser.add_argument("--debug", action="store_true", help="journalisation détaillée (trames brutes)")
	parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
	args, _unknown = parser.parse_known_args(argv)
	return args


def install_excepthook(parent: QWidget | None = None) -> None:
	"""Journalise les exceptions non gérées et les signale sans fermer l'application."""

	def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
		if issubclass(exc_type, KeyboardInterrupt):
			sys.__excepthook__(exc_type, exc, tb)
			return
		log.critical("Exception non gérée :\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
		if QApplication.instance() is not None:
			QMessageBox.critical(
				parent,
				"Erreur inattendue",
				f"Une erreur inattendue s'est produite :\n{exc}\n\nLe détail est enregistré dans le journal.",
			)

	sys.excepthook = hook


def show_first_launch_warning(ctx: AppContext, parent: QWidget | None) -> bool:
	"""Avertissement de sécurité au premier lancement. Retourne False si l'utilisateur refuse."""
	if ctx.settings.general.first_launch_ack:
		return True
	box = QMessageBox(parent)
	box.setIcon(QMessageBox.Icon.Warning)
	box.setWindowTitle("Avertissement de sécurité")
	box.setText("<b>Ne manipulez jamais cette application en conduisant.</b>")
	box.setInformativeText(
		"Installez l'ordinateur de façon stable, ou faites-vous assister par un passager.\n\n"
		"L'adaptateur ELM327 ne donne accès qu'aux fonctions OBD-II génériques. "
		"L'effacement des codes défaut et les commandes expertes peuvent modifier l'état du calculateur : "
		"utilisez-les en connaissance de cause."
	)
	box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
	box.button(QMessageBox.StandardButton.Ok).setText("J'ai compris")
	box.button(QMessageBox.StandardButton.Cancel).setText("Quitter")
	dont_show = QCheckBox("Ne plus afficher")
	dont_show.setChecked(True)
	box.setCheckBox(dont_show)
	if box.exec() != QMessageBox.StandardButton.Ok:
		return False
	if dont_show.isChecked():
		ctx.settings.general.first_launch_ack = True
		ctx.save_settings()
	return True


def main(argv: list[str] | None = None) -> int:
	"""Point d'entrée de l'interface graphique."""
	args = parse_args(sys.argv[1:] if argv is None else argv)
	paths.ensure_dirs()
	settings = Settings.load()
	applog.setup_logging(
		level="DEBUG" if args.debug else settings.logs.level,
		keep_sessions=settings.logs.keep_sessions,
		max_total_mb=settings.logs.max_total_mb,
	)
	log.info("Démarrage de %s %s (Python %s)", APP_DISPLAY_NAME, __version__, sys.version.split()[0])

	QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
	app = QApplication(sys.argv[:1])
	app.setApplicationName(APP_NAME)
	app.setApplicationDisplayName(APP_DISPLAY_NAME)
	app.setApplicationVersion(__version__)
	app.setDesktopFileName(APP_NAME)
	theme.apply_theme(app, settings.general.theme)

	ctx = AppContext(settings)
	window = MainWindow(ctx)
	install_excepthook(window)
	if not show_first_launch_warning(ctx, None):
		log.info("Avertissement refusé, fermeture")
		return 0
	window.show()
	code = app.exec()
	log.info("Arrêt de l'application (code %d)", code)
	return code
