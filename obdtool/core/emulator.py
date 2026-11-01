"""Mode démo : ELM327-emulator lancé dans le processus, sur un port TCP local."""

from __future__ import annotations

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
		emu = elm.Elm(net_port=self.port, net_interface="127.0.0.1")
		emu.__enter__()
		deadline = time.monotonic() + timeout
		while time.monotonic() < deadline:
			if emu.sock_inet is not None and emu.threadState == emu.THREAD.ACTIVE:
				break
			time.sleep(0.02)
		else:
			emu.terminate()
			raise RuntimeError("L'émulateur ELM327 n'a pas démarré.")
		self._elm = emu
		self.set_scenario(self.scenario)
		log.info("Émulateur ELM327 démarré sur %s (scénario %s)", self.url, self.scenario)
		return self.url

	def set_scenario(self, scenario: str) -> None:
		"""Change le scénario de l'émulateur (car, faults…)."""
		self.scenario = scenario
		if self._elm is None:
			return
		import elm.obd_message as messages

		self._install_dtc_overrides()
		self._install_simulation()
		if scenario == FAULTS_SCENARIO:
			self._install_faults_scenario()
			messages.DTC_STORED[:] = DEMO_STORED_DTCS
			messages.DTC_PENDING[:] = DEMO_PENDING_DTCS
		else:
			messages.DTC_STORED[:] = []
			messages.DTC_PENDING[:] = []
		messages.DTC_PERMANENT[:] = []
		self._elm.counters["cmd_dtc_cleared"] = False
		if scenario not in self._elm.ObdMessage:
			log.warning("Scénario d'émulateur inconnu : %s", scenario)
			return
		self._elm.set_sorted_obd_msg(scenario)

	def _install_simulation(self) -> None:
		"""Remplace les valeurs aléatoires de l'émulateur par le simulateur de conduite (valeurs continues).

		Le masque 0100 est aussi ajusté pour ressembler au K4M : capteur MAP, pas de débitmètre (PID 10),
		deux sondes lambda (PID 14 et 15).
		"""
		import elm.obd_message as messages

		from obdtool.core.demo_sim import SIMULATED_PIDS

		simulator = self.simulator
		for name in ("car", FAULTS_SCENARIO):
			scenario = self._elm.ObdMessage.get(name)
			if scenario is None or "DEMO_SIM_0C" in scenario:
				continue
			for pid in (*SIMULATED_PIDS, 0x00):

				def footer(emu: Any, cmd: str, pid_: str, uc_val: Any, pid: int = pid) -> str:
					data = "BE 3E B8 13" if pid == 0x00 else simulator.pid_bytes(pid)
					payload = f"41 {pid:02X} {data}"
					size = f"{len(payload.split()):02X}"
					return messages.HD(messages.ECU_R_ADDR_E) + messages.SZ(size) + messages.DT(payload)

				scenario[f"DEMO_SIM_{pid:02X}"] = {
					"Request": f"^01{pid:02X}" + messages.ELM_FOOTER,
					"Descr": f"PID {pid:02X} simulé (démo)",
					"Header": messages.ECU_ADDR_E,
					"Priority": 1,
					"ResponseFooter": footer,
				}

	def _install_dtc_overrides(self) -> None:
		"""Réponses 03/07/0A conformes à SAE J1979 sur CAN (octet « nombre de codes » après le mode).

		ELM327-emulator omet cet octet quand des codes sont présents ; python-OBD tronque alors la réponse.
		"""
		import elm.obd_message as messages

		sources = {"03": ("43", "DTC_STORED"), "07": ("47", "DTC_PENDING"), "0A": ("4A", "DTC_PERMANENT")}
		for name in ("car", FAULTS_SCENARIO):
			scenario = self._elm.ObdMessage.get(name)
			if scenario is None or "DEMO_DTC_03" in scenario:
				continue
			for mode, (sid, attribute) in sources.items():

				def footer(
					emu: Any, cmd: str, pid: str, uc_val: Any, sid: str = sid, attribute: str = attribute
				) -> str:
					codes = [] if emu.counters.get("cmd_dtc_cleared") else list(getattr(messages, attribute))[:2]
					payload = f"{sid} {len(codes):02X}" + "".join(f" {c[:2]} {c[2:]}" for c in codes)
					size = f"{len(payload.split()):02X}"
					return messages.HD(messages.ECU_R_ADDR_E) + messages.SZ(size) + messages.DT(payload)

				scenario[f"DEMO_DTC_{mode}"] = {
					"Request": f"^{mode}" + messages.ELM_FOOTER,
					"Descr": f"Codes défaut mode {mode} (démo)",
					"Header": messages.ECU_ADDR_E,
					"Priority": 1,
					"ResponseFooter": footer,
				}

	def _install_faults_scenario(self) -> None:
		"""Ajoute un scénario « faults » : celui de « car » avec un freeze frame (effacé par le mode 04)."""
		import elm.obd_message as messages

		if FAULTS_SCENARIO in self._elm.ObdMessage:
			return
		scenario = dict(self._elm.ObdMessage["car"])
		self._elm.ObdMessage[FAULTS_SCENARIO] = scenario
		bitmap = 0
		for pid in _DEMO_FREEZE:
			bitmap |= 1 << (32 - pid)
		bitmap_hex = f"{bitmap:08X}"
		entries = {0x00: " ".join(bitmap_hex[i : i + 2] for i in range(0, 8, 2)), **_DEMO_FREEZE}
		for pid, data in entries.items():
			payload = f"42 {pid:02X} 00 {data}"
			size = f"{len(payload.split()):02X}"

			def footer(
				emu: Any, cmd: str, pid_: str, uc_val: Any, payload: str = payload, size: str = size, pid: int = pid
			) -> str:
				if emu.counters.get("cmd_dtc_cleared") and pid != 0x00:
					data = "42 02 00 00 00" if pid == 0x02 else None
					if data is None:
						return messages.NA("12")
					return messages.HD(messages.ECU_R_ADDR_E) + messages.SZ("05") + messages.DT(data)
				return messages.HD(messages.ECU_R_ADDR_E) + messages.SZ(size) + messages.DT(payload)

			scenario[f"DEMO_FREEZE_{pid:02X}"] = {
				"Request": f"^02{pid:02X}00" + messages.ELM_FOOTER,
				"Descr": f"Freeze frame démo PID {pid:02X}",
				"Header": messages.ECU_ADDR_E,
				"Priority": 1,
				"ResponseFooter": footer,
			}
		self._install_dtc_overrides()
		self._install_simulation()

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
