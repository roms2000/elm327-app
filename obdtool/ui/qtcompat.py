"""Contournement d'un défaut de PySide6 sous Python 3.11 : `Signal.emit()` rend un booléen sans incrémenter son
compteur de références. À chaque émission, True perd une référence, jusqu'au plantage de l'interpréteur
(« Fatal Python error: bool_dealloc »). Python 3.12+ n'est pas touché (booléens immortels).

`emit()` remplace `signal.emit()` dans l'application : au premier appel, le défaut est mesuré ; s'il est présent,
la référence perdue est rendue après chaque émission. Sinon, l'appel est direct.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

_compensate: bool | None = None
_incref = ctypes.pythonapi.Py_IncRef
_incref.argtypes = [ctypes.py_object]
_incref.restype = None


class _Probe(QObject):
	probe = Signal(object)


def _measure() -> bool:
	"""Vrai si `Signal.emit` vole une référence au booléen qu'il renvoie."""
	if sys.version_info >= (3, 12):
		return False
	probe = _Probe()
	probe.probe.connect(lambda _value: None)
	before = sys.getrefcount(True)
	for _ in range(200):
		probe.probe.emit(None)
	lost = before - sys.getrefcount(True)
	if lost >= 150:
		_incref_many(True, lost)  # rend les références perdues pendant la mesure
		log.warning("PySide6 : Signal.emit perd des références à True sous Python 3.11 ; compensation activée")
		return True
	return False


def _incref_many(obj: object, count: int) -> None:
	for _ in range(count):
		_incref(obj)


def emit(signal: Any, *args: Any) -> None:
	"""Émet `signal` en compensant, si nécessaire, la référence perdue par PySide6."""
	global _compensate
	if _compensate is None:
		_compensate = _measure()
	result = signal.emit(*args)
	if _compensate and isinstance(result, bool):
		_incref(result)
