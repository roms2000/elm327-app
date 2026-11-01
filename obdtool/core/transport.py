"""Découverte des ports et vérification des droits d'accès."""

from __future__ import annotations

import glob
import grp
import ipaddress
import logging
import os
import pwd
import socket
import stat
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PORT_PATTERNS = ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/rfcomm*")
BAUDRATES = (9600, 19200, 38400, 57600, 115200, 230400, 500000)
DEFAULT_WIFI_HOST = "192.168.0.10"
DEFAULT_WIFI_PORT = 35000


@dataclass(frozen=True)
class PortInfo:
	"""Port série détecté."""

	device: str
	description: str
	kind: str  # usb | acm | bluetooth | autre


@dataclass(frozen=True)
class PortAccess:
	"""Résultat de la vérification des droits sur un port."""

	device: str
	exists: bool
	readable_writable: bool
	group: str | None
	user_in_group: bool
	group_active: bool
	advice: str

	@property
	def ok(self) -> bool:
		return self.exists and self.readable_writable


def list_ports() -> list[PortInfo]:
	"""Liste les ports série susceptibles d'accueillir un ELM327."""
	descriptions: dict[str, str] = {}
	try:
		from serial.tools import list_ports as serial_list_ports

		for info in serial_list_ports.comports():
			text = " ".join(x for x in (info.manufacturer, info.product or info.description) if x and x != "n/a")
			descriptions[info.device] = text
	except Exception as exc:  # pyserial absent ou erreur udev : on garde le glob
		log.debug("list_ports pyserial indisponible : %s", exc)
	devices: set[str] = set()
	for pattern in PORT_PATTERNS:
		devices.update(glob.glob(pattern))
	result = []
	for device in sorted(devices):
		name = os.path.basename(device)
		kind = "usb" if "USB" in name else "acm" if "ACM" in name else "bluetooth" if "rfcomm" in name else "autre"
		result.append(PortInfo(device, descriptions.get(device, ""), kind))
	return result


def wifi_url(host: str, port: int) -> str:
	"""URL pyserial pour un adaptateur Wi-Fi (socket TCP)."""
	return f"socket://{host.strip()}:{int(port)}"


def validate_wifi(host: str, port: int) -> str | None:
	"""Vérifie l'adresse Wi-Fi ; retourne un message d'erreur ou None."""
	host = host.strip()
	if not host:
		return "Adresse IP vide."
	if not 1 <= int(port) <= 65535:
		return "Port TCP invalide (1 à 65535)."
	try:
		ipaddress.ip_address(host)
	except ValueError:
		if not all(part.isalnum() or "-" in part for part in host.split(".")):
			return f"Adresse invalide : {host}"
	return None


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> str | None:
	"""Teste l'ouverture d'une connexion TCP ; retourne un message d'erreur ou None."""
	try:
		with socket.create_connection((host, port), timeout=timeout):
			return None
	except OSError as exc:
		return f"Connexion TCP à {host}:{port} impossible : {exc.strerror or exc}"


def serial_group_name() -> str:
	"""Groupe système habituel pour les ports série selon la distribution."""
	for name in ("dialout", "uucp", "tty"):
		try:
			grp.getgrnam(name)
			return name
		except KeyError:
			continue
	return "dialout"


def check_port_access(device: str) -> PortAccess:
	"""Vérifie que l'utilisateur peut ouvrir le port et explique comment corriger sinon."""
	path = Path(device)
	user = _current_user()
	if not path.exists():
		return PortAccess(
			device, False, False, None, False, False, f"Le port {device} n'existe pas (adaptateur branché ?)."
		)
	try:
		st = path.stat()
		group = grp.getgrgid(st.st_gid).gr_name
	except (OSError, KeyError):
		st = None
		group = None
	rw = os.access(device, os.R_OK | os.W_OK)
	group = group or serial_group_name()
	member = _user_in_group(user, group)
	active = _group_active(group)
	if rw:
		advice = ""
	elif member and not active:
		advice = (
			f"Vous êtes bien membre du groupe « {group} », mais la session actuelle ne le prend pas encore en compte.\n"
			"Fermez puis rouvrez votre session (ou redémarrez)."
		)
	else:
		mode = stat.filemode(st.st_mode) if st else "?"
		advice = (
			f"Le port {device} ({mode}, groupe « {group} ») n'est pas accessible à l'utilisateur « {user} ».\n"
			f"Ajoutez-vous au groupe « {group} » puis rouvrez votre session :\n"
			f"    sudo usermod -aG {group} {user}"
		)
		if group == "uucp":
			advice += "\n(Sur Arch/Manjaro le groupe est « uucp » ; sur Debian/Ubuntu/Fedora c'est « dialout ».)"
	return PortAccess(device, True, rw, group, member, active, advice)


def _current_user() -> str:
	try:
		return pwd.getpwuid(os.getuid()).pw_name
	except KeyError:
		return os.environ.get("USER", "utilisateur")


def _user_in_group(user: str, group: str) -> bool:
	try:
		info = grp.getgrnam(group)
	except KeyError:
		return False
	if user in info.gr_mem:
		return True
	try:
		return pwd.getpwnam(user).pw_gid == info.gr_gid
	except KeyError:
		return False


def _group_active(group: str) -> bool:
	try:
		gid = grp.getgrnam(group).gr_gid
	except KeyError:
		return False
	return gid in os.getgroups() or gid == os.getgid()


def port_users(device: str) -> list[tuple[int, str]]:
	"""Processus (autres que celui-ci) qui ont ouvert le port, d'après /proc : [(pid, commande)]."""
	try:
		target = os.path.realpath(device)
	except OSError:
		return []
	users = []
	own = os.getpid()
	for entry in Path("/proc").iterdir():
		if not entry.name.isdigit() or int(entry.name) == own:
			continue
		try:
			for fd in (entry / "fd").iterdir():
				if os.path.realpath(fd) == target:
					cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
					users.append((int(entry.name), cmdline or entry.name))
					break
		except OSError:
			continue
	return users
