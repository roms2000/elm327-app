"""Simulateur de conduite pour le mode démo : valeurs cohérentes entre elles et continues dans le temps.

Le cycle (≈ 100 s, répété) enchaîne ralenti, accélération en passant les rapports jusqu'à ~110 km/h,
croisière, reprise 80-120, décélération (coupure d'injection) et arrêt. Les rapports et la taille de pneu
sont ceux du profil par défaut (Clio III 1.6 16v).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

GEAR_RATIOS = (3.727, 2.048, 1.393, 1.029, 0.820)
FINAL_DRIVE = 4.214
WHEEL_CIRCUMFERENCE_M = 1.87  # 185/60 R15
IDLE_RPM = 780.0

# (durée en s, accélération en km/h/s) ; None = ralenti à l'arrêt.
_PHASES: tuple[tuple[float, float | None], ...] = (
	(8.0, None),
	(14.0, 7.5),  # 0 → ~105 km/h
	(14.0, 0.0),  # croisière
	(6.0, -5.0),  # ralentissement vers ~75
	(7.0, 6.5),  # reprise 75 → ~120
	(10.0, 0.0),
	(18.0, -6.5),  # freinage moteur jusqu'à l'arrêt
	(6.0, None),
)
CYCLE_S = sum(d for d, _ in _PHASES)


@dataclass
class SimState:
	"""État instantané du véhicule simulé."""

	speed: float = 0.0  # km/h
	rpm: float = IDLE_RPM
	gear: int = 0  # 0 = point mort
	load: float = 20.0  # %
	throttle: float = 13.0  # %
	map_kpa: float = 32.0
	coolant: float = 20.0
	iat: float = 22.0
	timing: float = 8.0
	stft: float = 0.0
	ltft: float = 2.3
	o2_up: float = 0.45
	o2_down: float = 0.65
	fuel_cut: bool = False
	closed_loop: bool = False


class DrivingSimulator:
	"""Calcule l'état du véhicule à un instant donné (temps réel par défaut)."""

	def __init__(self, start: float | None = None) -> None:
		self.start = time.monotonic() if start is None else start
		self._speed = 0.0
		self._last = self.start
		self._state = SimState()

	def _phase(self, elapsed: float) -> float | None:
		t = elapsed % CYCLE_S
		for duration, accel in _PHASES:
			if t < duration:
				return accel
			t -= duration
		return None

	def state(self, now: float | None = None) -> SimState:
		"""État du véhicule à l'instant `now` (horloge monotone)."""
		now = time.monotonic() if now is None else now
		dt = max(0.0, min(now - self._last, 1.0))
		self._last = now
		elapsed = now - self.start
		accel = self._phase(elapsed)
		if accel is None:
			self._speed = max(0.0, self._speed - 8.0 * dt)
		else:
			self._speed = min(max(0.0, self._speed + accel * dt), 125.0)
		s = self._state
		s.speed = self._speed
		# Choix du rapport selon la vitesse (montée vers 3000 tr/min, descente vers 1500).
		if s.speed < 3:
			s.gear = 0
		else:
			s.gear = 1 + sum(s.speed > threshold for threshold in (20, 38, 58, 78))
		if s.gear == 0:
			target_rpm = IDLE_RPM + (250.0 if s.coolant < 50 else 0.0)
		else:
			wheel_rps = s.speed / 3.6 / WHEEL_CIRCUMFERENCE_M
			target_rpm = max(IDLE_RPM, wheel_rps * GEAR_RATIOS[s.gear - 1] * FINAL_DRIVE * 60)
		s.rpm = target_rpm + 15.0 * math.sin(elapsed * 7.0)
		accelerating = accel is not None and accel > 0.5
		decelerating = accel is not None and accel < -0.5 and s.gear > 0
		s.fuel_cut = decelerating and s.rpm > 1300
		if accelerating:
			s.throttle = 35.0 + 8.0 * accel
		elif s.fuel_cut:
			s.throttle = 0.0
		elif s.gear:
			s.throttle = 12.0 + s.speed * 0.12
		else:
			s.throttle = 2.0
		s.load = min(100.0, 18.0 + s.throttle * 0.9) if not s.fuel_cut else 8.0
		s.map_kpa = min(100.0, 28.0 + s.load * 0.7)
		s.timing = 34.0 - s.load * 0.25 if not s.fuel_cut else 2.0
		# Montée en température : 20 → 90 °C en ~3 min, puis régulation thermostat.
		s.coolant = min(90.0 + math.sin(elapsed / 20.0), 20.0 + elapsed * 0.4)
		s.iat = 22.0 + min(elapsed * 0.05, 12.0)
		s.closed_loop = s.coolant > 40 and not s.fuel_cut and not (accelerating and s.load > 85)
		if s.closed_loop:
			s.o2_up = 0.45 + 0.38 * math.sin(elapsed * 2 * math.pi * 1.2)
			s.stft = 3.0 * math.sin(elapsed * 2 * math.pi * 0.6)
		else:
			s.o2_up = 0.05 if s.fuel_cut else 0.85
			s.stft = 0.0
		s.o2_down = 0.68 + 0.03 * math.sin(elapsed * 0.3)
		return s

	@staticmethod
	def fuel_status_byte(state: SimState) -> int:
		"""Octet A du PID 03 (état du système carburant)."""
		if state.coolant < 40:
			return 0x01
		if state.fuel_cut:
			return 0x04
		return 0x02 if state.closed_loop else 0x04

	def pid_bytes(self, pid: int, now: float | None = None) -> str | None:
		"""Octets de données (hexadécimal) d'une réponse Mode 01 simulée, ou None si non simulé."""
		s = self.state(now)

		def b(value: float) -> str:
			return f"{max(0, min(255, round(value))):02X}"

		if pid == 0x03:
			return f"{self.fuel_status_byte(s):02X} 00"
		if pid == 0x04:
			return b(s.load * 255 / 100)
		if pid == 0x05:
			return b(s.coolant + 40)
		if pid == 0x06:
			return b(s.stft * 128 / 100 + 128)
		if pid == 0x07:
			return b(s.ltft * 128 / 100 + 128)
		if pid == 0x0B:
			return b(s.map_kpa)
		if pid == 0x0C:
			raw = max(0, min(65535, round(s.rpm * 4)))
			return f"{raw >> 8:02X} {raw & 0xFF:02X}"
		if pid == 0x0D:
			return b(s.speed)
		if pid == 0x0E:
			return b(s.timing * 2 + 128)
		if pid == 0x0F:
			return b(s.iat + 40)
		if pid == 0x11:
			return b(s.throttle * 255 / 100)
		if pid == 0x14:
			trim = 0xFF if not s.closed_loop else round(s.stft * 128 / 100 + 128)
			return f"{b(s.o2_up / 0.005)} {max(0, min(255, trim)):02X}"
		if pid == 0x15:
			return f"{b(s.o2_down / 0.005)} FF"
		return None


SIMULATED_PIDS = (0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x14, 0x15)
