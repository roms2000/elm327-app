"""Mode démo : ELM327-emulator lancé dans le processus, sur un port TCP local."""

from __future__ import annotations

import inspect
import logging
import os
import socket
import time
from typing import Any

from obdtool.core.demo_sim import DrivingSimulator

log = logging.getLogger(__name__)

DEFAULT_SCENARIO = "car"
FAULTS_SCENARIO = "faults"
SCENARIO_LABELS = {
	"car": "Véhicule en marche, sans défaut",
	FAULTS_SCENARIO: "Véhicule avec défauts (P0171, P0300, P0420 en attente)",
}
DEMO_STORED_DTCS = ["0171", "0300"]
DEMO_PENDING_DTCS = ["0420"]

# Freeze frame du scénario « faults » : PID → octets de données (trame 0), cohérents avec un mélange pauvre.
_DEMO_FREEZE = {
	0x02: "01 71",
	0x03: "02 00",
	0x04: "4D",
	0x05: "7F",
	0x06: "A0",
	0x07: "9A",
	0x0B: "21",
	0x0C: "0C 80",
	0x0D: "00",
	0x0E: "8C",
	0x0F: "46",
	0x11: "20",
}


def emulator_available() -> bool:
	"""Indique si le paquet ELM327-emulator est installé."""
	try:
		import elm  # noqa: F401
	except ImportError:
		return False
	return True


class _EmulatorLogFilter(logging.Filter):
	"""Écarte les messages d'information de l'émulateur (il journalise sur le logger racine)."""

	def __init__(self, package_dir: str) -> None:
		super().__init__()
		self.package_dir = package_dir

	def filter(self, record: logging.LogRecord) -> bool:
		if record.pathname.startswith(self.package_dir):
			if record.levelno < logging.WARNING:
				return False
			record.name = "emulateur"
		return True


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
		sock.bind(("127.0.0.1", 0))
		return int(sock.getsockname()[1])


class DemoEmulator:
	"""Émulateur ELM327 accessible via une URL socket:// locale."""

	def __init__(self, scenario: str = DEFAULT_SCENARIO) -> None:
		self.scenario = scenario
		self.port = 0
		self._elm: Any = None
		self.simulator = DrivingSimulator()
		self.dtcs: dict[str, list[str]] = {"03": [], "07": [], "0A": []}
		self.cleared = False
		# Comportement d'un calculateur de véhicule sans clé : effacement refusé moteur tournant (7F 04 22).
		self.refuse_clear_while_running = False
		self.engine_running = True
		self.renault_faults: list[tuple[str, int]] = []
		self.renault_cleared = False
		self._filter: _EmulatorLogFilter | None = None

	@property
	def running(self) -> bool:
		return self._elm is not None

	@property
	def url(self) -> str:
		return f"socket://127.0.0.1:{self.port}"

	@property
	def emulator(self) -> Any:
		"""Objet `elm.Elm` sous-jacent (pour injecter des réponses en démo et en test)."""
		return self._elm

	def start(self, timeout: float = 5.0, port: int = 0) -> str:
		"""Démarre l'émulateur (sur `port`, ou un port libre) et retourne l'URL de connexion."""
		if self._elm is not None:
			return self.url
		try:
			import elm
			import elm.elm as elm_module
		except ImportError as exc:
			raise RuntimeError(
				"Le mode démo nécessite le paquet ELM327-emulator : pip install 'obdtool[demo]'"
			) from exc
		# L'émulateur reconfigure le logging racine au démarrage : on l'en empêche.
		elm_module.setup_logging = lambda *args, **kwargs: None
		package_dir = os.path.dirname(elm_module.__file__)
		self._filter = _EmulatorLogFilter(package_dir)
		for handler in logging.getLogger().handlers:
			handler.addFilter(self._filter)
		self.port = port or _free_port()
		options: dict[str, Any] = {"net_port": self.port}
		if "net_interface" in inspect.signature(elm.Elm.__init__).parameters:
			options["net_interface"] = "127.0.0.1"  # versions récentes : écoute limitée à la machine locale
		emu = elm.Elm(**options)
		emu.__enter__()
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			if getattr(emu, "sock_inet", None) is not None and emu.threadState == emu.THREAD.ACTIVE:
				break
			time.sleep(0.02)
		else:
			emu.terminate()
			raise RuntimeError("L'émulateur ELM327 n'a pas démarré.")
		self._elm = emu
		self._install_overrides()
		self.set_scenario(self.scenario)
		log.info("Émulateur ELM327 démarré sur %s (scénario %s)", self.url, self.scenario)
		return self.url

	def set_scenario(self, scenario: str) -> None:
		"""Change le scénario de l'émulateur (car, faults…) et réinitialise les codes défaut simulés."""
		self.scenario = scenario
		faults = scenario == FAULTS_SCENARIO
		self.dtcs = {
			"03": list(DEMO_STORED_DTCS) if faults else [],
			"07": list(DEMO_PENDING_DTCS) if faults else [],
			"0A": [],
		}
		self.cleared = False
		# Mémoire de défauts constructeur du calculateur moteur (code sur 2 octets, état ISO 14230).
		self.renault_faults: list[tuple[str, int]] = [("0010", 0xE0)] if faults else []
		self.renault_cleared = False
		if self._elm is None:
			return
		if scenario not in self._elm.ObdMessage:
			log.warning("Scénario d'émulateur inconnu : %s", scenario)
			return
		self._elm.scenario = scenario
		try:
			self._elm.set_sorted_obd_msg(scenario)
		except TypeError:  # anciennes versions : scénario lu dans l'attribut
			self._elm.set_sorted_obd_msg()

	# ------------------------------------------------------- réponses de démo
	def _answer(self, payload: str) -> str:
		import elm.obd_message as messages

		size = f"{len(payload.split()):02X}"
		return messages.HD(messages.ECU_R_ADDR_E) + messages.SZ(size) + messages.DT(payload)

	def _entry(self, request: str, description: str, footer: Any) -> dict[str, Any]:
		import elm.obd_message as messages

		return {
			"Request": f"^{request}" + messages.ELM_FOOTER,
			"Descr": description,
			"Header": messages.ECU_ADDR_E,
			"Priority": 1,
			"ResponseFooter": footer,
		}

	def _install_overrides(self) -> None:
		"""Réponses de démo indépendantes de la version d'ELM327-emulator.

		- simulateur de conduite pour les principaux PID (valeurs continues et cohérentes) ;
		- masque 0100 du K4M : capteur MAP, pas de débitmètre (PID 10), deux sondes lambda (PID 14, 15) ;
		- codes défaut conformes à SAE J1979 sur CAN (octet « nombre de codes »), effacement (mode 04),
		  état du voyant (PID 01) et freeze frame cohérents avec ces codes.
		Ces réponses sont placées en tête du scénario pour être prioritaires sur celles de l'émulateur.
		"""
		from obdtool.core.demo_sim import SIMULATED_PIDS

		ours: dict[str, dict[str, Any]] = {}
		for pid in (*SIMULATED_PIDS, 0x00):

			def sim(emu: Any, cmd: str, pid_: str, uc_val: Any, pid: int = pid) -> str:
				data = "BE 3E B8 13" if pid == 0x00 else self.simulator.pid_bytes(pid)
				return self._answer(f"41 {pid:02X} {data}")

			ours[f"DEMO_SIM_{pid:02X}"] = self._entry(f"01{pid:02X}", f"PID {pid:02X} simulé", sim)

		for mode, sid in (("03", "43"), ("07", "47"), ("0A", "4A")):

			def codes(emu: Any, cmd: str, pid: str, uc_val: Any, mode: str = mode, sid: str = sid) -> str:
				listed = [] if self.cleared else self.dtcs.get(mode, [])[:2]
				return self._answer(f"{sid} {len(listed):02X}" + "".join(f" {c[:2]} {c[2:]}" for c in listed))

			ours[f"DEMO_DTC_{mode}"] = self._entry(mode, f"Codes défaut mode {mode}", codes)

		def clear(emu: Any, cmd: str, pid: str, uc_val: Any) -> str:
			if self.refuse_clear_while_running and self.engine_running:
				return self._answer("7F 04 22")
			self.cleared = True
			return self._answer("44")

		ours["DEMO_CLEAR"] = self._entry("04", "Effacement des codes", clear)

		def status(emu: Any, cmd: str, pid: str, uc_val: Any) -> str:
			stored = [] if self.cleared else self.dtcs["03"]
			mil = (0x80 if stored else 0x00) | len(stored)
			# Allumage commandé ; moniteurs catalyseur, EVAP, sondes, réchauffage sondes supportés.
			incomplete = 0x65 if self.cleared else 0x04
			return self._answer(f"41 01 {mil:02X} 07 65 {incomplete:02X}")

		ours["DEMO_STATUS"] = self._entry("0101", "État depuis l'effacement", status)

		bitmap = 0
		for pid in _DEMO_FREEZE:
			bitmap |= 1 << (32 - pid)
		bitmap_hex = f"{bitmap:08X}"
		freeze = {0x00: " ".join(bitmap_hex[i : i + 2] for i in range(0, 8, 2)), **_DEMO_FREEZE}
		faults_only: dict[str, dict[str, Any]] = {}
		for pid, data in freeze.items():

			def frame(emu: Any, cmd: str, pid_: str, uc_val: Any, pid: int = pid, data: str = data) -> str:
				import elm.obd_message as messages

				if self.cleared and pid != 0x00:
					return self._answer("42 02 00 00 00") if pid == 0x02 else messages.NA("12")
				return self._answer(f"42 {pid:02X} 00 {data}")

			faults_only[f"DEMO_FREEZE_{pid:02X}"] = self._entry(f"02{pid:02X}00", f"Freeze frame PID {pid:02X}", frame)

		ours.update(self._renault_ecus())

		car = self._elm.ObdMessage["car"]
		self._elm.ObdMessage["car"] = {**ours, **{k: v for k, v in car.items() if k not in ours}}
		self._elm.ObdMessage[FAULTS_SCENARIO] = {
			**ours,
			**faults_only,
			**{k: v for k, v in car.items() if k not in ours},
		}

	def _frames(self, header: str, payload: str) -> str:
		"""Réponse ISO-TP (trame unique ou première trame + trames consécutives) au format de l'émulateur."""
		import elm.obd_message as messages

		data = payload.split()
		if len(data) <= 7:
			return messages.HD(header) + messages.SZ(f"{len(data):02X}") + messages.DT(" ".join(data))
		out = messages.HD(header) + messages.SZ("10") + messages.DT(" ".join([f"{len(data):02X}", *data[:6]]))
		rest, index = data[6:], 1
		while rest:
			chunk, rest = rest[:7], rest[7:]
			out += messages.HD(header) + messages.SZ(f"2{index % 16:X}") + messages.DT(" ".join(chunk))
			index += 1
		return out

	def _renault_ecus(self) -> dict[str, dict[str, Any]]:
		"""UCH et tableau de bord FICTIFS (adressage physique Renault), cohérents avec la base de démonstration."""
		import elm.obd_message as messages

		# (émission, réception, octets d'identification 2180 après « 61 80 », réponses de lecture)
		ecus = {
			"UCH": (
				"745",
				"765",
				"00 00 00 00 00 0C 44 45 4D 00 00 00 00 00 01 23 00 01 00 00 00 00 00 00",
				{"2101": "61 01 68 01"},
			),
			"TDB": (
				"743",
				"763",
				"00 00 00 00 00 08 44 4D 4F 00 00 00 00 00 04 56 00 02 00 00 00 00 00 00",
				{"2110": "61 10 90 07"},
			),
		}
		entries: dict[str, dict[str, Any]] = {}
		for name, (tx, rx, ident, reads) in ecus.items():
			answers = {"10C0": "50 C0", "1081": "50 81", "2180": f"61 80 {ident}", **reads}
			for request, payload in answers.items():

				def footer(emu: Any, cmd: str, pid: str, uc_val: Any, rx: str = rx, payload: str = payload) -> str:
					return self._frames(rx, payload)

				entries[f"DEMO_{name}_{request}"] = {
					"Request": f"^{request}$",
					"Descr": f"{name} fictif {request}",
					"Header": tx,
					"Priority": 1,
					"ResponseFooter": footer,
				}

		# Calculateur moteur, services Renault (adresse physique 7E0) : mémoire de défauts constructeur.
		def engine(request: str) -> str:
			if request == "17FF00":
				faults = [] if self.renault_cleared else self.renault_faults
				return "57 " + f"{len(faults):02X}" + "".join(f" {c[:2]} {c[2:]} {st:02X}" for c, st in faults)
			if request == "14FF00":
				if self.refuse_clear_while_running and self.engine_running:
					return "7F 14 22"
				self.renault_cleared = True
				return "54 FF 00"
			return {"10C0": "50 C0", "1081": "50 81", "1802FF00": "7F 18 11"}[request]

		for request in ("10C0", "1081", "17FF00", "1802FF00", "14FF00"):

			def engine_footer(emu: Any, cmd: str, pid: str, uc_val: Any, request: str = request) -> str:
				return self._frames("7E8", engine(request))

			entries[f"DEMO_ENGINE_{request}"] = {
				"Request": f"^{request}$",
				"Descr": f"Moteur Renault {request}",
				"Header": "7E0",
				"Priority": 1,
				"ResponseFooter": engine_footer,
			}

		def reset_cra(emu: Any, cmd: str, pid: str, uc_val: Any) -> str:
			emu.counters["cmd_cra"] = None  # l'émulateur n'efface pas le filtre de réception sur ATAR
			return ""

		entries["DEMO_ATAR"] = {
			"Request": "^ATAR$",
			"Descr": "ATAR",
			"Priority": 1,
			"Response": messages.ELM_R_OK,
			"ResponseFooter": reset_cra,
		}
		return entries

	def scenarios(self) -> list[str]:
		"""Scénarios disponibles."""
		if self._elm is not None:
			return sorted(self._elm.ObdMessage)
		try:
			from elm.obd_message import ObdMessage
		except ImportError:
			return []
		return sorted(ObdMessage)

	def stop(self) -> None:
		"""Arrête l'émulateur."""
		if self._elm is None:
			return
		try:
			self._elm.terminate()
		except Exception as exc:
			log.warning("Arrêt de l'émulateur : %s", exc)
		self._elm = None
		if self._filter is not None:
			for handler in logging.getLogger().handlers:
				handler.removeFilter(self._filter)
		log.info("Émulateur ELM327 arrêté")
