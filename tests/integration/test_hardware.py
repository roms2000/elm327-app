"""Tests sur un adaptateur réel : `pytest -m hardware` (port via OBDTOOL_PORT, défaut /dev/ttyUSB0)."""

from __future__ import annotations

import os

import pytest

from obdtool.core import transport
from obdtool.core.elm_link import ElmLink, LinkConfig

pytestmark = pytest.mark.hardware

PORT = os.environ.get("OBDTOOL_PORT", "/dev/ttyUSB0")


@pytest.fixture
def real_link() -> ElmLink:
	if not transport.check_port_access(PORT).ok:
		pytest.skip(f"{PORT} absent ou inaccessible")
	link = ElmLink()
	link.open(LinkConfig(url=PORT, timeout_s=5.0, retries=0))
	yield link
	link.close()


def test_adapter_answers(real_link: ElmLink) -> None:
	info = real_link.info
	assert info.version.upper().startswith("ELM327") or info.stn
	assert real_link.send_raw("ATI")
