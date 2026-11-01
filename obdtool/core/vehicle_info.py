"""Informations véhicule : Mode 09 (VIN, CAL ID, CVN, nom du calculateur, compteurs) et PID d'état du Mode 01."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import obd

from obdtool.core import pids
from obdtool.core.elm_link import ElmLink
from obdtool.core.errors import ObdError
from obdtool.core.poller import decode_messages

log = logging.getLogger(__name__)

IPT_SPARK = (
	"OBDCOND",
	"IGNCNTR",
	"CATCOMP1",
	"CATCOND1",
	"CATCOMP2",
	"CATCOND2",
	"O2SCOMP1",
	"O2SCOND1",
	"O2SCOMP2",
	"O2SCOND2",
	"EGRCOMP",
	"EGRCOND",
	"AIRCOMP",
	"AIRCOND",
	"EVAPCOMP",
	"EVAPCOND",
	"SO2SCOMP1",
	"SO2SCOND1",
	"SO2SCOMP2",
	"SO2SCOND2",
)
IPT_RATIOS = {
	"CAT1": ("Catalyseur banc 1", "CATCOMP1", "CATCOND1"),
	"CAT2": ("Catalyseur banc 2", "CATCOMP2", "CATCOND2"),
	"O2S1": ("Sonde amont banc 1", "O2SCOMP1", "O2SCOND1"),
	"O2S2": ("Sonde amont banc 2", "O2SCOMP2", "O2SCOND2"),
	"EGR": ("EGR / distribution variable", "EGRCOMP", "EGRCOND"),
	"AIR": ("Air secondaire", "AIRCOMP", "AIRCOND"),
	"EVAP": ("EVAP", "EVAPCOMP", "EVAPCOND"),
	"SO2S1": ("Sonde aval banc 1", "SO2SCOMP1", "SO2SCOND1"),
	"SO2S2": ("Sonde aval banc 2", "SO2SCOMP2", "SO2SCOND2"),
}

# PID du Mode 01 affichés sur l'écran Infos véhicule.
INFO_PIDS = (
	"OBD_COMPLIANCE",
	"FUEL_TYPE",
	"DISTANCE_W_MIL",
	"DISTANCE_SINCE_DTC_CLEAR",
	"RUN_TIME_MIL",
	"TIME_SINCE_DTC_CLEARED",
	"WARMUPS_SINCE_DTC_CLEAR",
	"RUN_TIME",
	"AUX_INPUT_STATUS",
)


@dataclass
class VehicleInfo:
	"""Informations lues sur le véhicule (None = non supporté)."""

	mode09_supported: list[int] = field(default_factory=list)
	vin: str | None = None
	calibration_ids: list[str] | None = None
	cvns: list[str] | None = None
	ecu_name: str | None = None
	ipt: dict[str, int] | None = None
	mode01: dict[str, float | str | None] = field(default_factory=dict)
	o2_sensors: str | None = None


def _ascii(data: bytes) -> str:
	return "".join(chr(b) for b in data if 32 <= b < 127).strip()


def _payload(messages: list, pid: int) -> bytes:
	"""Données d'une réponse 49 PID [nombre] … (CAN : un octet « nombre d'éléments » après le PID)."""
	result = b""
	for message in messages:
		data = bytes(message.data)
		if len(data) >= 3 and data[0] == 0x49 and data[1] == pid:
			result += data[3:] if len(data) > 3 else b""
	return result


def decode_ipt(data: bytes) -> dict[str, int]:
	"""Compteurs de performance en utilisation (PID 0908), paires d'octets."""
	counters = {}
	for index, name in enumerate(IPT_SPARK):
		if 2 * index + 1 < len(data):
			counters[name] = data[2 * index] * 256 + data[2 * index + 1]
	return counters


def ipt_ratios(counters: dict[str, int]) -> list[tuple[str, int, int, float | None]]:
	"""(moniteur, réalisations, conditions, ratio) pour chaque moniteur présent."""
	rows = []
	for label, comp, cond in IPT_RATIOS.values():
		if comp in counters and cond in counters and (counters[comp] or counters[cond]):
			done, conditions = counters[comp], counters[cond]
			rows.append((label, done, conditions, round(done / conditions, 3) if conditions else None))
	return rows


def read_vehicle_info(link: ElmLink) -> VehicleInfo:
	"""Lit les informations disponibles (chaque élément absent reste à None)."""
	info = VehicleInfo()
	try:
		messages = link.request("0900")
		data = bytes(messages[0].data)
		if len(data) >= 6:
			info.mode09_supported = sorted(pids.decode_supported_bitmap(0, data[2:6]))
	except ObdError as exc:
		if exc.is_link_lost:
			raise
	supported = set(info.mode09_supported)

	def query(command: obd.OBDCommand) -> object | None:
		try:
			return link.query(command).value
		except ObdError as exc:
			if exc.is_link_lost:
				raise
		except Exception as exc:
			log.debug("Décodage %s impossible : %s", command.name, exc)
		return None

	if 0x02 in supported or not supported:
		try:
			info.vin = _ascii(_payload(link.request("0902"), 0x02)) or None
		except ObdError as exc:
			if exc.is_link_lost:
				raise
		if info.vin is None or len(info.vin) != 17:
			vin = query(obd.commands.VIN)
			if vin:
				info.vin = _ascii(bytes(vin)) if isinstance(vin, bytes | bytearray) else str(vin).strip()
	if 0x04 in supported:
		calid = query(obd.commands.CALIBRATION_ID)
		if calid:
			text = _ascii(bytes(calid)) if isinstance(calid, bytes | bytearray) else str(calid)
			info.calibration_ids = [
				text[i : i + 16].strip() for i in range(0, len(text), 16) if text[i : i + 16].strip()
			]
	if 0x06 in supported:
		cvn = query(obd.commands.CVN)
		if cvn:
			info.cvns = [str(cvn)] if not isinstance(cvn, list | tuple) else [str(c) for c in cvn]
	for pid, attribute in ((0x0A, "ecu_name"), (0x08, "ipt")):
		if pid not in supported:
			continue
		try:
			data = _payload(link.request(f"09{pid:02X}"), pid)
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			continue
		if attribute == "ecu_name":
			info.ecu_name = _ascii(data).replace("\x00", "") or None
		else:
			info.ipt = decode_ipt(data)
	for key in INFO_PIDS:
		definition = pids.CATALOG[key]
		try:
			info.mode01[key] = decode_messages(definition, link.request(definition.request))
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			info.mode01[key] = None
		except Exception:
			info.mode01[key] = None
	for pid in (0x13, 0x1D):
		try:
			messages = link.request(f"01{pid:02X}")
		except ObdError as exc:
			if exc.is_link_lost:
				raise
			continue
		data = bytes(messages[0].data)
		if len(data) >= 3 and data[1] == pid:
			info.o2_sensors = describe_o2_sensors(pid, data[2])
			break
	return info


def describe_o2_sensors(pid: int, mask: int) -> str:
	"""Sondes lambda présentes : PID 13 (2 bancs × 4 sondes) ou PID 1D (4 bancs × 2 sondes)."""
	per_bank = 4 if pid == 0x13 else 2
	present = []
	for bit in range(8):
		if mask & (1 << bit):
			bank, sensor = bit // per_bank + 1, bit % per_bank + 1
			position = " (amont)" if sensor == 1 else " (aval)" if sensor == 2 else ""
			present.append(f"banc {bank} capteur {sensor}{position}")
	return ", ".join(present) if present else "aucune"
