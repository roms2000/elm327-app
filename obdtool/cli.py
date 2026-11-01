"""Outil en ligne de commande (sans interface graphique)."""

from __future__ import annotations

import argparse

from obdtool import APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
	"""Point d'entrée de `obdtool-cli`."""
	parser = argparse.ArgumentParser(prog=f"{APP_NAME}-cli", description="Diagnostic OBD-II en ligne de commande")
	parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
	parser.parse_args(argv)
	return 0
