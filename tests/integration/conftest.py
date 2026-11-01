"""Fixtures des tests d'intégration contre ELM327-emulator."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from obdtool.core.elm_link import ElmLink, LinkConfig
from obdtool.core.emulator import DemoEmulator, emulator_available

pytestmark = pytest.mark.integration

if not emulator_available():
	pytest.skip("ELM327-emulator non installé", allow_module_level=True)


@pytest.fixture
def emulator() -> Iterator[DemoEmulator]:
	emu = DemoEmulator()
	emu.start()
	yield emu
	emu.stop()


@pytest.fixture
def link(emulator: DemoEmulator) -> Iterator[ElmLink]:
	elm = ElmLink()
	elm.open(LinkConfig(url=emulator.url, timeout_s=3.0))
	yield elm
	elm.close()
