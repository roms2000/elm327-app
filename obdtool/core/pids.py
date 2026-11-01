"""Catalogue des PID du Mode 01 : noms français, catégories, unités, décodage.

Le décodage s'appuie sur python-OBD ; quelques valeurs qu'il ignore (correction carburant d'une sonde,
lambda des sondes large bande) sont décodées ici à partir des mêmes octets : une seule requête peut donc
produire plusieurs valeurs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import obd
from obd import OBDCommand

CATEGORIES: tuple[str, ...] = (
	"Moteur",
	"Papillon et pédale",
	"Carburant",
	"Air et admission",
	"Sondes lambda",
	"Températures",
	"Émissions (EGR, EVAP)",
	"Vitesse, distances et durées",
	"Électrique",
	"État",
	"Valeurs calculées",
)

Decoder = Callable[[bytes], float | str | None]


@dataclass(frozen=True)
class PidDef:
	"""Valeur affichable, issue d'une requête Mode 01 (ou d'une source spéciale)."""

	key: str
	pid: int  # -1 pour les valeurs qui ne sont pas des PID OBD (tension adaptateur, valeurs calculées)
	name: str
	category: str
	unit: str = ""
	vmin: float = 0.0
	vmax: float = 100.0
	slow: bool = False  # interrogé moins souvent (températures, niveaux, compteurs)
	numeric: bool = True
	decoder: Decoder | None = None  # décodeur propre ; sinon celui de python-OBD
	source: str = "obd"  # obd | atrv | derived

	@property
	def request(self) -> str:
		"""Requête hexadécimale (ex. « 010C »)."""
		return f"01{self.pid:02X}" if self.pid >= 0 else ""

	@property
	def command(self) -> OBDCommand | None:
		"""Commande python-OBD correspondante, si elle existe."""
		if self.pid < 0 or self.decoder is not None:
			return None
		return _MODE1.get(self.pid)


_MODE1: dict[int, OBDCommand] = {c.pid: c for c in obd.commands.modes[1] if c is not None}


def _o2_trim(data: bytes) -> float | None:
	"""Correction carburant associée à une sonde à bande étroite (octet B) ; 0xFF = non utilisée."""
	if len(data) < 2 or data[1] == 0xFF:
		return None
	return round((data[1] - 128) * 100 / 128, 2)


def _wide_lambda(data: bytes) -> float | None:
	"""Lambda (rapport d'équivalence) d'une sonde large bande, octets A-B."""
	if len(data) < 2:
		return None
	return round((data[0] * 256 + data[1]) * 2 / 65536, 4)


def _d(
	key: str, pid: int, name: str, cat: str, unit: str = "", vmin: float = 0, vmax: float = 100, **kw: Any
) -> PidDef:
	return PidDef(key, pid, name, cat, unit, float(vmin), float(vmax), **kw)


_M, _T, _F, _A, _O, _C, _E, _V, _EL, _S = (
	"Moteur",
	"Papillon et pédale",
	"Carburant",
	"Air et admission",
	"Sondes lambda",
	"Températures",
	"Émissions (EGR, EVAP)",
	"Vitesse, distances et durées",
	"Électrique",
	"État",
)

_DEFS: list[PidDef] = [
	_d("FUEL_STATUS", 0x03, "État du système carburant", _F, numeric=False, slow=True),
	_d("ENGINE_LOAD", 0x04, "Charge calculée du moteur", _M, "%", 0, 100),
	_d("COOLANT_TEMP", 0x05, "Température du liquide de refroidissement", _C, "°C", -40, 130, slow=True),
	_d("SHORT_FUEL_TRIM_1", 0x06, "Correction carburant court terme, banc 1", _F, "%", -25, 25),
	_d("LONG_FUEL_TRIM_1", 0x07, "Correction carburant long terme, banc 1", _F, "%", -25, 25, slow=True),
	_d("SHORT_FUEL_TRIM_2", 0x08, "Correction carburant court terme, banc 2", _F, "%", -25, 25),
	_d("LONG_FUEL_TRIM_2", 0x09, "Correction carburant long terme, banc 2", _F, "%", -25, 25, slow=True),
	_d("FUEL_PRESSURE", 0x0A, "Pression carburant (relative)", _F, "kPa", 0, 765),
	_d("INTAKE_PRESSURE", 0x0B, "Pression absolue collecteur (MAP)", _A, "kPa", 0, 110),
	_d("RPM", 0x0C, "Régime moteur", _M, "tr/min", 0, 7000),
	_d("SPEED", 0x0D, "Vitesse du véhicule", _V, "km/h", 0, 200),
	_d("TIMING_ADVANCE", 0x0E, "Avance à l'allumage (cylindre 1)", _M, "°", -10, 50),
	_d("INTAKE_TEMP", 0x0F, "Température d'air d'admission", _C, "°C", -40, 80, slow=True),
	_d("MAF", 0x10, "Débit d'air (débitmètre MAF)", _A, "g/s", 0, 200),
	_d("THROTTLE_POS", 0x11, "Position absolue du papillon", _T, "%", 0, 100),
	_d("AIR_STATUS", 0x12, "État de l'injection d'air secondaire", _A, numeric=False, slow=True),
	_d("OBD_COMPLIANCE", 0x1C, "Normes OBD respectées", _S, numeric=False, slow=True),
	_d("AUX_INPUT_STATUS", 0x1E, "Entrée auxiliaire (prise de force)", _S, numeric=False, slow=True),
	_d("RUN_TIME", 0x1F, "Temps depuis le démarrage du moteur", _V, "s", 0, 3600, slow=True),
	_d("DISTANCE_W_MIL", 0x21, "Distance parcourue avec voyant moteur allumé", _V, "km", 0, 1000, slow=True),
	_d("FUEL_RAIL_PRESSURE_VAC", 0x22, "Pression rampe (relative au collecteur)", _F, "kPa", 0, 5000),
	_d("FUEL_RAIL_PRESSURE_DIRECT", 0x23, "Pression rampe (injection directe)", _F, "kPa", 0, 200000),
	_d("COMMANDED_EGR", 0x2C, "EGR commandée", _E, "%", 0, 100),
	_d("EGR_ERROR", 0x2D, "Écart EGR", _E, "%", -100, 100),
	_d("EVAPORATIVE_PURGE", 0x2E, "Purge canister commandée", _E, "%", 0, 100),
	_d("FUEL_LEVEL", 0x2F, "Niveau de carburant", _F, "%", 0, 100, slow=True),
	_d("WARMUPS_SINCE_DTC_CLEAR", 0x30, "Mises en température depuis l'effacement", _V, "", 0, 255, slow=True),
	_d("DISTANCE_SINCE_DTC_CLEAR", 0x31, "Distance depuis l'effacement des codes", _V, "km", 0, 10000, slow=True),
	_d("EVAP_VAPOR_PRESSURE", 0x32, "Pression vapeur du circuit EVAP", _E, "Pa", -8192, 8192),
	_d("BAROMETRIC_PRESSURE", 0x33, "Pression atmosphérique", _A, "kPa", 70, 110, slow=True),
	_d("CATALYST_TEMP_B1S1", 0x3C, "Température catalyseur banc 1 capteur 1", _C, "°C", 0, 1000, slow=True),
	_d("CATALYST_TEMP_B2S1", 0x3D, "Température catalyseur banc 2 capteur 1", _C, "°C", 0, 1000, slow=True),
	_d("CATALYST_TEMP_B1S2", 0x3E, "Température catalyseur banc 1 capteur 2", _C, "°C", 0, 1000, slow=True),
	_d("CATALYST_TEMP_B2S2", 0x3F, "Température catalyseur banc 2 capteur 2", _C, "°C", 0, 1000, slow=True),
	_d("CONTROL_MODULE_VOLTAGE", 0x42, "Tension du calculateur", _EL, "V", 10, 16, slow=True),
	_d("ABSOLUTE_LOAD", 0x43, "Charge absolue", _M, "%", 0, 200),
	_d("COMMANDED_EQUIV_RATIO", 0x44, "Richesse commandée (lambda)", _F, "λ", 0.5, 1.5),
	_d("RELATIVE_THROTTLE_POS", 0x45, "Position relative du papillon", _T, "%", 0, 100),
	_d("AMBIANT_AIR_TEMP", 0x46, "Température extérieure", _C, "°C", -40, 60, slow=True),
	_d("THROTTLE_POS_B", 0x47, "Position absolue du papillon B", _T, "%", 0, 100),
	_d("THROTTLE_POS_C", 0x48, "Position absolue du papillon C", _T, "%", 0, 100),
	_d("ACCELERATOR_POS_D", 0x49, "Position pédale d'accélérateur D", _T, "%", 0, 100),
	_d("ACCELERATOR_POS_E", 0x4A, "Position pédale d'accélérateur E", _T, "%", 0, 100),
	_d("ACCELERATOR_POS_F", 0x4B, "Position pédale d'accélérateur F", _T, "%", 0, 100),
	_d("THROTTLE_ACTUATOR", 0x4C, "Commande du moteur de papillon", _T, "%", 0, 100),
	_d("RUN_TIME_MIL", 0x4D, "Temps moteur avec voyant allumé", _V, "min", 0, 1000, slow=True),
	_d("TIME_SINCE_DTC_CLEARED", 0x4E, "Temps moteur depuis l'effacement des codes", _V, "min", 0, 10000, slow=True),
	_d("FUEL_TYPE", 0x51, "Type de carburant", _F, numeric=False, slow=True),
	_d("ETHANOL_PERCENT", 0x52, "Taux d'éthanol", _F, "%", 0, 100, slow=True),
	_d("EVAP_VAPOR_PRESSURE_ABS", 0x53, "Pression vapeur EVAP (absolue)", _E, "kPa", 0, 330),
	_d("EVAP_VAPOR_PRESSURE_ALT", 0x54, "Pression vapeur EVAP (alternative)", _E, "Pa", -32768, 32768),
	_d("SHORT_O2_TRIM_B1", 0x55, "Correction sonde secondaire court terme, banc 1", _O, "%", -100, 100),
	_d("LONG_O2_TRIM_B1", 0x56, "Correction sonde secondaire long terme, banc 1", _O, "%", -100, 100, slow=True),
	_d("SHORT_O2_TRIM_B2", 0x57, "Correction sonde secondaire court terme, banc 2", _O, "%", -100, 100),
	_d("LONG_O2_TRIM_B2", 0x58, "Correction sonde secondaire long terme, banc 2", _O, "%", -100, 100, slow=True),
	_d("FUEL_RAIL_PRESSURE_ABS", 0x59, "Pression rampe (absolue)", _F, "kPa", 0, 200000),
	_d("RELATIVE_ACCEL_POS", 0x5A, "Position relative de la pédale", _T, "%", 0, 100),
	_d("HYBRID_BATTERY_REMAINING", 0x5B, "Charge restante batterie hybride", _EL, "%", 0, 100, slow=True),
	_d("OIL_TEMP", 0x5C, "Température d'huile moteur", _C, "°C", -40, 150, slow=True),
	_d("FUEL_INJECT_TIMING", 0x5D, "Calage de l'injection", _M, "°", -210, 302),
	_d("FUEL_RATE", 0x5E, "Débit de carburant (calculateur)", _F, "L/h", 0, 50),
	_d("ADAPTER_VOLTAGE", -1, "Tension batterie (mesure de l'adaptateur)", _EL, "V", 10, 16, slow=True, source="atrv"),
]

# Sondes à bande étroite : tension (python-OBD) et correction carburant associée (octet B).
for _i, (_bank, _sensor) in enumerate([(b, s) for b in (1, 2) for s in (1, 2, 3, 4)]):
	_pid = 0x14 + _i
	_where = f"banc {_bank} capteur {_sensor}" + (" (amont)" if _sensor == 1 else " (aval)" if _sensor == 2 else "")
	_DEFS.append(_d(f"O2_B{_bank}S{_sensor}", _pid, f"Tension sonde lambda {_where}", _O, "V", 0, 1.1))
	_DEFS.append(
		_d(
			f"O2_B{_bank}S{_sensor}_TRIM",
			_pid,
			f"Correction carburant sonde {_where}",
			_O,
			"%",
			-100,
			100,
			decoder=_o2_trim,
		)
	)

# Sondes large bande : tension ou courant (python-OBD) et lambda (octets A-B).
for _n in range(1, 9):
	_DEFS.append(_d(f"O2_S{_n}_WR_VOLTAGE", 0x23 + _n, f"Sonde large bande {_n} : tension", _O, "V", 0, 8))
	_DEFS.append(
		_d(
			f"O2_S{_n}_WR_LAMBDA",
			0x23 + _n,
			f"Sonde large bande {_n} : lambda",
			_O,
			"λ",
			0.5,
			1.5,
			decoder=_wide_lambda,
		)
	)
	_DEFS.append(_d(f"O2_S{_n}_WR_CURRENT", 0x33 + _n, f"Sonde large bande {_n} : courant", _O, "mA", -128, 128))
	_DEFS.append(
		_d(
			f"O2_S{_n}_WRC_LAMBDA",
			0x33 + _n,
			f"Sonde large bande {_n} : lambda (courant)",
			_O,
			"λ",
			0.5,
			1.5,
			decoder=_wide_lambda,
		)
	)

CATALOG: dict[str, PidDef] = {d.key: d for d in _DEFS}

# PID qui ne sont pas des valeurs « en direct » : traités par d'autres écrans.
NON_LIVE_PIDS = {0x00, 0x01, 0x02, 0x13, 0x1D, 0x20, 0x40, 0x41, 0x4F, 0x50, 0x5F, 0x60, 0x80, 0xA0, 0xC0}

# Sélection par défaut, adaptée à un moteur essence à capteur MAP (ex. Renault K4M).
DEFAULT_SELECTION: tuple[str, ...] = (
	"RPM",
	"SPEED",
	"ENGINE_LOAD",
	"COOLANT_TEMP",
	"INTAKE_TEMP",
	"INTAKE_PRESSURE",
	"MAF",
	"THROTTLE_POS",
	"TIMING_ADVANCE",
	"SHORT_FUEL_TRIM_1",
	"LONG_FUEL_TRIM_1",
	"O2_B1S1",
	"O2_B1S2",
	"FUEL_STATUS",
	"ADAPTER_VOLTAGE",
)


def get(key: str) -> PidDef | None:
	"""Définition d'une valeur par sa clé."""
	return CATALOG.get(key)


def defs_for_pid(pid: int) -> list[PidDef]:
	"""Toutes les valeurs issues d'un même PID."""
	return [d for d in CATALOG.values() if d.pid == pid]


def available(supported: set[int]) -> list[PidDef]:
	"""Valeurs disponibles compte tenu des PID supportés par le calculateur."""
	return [d for d in CATALOG.values() if d.source != "obd" or d.pid in supported]


def decode_supported_bitmap(block_start: int, data: bytes) -> set[int]:
	"""Décode un masque de PID supportés (réponse à 0100, 0120…) : 4 octets après mode et PID."""
	result = set()
	for byte_index, byte in enumerate(data[:4]):
		for bit in range(8):
			if byte & (0x80 >> bit):
				result.add(block_start + byte_index * 8 + bit + 1)
	return result


def format_value(value: float | str | None, unit: str = "", decimals: int | None = None) -> str:
	"""Formate une valeur pour l'affichage."""
	if value is None:
		return "—"
	if isinstance(value, str):
		return value
	if decimals is None:
		magnitude = abs(value)
		decimals = 0 if magnitude >= 100 else 1 if magnitude >= 10 else 2 if magnitude >= 1 else 3
		if unit in ("tr/min", "km/h", "kPa", "s", "km", "min", "Pa"):
			decimals = 0
	text = f"{value:.{decimals}f}".replace(".", ",")
	return text
