"""Mode démo : ELM327-emulator lancé dans le processus, sur un port TCP local."""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_SCENARIO = "car"


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
		"""Change le scénario de l'émulateur (car, engineoff…)."""
		self.scenario = scenario
		if self._elm is None:
			return
		if scenario not in self._elm.ObdMessage:
			log.warning("Scénario d'émulateur inconnu : %s", scenario)
			return
		self._elm.set_sorted_obd_msg(scenario)

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
