"""Outil en ligne de commande (sans interface graphique)."""

from __future__ import annotations

import argparse
import logging
import sys

from obdtool import APP_NAME, __version__
from obdtool.core import log as applog
from obdtool.core import paths, transport
from obdtool.core.adapter_info import assess_voltage
from obdtool.core.elm_link import ElmLink, LinkConfig
from obdtool.core.emulator import DemoEmulator
from obdtool.core.errors import ObdError

log = logging.getLogger(__name__)


def _add_link_args(parser: argparse.ArgumentParser) -> None:
	group = parser.add_mutually_exclusive_group()
	group.add_argument("--port", default="/dev/ttyUSB0", help="port série (défaut : /dev/ttyUSB0)")
	group.add_argument("--wifi", metavar="IP:PORT", help="adaptateur Wi-Fi, ex. 192.168.0.10:35000")
	group.add_argument("--demo", action="store_true", help="utiliser l'émulateur ELM327")
	parser.add_argument("--baud", type=int, default=0, help="débit série (0 = automatique)")
	parser.add_argument("--protocol", default="0", help="code protocole ATSP (0 = automatique)")
	parser.add_argument("--timeout", type=float, default=5.0, help="délai de réponse en secondes")


def _open(args: argparse.Namespace) -> tuple[ElmLink, DemoEmulator | None]:
	emulator = None
	if args.demo:
		emulator = DemoEmulator()
		url = emulator.start()
	elif args.wifi:
		host, _, port = args.wifi.rpartition(":")
		url = transport.wifi_url(host or args.wifi, int(port or transport.DEFAULT_WIFI_PORT))
	else:
		url = args.port
	link = ElmLink()
	link.open(LinkConfig(url=url, baudrate=args.baud, protocol=args.protocol, timeout_s=args.timeout))
	return link, emulator


def cmd_ports(_args: argparse.Namespace) -> int:
	ports = transport.list_ports()
	if not ports:
		print("Aucun port série détecté (/dev/ttyUSB*, /dev/ttyACM*, /dev/rfcomm*).")
	for info in ports:
		access = transport.check_port_access(info.device)
		state = "accessible" if access.ok else "ACCÈS REFUSÉ"
		print(f"{info.device:16} {state:13} {info.description}")
		if not access.ok:
			print("    " + access.advice.replace("\n", "\n    "))
	return 0


def cmd_info(args: argparse.Namespace) -> int:
	link, emulator = _open(args)
	try:
		info = link.info
		print(f"Adaptateur   : {info.version} ({info.description})")
		if info.stn:
			print(f"Puce STN     : {info.stn}")
		voltage = f"{info.voltage:.1f} V" if info.voltage is not None else info.voltage_raw or "?"
		print(f"Tension      : {voltage} — {assess_voltage(info.voltage)}")
		print(f"Protocole    : {info.protocol_name or 'non déterminé'}")
		print(f"Véhicule     : {info.vehicle_state.value}")
		if info.ecu_error:
			print(f"               {info.ecu_error}")
		for warning in info.warnings:
			print(f"ATTENTION    : {warning}")
	finally:
		link.close()
		if emulator:
			emulator.stop()
	return 0


def cmd_raw(args: argparse.Namespace) -> int:
	link, emulator = _open(args)
	try:
		for command in args.commands:
			try:
				lines = link.send_raw(command, check=False)
			except ObdError as exc:
				lines = [f"<{exc.user_message}>"]
			print(f"> {command}")
			for line in lines:
				print(f"  {line}")
	finally:
		link.close()
		if emulator:
			emulator.stop()
	return 0


def main(argv: list[str] | None = None) -> int:
	"""Point d'entrée de `obdtool-cli`."""
	parser = argparse.ArgumentParser(prog=f"{APP_NAME}-cli", description="Diagnostic OBD-II en ligne de commande")
	parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
	parser.add_argument("--debug", action="store_true", help="journalisation détaillée")
	sub = parser.add_subparsers(dest="command", required=True)
	sub.add_parser("ports", help="lister les ports série et vérifier les droits").set_defaults(func=cmd_ports)
	info = sub.add_parser("info", help="se connecter et afficher les informations de l'adaptateur")
	_add_link_args(info)
	info.set_defaults(func=cmd_info)
	raw = sub.add_parser("raw", help="envoyer des commandes AT ou hexadécimales brutes")
	_add_link_args(raw)
	raw.add_argument("commands", nargs="+", help="commandes, ex. ATI ATRV 0100")
	raw.set_defaults(func=cmd_raw)
	args = parser.parse_args(argv)
	paths.ensure_dirs()
	applog.setup_logging(level="DEBUG" if args.debug else "INFO", console=True)
	try:
		return int(args.func(args))
	except ObdError as exc:
		print(f"Erreur : {exc.user_message}", file=sys.stderr)
		return 2
	except RuntimeError as exc:
		print(f"Erreur : {exc}", file=sys.stderr)
		return 2
