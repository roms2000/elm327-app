"""Valeurs en direct contre l'émulateur."""

from __future__ import annotations

import threading
import time

import pytest

from obdtool.core.elm_link import ElmLink
from obdtool.core.poller import LivePoller, read_supported_pids
from obdtool.core.samples import SampleBatch
from obdtool.core.service import DiagnosticService, Event, PollEvent
from obdtool.core.settings import ConnectionSettings

pytestmark = pytest.mark.integration


def test_supported_pids_from_emulator(link: ElmLink) -> None:
	supported = read_supported_pids(link)
	assert {0x04, 0x05, 0x0B, 0x0C, 0x0D} <= supported


def test_service_polls_and_reports_rate() -> None:
	service = DiagnosticService()
	poller = LivePoller()
	poller.set_selection(["RPM", "SPEED", "COOLANT_TEMP", "INTAKE_PRESSURE", "ADAPTER_VOLTAGE"])
	service.poll_handler = poller
	batches: list[SampleBatch] = []
	lock = threading.Lock()

	def listener(event: Event) -> None:
		if isinstance(event, PollEvent):
			with lock:
				batches.append(event.payload)

	service.subscribe(listener)
	try:
		service.connect(ConnectionSettings(kind="demo")).result(timeout=20)
		service.set_polling(True)
		time.sleep(2.0)
		# Une tâche ponctuelle passe pendant l'interrogation.
		assert service.submit(lambda link: link.send_raw("ATRV")).result(timeout=5)
		service.set_polling(False)
	finally:
		service.shutdown()
	with lock:
		keys = {s.key for b in batches for s in b.samples}
		rate = batches[-1].query_rate
	assert {"RPM", "SPEED", "INTAKE_PRESSURE"} <= keys
	assert rate > 20
