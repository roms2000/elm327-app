"""Réglages persistants, stockés en TOML dans ~/.config/obdtool/config.toml."""

from __future__ import annotations

import logging
import tomllib
import types
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import tomli_w

from obdtool.core import paths

log = logging.getLogger(__name__)


@dataclass
class GeneralSettings:
	"""Réglages généraux de l'interface."""

	theme: str = "system"  # system | light | dark
	units: str = "metric"  # metric | imperial
	first_launch_ack: bool = False
	alert_sound: bool = False


@dataclass
class ConnectionSettings:
	"""Dernière configuration de connexion utilisée."""

	kind: str = "serial"  # serial | wifi | demo
	port: str = "/dev/ttyUSB0"
	baudrate: int = 0  # 0 = détection automatique
	protocol: str = "0"  # code ATSP : 0 = automatique, 1..C = forcé
	fast: bool = True
	timeout_s: float = 5.0
	retries: int = 2
	auto_reconnect: bool = True
	wifi_host: str = "192.168.0.10"
	wifi_port: int = 35000
	demo_scenario: str = "car"


@dataclass
class LogSettings:
	"""Réglages des journaux."""

	level: str = "INFO"
	keep_sessions: int = 10
	max_total_mb: float = 50.0


@dataclass
class PollingSettings:
	"""Réglages de l'interrogation des valeurs en direct."""

	interval_ms: int = 0  # 0 = aussi vite que possible
	selected_pids: list[str] = field(default_factory=list)


@dataclass
class Settings:
	"""Ensemble des réglages de l'application."""

	general: GeneralSettings = field(default_factory=GeneralSettings)
	connection: ConnectionSettings = field(default_factory=ConnectionSettings)
	logs: LogSettings = field(default_factory=LogSettings)
	polling: PollingSettings = field(default_factory=PollingSettings)
	extra: dict[str, Any] = field(default_factory=dict)

	@classmethod
	def default_path(cls) -> Path:
		return paths.config_dir() / "config.toml"

	@classmethod
	def load(cls, path: Path | None = None) -> Settings:
		"""Charge les réglages ; valeurs par défaut si le fichier est absent ou invalide."""
		path = path or cls.default_path()
		if not path.exists():
			return cls()
		try:
			with path.open("rb") as fh:
				data = tomllib.load(fh)
		except (OSError, tomllib.TOMLDecodeError) as exc:
			log.warning("Réglages illisibles (%s), valeurs par défaut utilisées : %s", path, exc)
			return cls()
		return _from_dict(cls, data)

	def save(self, path: Path | None = None) -> None:
		"""Enregistre les réglages de façon atomique."""
		path = path or self.default_path()
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp = path.with_suffix(".tmp")
		try:
			with tmp.open("wb") as fh:
				tomli_w.dump(_to_dict(self), fh)
			tmp.replace(path)
		except OSError as exc:
			log.error("Impossible d'enregistrer les réglages dans %s : %s", path, exc)


def _to_dict(obj: Any) -> Any:
	if is_dataclass(obj):
		return {f.name: _to_dict(getattr(obj, f.name)) for f in fields(obj)}
	if isinstance(obj, dict):
		return {str(k): _to_dict(v) for k, v in obj.items() if v is not None}
	if isinstance(obj, list | tuple):
		return [_to_dict(v) for v in obj if v is not None]
	return obj


def _from_dict(cls: type, data: Any) -> Any:
	"""Construit une dataclass en ignorant les clés inconnues et les types invalides."""
	if not isinstance(data, dict):
		return cls()
	hints = typing.get_type_hints(cls)
	kwargs: dict[str, Any] = {}
	for f in fields(cls):
		if f.name not in data:
			continue
		value = data[f.name]
		hint = hints.get(f.name)
		if isinstance(hint, type) and is_dataclass(hint):
			kwargs[f.name] = _from_dict(hint, value)
			continue
		default = f.default if f.default is not MISSING else f.default_factory()  # type: ignore[misc]
		if _type_ok(value, default, hint):
			kwargs[f.name] = float(value) if isinstance(default, float) and isinstance(value, int) else value
		else:
			log.warning("Réglage %s.%s invalide (%r), valeur par défaut conservée", cls.__name__, f.name, value)
	return cls(**kwargs)


def _type_ok(value: Any, default: Any, hint: Any) -> bool:
	if isinstance(default, bool):
		return isinstance(value, bool)
	if isinstance(default, float):
		return isinstance(value, int | float) and not isinstance(value, bool)
	if isinstance(default, int):
		return isinstance(value, int) and not isinstance(value, bool)
	if isinstance(default, str):
		return isinstance(value, str)
	origin = typing.get_origin(hint)
	if origin in (list, typing.List):  # noqa: UP006
		return isinstance(value, list)
	if origin in (dict, typing.Dict):  # noqa: UP006
		return isinstance(value, dict)
	if isinstance(hint, types.UnionType):
		return True
	return True
