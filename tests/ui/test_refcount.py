"""Détection des appels PySide qui dérèglent le compteur de références de True/False.

Sous Python 3.11, True et False ne sont pas immortels : une extension C qui rend un booléen sans incrémenter
son compteur finit par provoquer « Fatal Python error: bool_dealloc ». Python 3.12+ masque ce défaut.
"""

from __future__ import annotations

import sys
import threading

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton


class _Emitter(QObject):
	obj = Signal(object)
	flag = Signal(bool)


def _drift(func, repeat: int = 2000) -> tuple[int, int]:
	true_before, false_before = sys.getrefcount(True), sys.getrefcount(False)
	for _ in range(repeat):
		func()
	return sys.getrefcount(True) - true_before, sys.getrefcount(False) - false_before


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="booléens immortels depuis Python 3.12")
def test_report_bool_refcount_drift(qtbot) -> None:
	emitter = _Emitter()
	received = []
	emitter.obj.connect(received.append)
	emitter.flag.connect(received.append)
	label = QLabel("x")
	button = QPushButton("x")
	check = QCheckBox("x")
	timer = QTimer()
	qtbot.addWidget(label)
	candidates = {
		"Signal(object).emit": lambda: emitter.obj.emit(1),
		"Signal(bool).emit(True)": lambda: emitter.flag.emit(True),
		"blockSignals": lambda: label.blockSignals(False),
		"isVisible": label.isVisible,
		"isEnabled": button.isEnabled,
		"setEnabled": lambda: button.setEnabled(True),
		"isChecked": check.isChecked,
		"setChecked": lambda: check.setChecked(False),
		"timer.isActive": timer.isActive,
		"connect": lambda: emitter.obj.connect(received.append),
	}

	report = {}
	for name, func in candidates.items():
		report[name] = _drift(func)
	for name, (dt, df) in report.items():
		print(f"refcount drift {name}: True {dt:+d}, False {df:+d}")
	leaking = {name: drift for name, drift in report.items() if drift[0] < -100 or drift[1] < -100}
	assert not leaking, f"Appels qui volent des références à True/False : {leaking}"


def test_bridge_never_emits_from_worker_thread(qtbot) -> None:
	"""Les signaux du pont doivent être émis dans le thread de l'interface (cf. défaut PySide6 / Python 3.11)."""
	from obdtool.core.service import DiagnosticService, LinkState, StateEvent
	from obdtool.ui.bridge import ServiceBridge

	service = DiagnosticService()
	bridge = ServiceBridge(service)
	main = threading.get_ident()
	threads = []
	bridge.state_changed.connect(lambda _e: threads.append(threading.get_ident()))
	try:
		worker = threading.Thread(target=lambda: service._emit(StateEvent(LinkState.CONNECTING)))
		worker.start()
		worker.join()
		qtbot.waitUntil(lambda: bool(threads), timeout=2000)
		assert threads == [main]
	finally:
		bridge.detach()
		service.shutdown()
