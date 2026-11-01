"""Tests du catalogue de PID, des unités, des statistiques et de l'interrogation cyclique."""

from __future__ import annotations

import time

import pytest
from obd.protocols import ISO_15765_4_11bit_500k

from obdtool.core import pids, units
from obdtool.core.errors import ErrorKind, ObdError
from obdtool.core.poller import LivePoller, decode_messages, read_supported_pids
from obdtool.core.samples import Sample, SampleBatch, SessionStats

_PROTO = ISO_15765_4_11bit_500k(["7E8 06 41 00 BE 3F A8 13"])


def _messages(*lines: str) -> list:
	return [m for m in _PROTO(list(lines)) if m.data]


def test_catalog_consistency() -> None:
	for key, definition in pids.CATALOG.items():
		assert key == definition.key
		assert definition.category in pids.CATEGORIES
		assert definition.vmin < definition.vmax
		if definition.source == "obd" and definition.decoder is None:
			assert definition.command is not None, key
			assert definition.pid not in pids.NON_LIVE_PIDS
	for key in pids.DEFAULT_SELECTION:
		assert key in pids.CATALOG


def test_bitmap_decoding() -> None:
	# Réponse de l'émulateur à 0100 : BE 3F A8 13.
	supported = pids.decode_supported_bitmap(0x00, bytes.fromhex("BE3FA813"))
	assert {0x01, 0x03, 0x04, 0x05, 0x0C, 0x0D, 0x0F, 0x10, 0x11, 0x20} <= supported
	assert 0x02 not in supported
	assert pids.decode_supported_bitmap(0x20, bytes.fromhex("80000001")) == {0x21, 0x40}


def test_decode_python_obd_and_custom() -> None:
	rpm = decode_messages(pids.CATALOG["RPM"], _messages("7E8 04 41 0C 1A F8"))
	assert rpm == pytest.approx(1726.0)
	temp = decode_messages(pids.CATALOG["COOLANT_TEMP"], _messages("7E8 03 41 05 7B"))
	assert temp == pytest.approx(83.0)
	messages = _messages("7E8 04 41 14 5A 90")
	assert decode_messages(pids.CATALOG["O2_B1S1"], messages) == pytest.approx(0.45)
	assert decode_messages(pids.CATALOG["O2_B1S1_TRIM"], messages) == pytest.approx(12.5)
	assert decode_messages(pids.CATALOG["O2_B1S1_TRIM"], _messages("7E8 04 41 14 5A FF")) is None
	wide = decode_messages(pids.CATALOG["O2_S1_WR_LAMBDA"], _messages("7E8 06 41 24 80 00 80 00"))
	assert wide == pytest.approx(1.0)
	status = decode_messages(pids.CATALOG["FUEL_STATUS"], _messages("7E8 04 41 03 02 00"))
	assert status == "Boucle fermée (régulation par sonde lambda)"


def test_format_value() -> None:
	assert pids.format_value(None) == "—"
	assert pids.format_value(3788.0, "tr/min") == "3788"
	assert pids.format_value(0.45, "V") == "0,450"
	assert pids.format_value(12.34, "%") == "12,3"
	assert pids.format_value("Essence") == "Essence"


def test_units() -> None:
	assert units.display_unit("km/h", "imperial") == "mph"
	assert units.display_unit("km/h", "metric") == "km/h"
	assert units.convert(100.0, "km/h", "imperial") == pytest.approx(62.137, rel=1e-3)
	assert units.convert(100.0, "°C", "imperial") == pytest.approx(212.0)
	assert units.convert(100.0, "kPa", "imperial") == pytest.approx(14.5038, rel=1e-3)
	assert units.convert("texte", "km/h", "imperial") == "texte"
	assert units.convert(5.0, "%", "imperial") == 5.0


def test_session_stats() -> None:
	stats = SessionStats()
	stats.add_batch(
		SampleBatch(
			[
				Sample("RPM", 1.0, 800.0),
				Sample("RPM", 2.0, 3000.0),
				Sample("RPM", 3.0, None, "pas de réponse"),
				Sample("FUEL_STATUS", 1.0, "Boucle fermée"),
			]
		)
	)
	rpm = stats.get("RPM")
	assert rpm is not None
	assert (rpm.minimum, rpm.maximum, rpm.mean, rpm.count) == (800.0, 3000.0, 1900.0, 2)
	assert rpm.error == "pas de réponse"
	assert rpm.last == 3000.0
	assert stats.get("FUEL_STATUS").last == "Boucle fermée"
	stats.reset()
	assert stats.get("RPM") is None


class FakeLink:
	"""Adaptateur factice : répond à partir d'un dictionnaire requête → lignes."""

	def __init__(self, answers: dict[str, list[str] | ObdError]) -> None:
		self.answers = answers
		self.requests: list[str] = []

	def request(self, request: str, fast: bool = False) -> list:
		self.requests.append(request)
		answer = self.answers.get(request, ObdError(ErrorKind.NO_DATA))
		if isinstance(answer, ObdError):
			raise answer
		return _messages(*answer)

	def read_voltage(self) -> float:
		return 13.8


def _run(poller: LivePoller, link: FakeLink, steps: int) -> list[Sample]:
	samples: list[Sample] = []
	for _ in range(steps):
		batch = poller.poll_step(link)  # type: ignore[arg-type]
		if batch:
			samples.extend(batch.samples)
	time.sleep(0.11)
	batch = poller._maybe_flush()
	if batch:
		samples.extend(batch.samples)
	return samples


def test_poller_groups_values_sharing_a_pid() -> None:
	link = FakeLink({"010C": ["7E8 04 41 0C 1A F8"], "0114": ["7E8 04 41 14 5A 90"]})
	poller = LivePoller()
	poller.set_selection(["RPM", "O2_B1S1", "O2_B1S1_TRIM", "ADAPTER_VOLTAGE", "INCONNU"])
	samples = _run(poller, link, 6)
	keys = {s.key for s in samples}
	assert {"RPM", "O2_B1S1", "O2_B1S1_TRIM"} <= keys
	# Une seule requête par PID pour la sonde et sa correction.
	o2_requests = link.requests.count("0114")
	assert o2_requests >= 1
	assert [s.key for s in samples].count("O2_B1S1") == o2_requests
	assert [s.key for s in samples].count("O2_B1S1_TRIM") == o2_requests


def test_poller_pauses_silent_pid_and_detects_vehicle_loss() -> None:
	link = FakeLink({"010C": ["7E8 04 41 0C 1A F8"]})
	poller = LivePoller()
	poller.set_selection(["RPM", "SPEED"])
	samples = _run(poller, link, 8)
	speed = [s for s in samples if s.key == "SPEED"]
	assert speed and all(s.value is None for s in speed)
	assert any("pause" in s.error for s in speed)
	count = link.requests.count("010D")
	_run(poller, link, 6)
	assert link.requests.count("010D") == count  # en pause

	dead = FakeLink({"010C": ObdError(ErrorKind.CAN_ERROR), "010D": ObdError(ErrorKind.CAN_ERROR)})
	poller.reset()
	with pytest.raises(ObdError) as err:
		_run(poller, dead, 10)
	assert err.value.kind == ErrorKind.CAN_ERROR


def test_poller_interval_limits_cycle_rate() -> None:
	link = FakeLink({"010C": ["7E8 04 41 0C 1A F8"]})
	poller = LivePoller()
	poller.set_selection(["RPM"])
	poller.set_interval(100)
	start = time.monotonic()
	while time.monotonic() - start < 0.55:
		poller.poll_step(link)  # type: ignore[arg-type]
	assert 4 <= link.requests.count("010C") <= 7


def test_read_supported_pids_follows_blocks() -> None:
	link = FakeLink(
		{
			"0100": ["7E8 06 41 00 BE 3F A8 13"],
			"0120": ["7E8 06 41 20 80 00 00 00"],
		}
	)
	supported = read_supported_pids(link)  # type: ignore[arg-type]
	assert 0x0C in supported and 0x21 in supported
	assert link.requests == ["0100", "0120"]
