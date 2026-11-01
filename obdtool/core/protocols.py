"""Protocoles OBD-II de l'ELM327 (codes ATSP0 à ATSPC)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ElmProtocol:
	"""Protocole sélectionnable avec ATSPx."""

	code: str
	name: str
	is_can: bool


PROTOCOLS: tuple[ElmProtocol, ...] = (
	ElmProtocol("0", "Automatique", False),
	ElmProtocol("1", "SAE J1850 PWM (41,6 kbaud)", False),
	ElmProtocol("2", "SAE J1850 VPW (10,4 kbaud)", False),
	ElmProtocol("3", "ISO 9141-2 (5 baud init, 10,4 kbaud)", False),
	ElmProtocol("4", "ISO 14230-4 KWP (5 baud init, 10,4 kbaud)", False),
	ElmProtocol("5", "ISO 14230-4 KWP (init rapide, 10,4 kbaud)", False),
	ElmProtocol("6", "ISO 15765-4 CAN (11 bit, 500 kbaud)", True),
	ElmProtocol("7", "ISO 15765-4 CAN (29 bit, 500 kbaud)", True),
	ElmProtocol("8", "ISO 15765-4 CAN (11 bit, 250 kbaud)", True),
	ElmProtocol("9", "ISO 15765-4 CAN (29 bit, 250 kbaud)", True),
	ElmProtocol("A", "SAE J1939 CAN (29 bit, 250 kbaud)", True),
	ElmProtocol("B", "USER1 CAN (11 bit, 125 kbaud par défaut)", True),
	ElmProtocol("C", "USER2 CAN (11 bit, 50 kbaud par défaut)", True),
)

_BY_CODE = {p.code: p for p in PROTOCOLS}


def protocol_by_code(code: str) -> ElmProtocol | None:
	"""Protocole correspondant à un code ATSP (insensible à la casse)."""
	return _BY_CODE.get(code.strip().upper())


def parse_dpn(answer: str) -> tuple[str, bool]:
	"""Décode la réponse à ATDPN : (code, détecté automatiquement)."""
	answer = answer.strip().upper()
	auto = len(answer) > 1 and answer.startswith("A")
	return (answer[1:] if auto else answer), auto


def describe_dpn(answer: str) -> str:
	"""Libellé lisible d'une réponse ATDPN."""
	code, auto = parse_dpn(answer)
	proto = protocol_by_code(code)
	name = proto.name if proto else f"inconnu ({answer})"
	return f"{name} (détection auto)" if auto and code != "0" else name


def is_can(code: str) -> bool:
	"""Indique si le protocole est un protocole CAN."""
	proto = protocol_by_code(code)
	return bool(proto and proto.is_can)
