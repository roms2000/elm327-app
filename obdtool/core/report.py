"""Rapport de défauts exportable (texte et HTML)."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime

from obdtool import APP_DISPLAY_NAME, __version__
from obdtool.core import pids
from obdtool.core.dtc import DtcEntry, DtcKind, FreezeFrame, MilStatus
from obdtool.core.onboard_tests import TestsReport
from obdtool.core.readiness import MonitorState, ReadinessReport
from obdtool.core.vehicle_info import VehicleInfo


@dataclass
class DtcReport:
	"""Contenu d'un rapport de défauts."""

	vehicle: str = ""
	vin: str = ""
	adapter: str = ""
	protocol: str = ""
	mil: MilStatus | None = None
	entries: dict[DtcKind, list[DtcEntry] | None] = field(default_factory=dict)
	freeze: FreezeFrame | None = None
	recurring: dict[str, int] = field(default_factory=dict)
	distance_since_clear: float | None = None
	distance_with_mil: float | None = None
	created: datetime = field(default_factory=datetime.now)
	# Bilan complet (facultatif)
	readiness: ReadinessReport | None = None
	tests: TestsReport | None = None
	vehicle_info: VehicleInfo | None = None


def _value(definition: pids.PidDef, value: float | str | None) -> str:
	text = pids.format_value(value, definition.unit)
	return f"{text} {definition.unit}".strip() if value is not None else "—"


def to_text(report: DtcReport) -> str:
	"""Rapport au format texte."""
	lines = [
		f"Rapport de défauts — {report.created:%d/%m/%Y %H:%M}",
		f"Généré par {APP_DISPLAY_NAME} {__version__}",
		"",
		f"Véhicule   : {report.vehicle or '—'}",
		f"VIN        : {report.vin or 'non disponible'}",
		f"Adaptateur : {report.adapter or '—'}",
		f"Protocole  : {report.protocol or '—'}",
	]
	if report.mil is not None:
		lines.append(
			f"Voyant MIL : {'ALLUMÉ' if report.mil.mil_on else 'éteint'} ({report.mil.dtc_count} défaut(s) signalé(s))"
		)
	if report.distance_since_clear is not None:
		lines.append(f"Distance depuis l'effacement des codes : {report.distance_since_clear:.0f} km")
	if report.distance_with_mil is not None:
		lines.append(f"Distance parcourue voyant allumé : {report.distance_with_mil:.0f} km")
	for kind in DtcKind:
		lines.append("")
		entries = report.entries.get(kind)
		title = f"Codes {kind.plural} (mode {kind.mode})"
		lines.append(title)
		lines.append("-" * len(title))
		if entries is None:
			lines.append("Non supporté par le calculateur ou non lu.")
		elif not entries:
			lines.append("Aucun code.")
		for entry in entries or []:
			recur = report.recurring.get(entry.code)
			extra = f"  [revenu après {recur} effacement(s)]" if recur else ""
			lines.append(f"{entry.code}  {entry.description.display}{extra}")
	lines.append("")
	lines.append("Freeze frame (mode 02)")
	lines.append("----------------------")
	freeze = report.freeze
	if freeze is None:
		lines.append("Non supporté ou non lu.")
	elif not freeze.dtc:
		lines.append("Aucun instantané mémorisé.")
	else:
		description = freeze.description.display if freeze.description else ""
		lines.append(f"Déclenché par : {freeze.dtc} {description}")
		for definition, value in freeze.values:
			lines.append(f"  {definition.name:<55} {_value(definition, value)}")
	return "\n".join(lines) + "\n"


def to_html(report: DtcReport) -> str:
	"""Rapport au format HTML autonome (imprimable)."""

	def esc(text: object) -> str:
		return html.escape(str(text))

	rows = []
	for label, value in (
		("Véhicule", report.vehicle or "—"),
		("VIN", report.vin or "non disponible"),
		("Adaptateur", report.adapter or "—"),
		("Protocole", report.protocol or "—"),
	):
		rows.append(f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>")
	if report.mil is not None:
		state = "<b style='color:#c62828'>ALLUMÉ</b>" if report.mil.mil_on else "éteint"
		rows.append(f"<tr><th>Voyant MIL</th><td>{state} ({report.mil.dtc_count} défaut(s) signalé(s))</td></tr>")
	if report.distance_since_clear is not None:
		rows.append(f"<tr><th>Distance depuis l'effacement</th><td>{report.distance_since_clear:.0f} km</td></tr>")
	if report.distance_with_mil is not None:
		rows.append(f"<tr><th>Distance voyant allumé</th><td>{report.distance_with_mil:.0f} km</td></tr>")
	sections = []
	for kind in DtcKind:
		entries = report.entries.get(kind)
		body = ""
		if entries is None:
			body = "<p><i>Non supporté par le calculateur ou non lu.</i></p>"
		elif not entries:
			body = "<p>Aucun code.</p>"
		else:
			items = []
			for entry in entries:
				recur = report.recurring.get(entry.code)
				badge = f" <span class='recur'>revenu après {recur} effacement(s)</span>" if recur else ""
				items.append(
					f"<tr><td class='code'>{esc(entry.code)}</td><td>{esc(entry.description.display)}{badge}</td></tr>"
				)
			body = "<table class='codes'>" + "".join(items) + "</table>"
		sections.append(f"<h2>Codes {esc(kind.plural)} <small>(mode {kind.mode})</small></h2>{body}")
	freeze = report.freeze
	if freeze is None:
		freeze_html = "<p><i>Non supporté ou non lu.</i></p>"
	elif not freeze.dtc:
		freeze_html = "<p>Aucun instantané mémorisé.</p>"
	else:
		desc = freeze.description.display if freeze.description else ""
		values = "".join(f"<tr><th>{esc(d.name)}</th><td>{esc(_value(d, v))}</td></tr>" for d, v in freeze.values)
		freeze_html = f"<p>Déclenché par <b>{esc(freeze.dtc)}</b> {esc(desc)}</p><table>{values}</table>"
	return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Rapport de défauts</title>
<style>
body {{ font-family: sans-serif; margin: 2em; color: #222; }}
table {{ border-collapse: collapse; margin-bottom: 1em; }}
th, td {{ text-align: left; padding: 4px 10px; border-bottom: 1px solid #ddd; vertical-align: top; }}
th {{ color: #555; font-weight: normal; }}
td.code {{ font-family: monospace; font-weight: bold; font-size: 1.1em; }}
.recur {{ background: #fff3cd; color: #8a6d00; padding: 1px 6px; border-radius: 4px; font-size: 0.9em; }}
small {{ color: #777; font-weight: normal; }}
</style></head><body>
<h1>Rapport de défauts</h1>
<p>{report.created:%d/%m/%Y %H:%M} — {esc(APP_DISPLAY_NAME)} {esc(__version__)}</p>
<table>{"".join(rows)}</table>
{"".join(sections)}
<h2>Freeze frame <small>(mode 02)</small></h2>
{freeze_html}
{_full_sections(report, esc)}
</body></html>
"""


def _full_sections(report: DtcReport, esc) -> str:  # type: ignore[no-untyped-def]
	"""Sections du bilan complet : moniteurs, tests embarqués, identification."""
	parts = []
	if report.vehicle_info is not None:
		info = report.vehicle_info
		rows = [
			("CAL ID", ", ".join(info.calibration_ids or []) or "non supporté"),
			("CVN", ", ".join(info.cvns or []) or "non supporté"),
			("Calculateur", info.ecu_name or "non supporté"),
			("Sondes lambda", info.o2_sensors or "non supporté"),
		]
		for key, value in info.mode01.items():
			definition = pids.CATALOG[key]
			rows.append((definition.name, "non supporté" if value is None else _value(definition, value)))
		parts.append(
			"<h2>Identification</h2><table>"
			+ "".join(f"<tr><th>{esc(a)}</th><td>{esc(b)}</td></tr>" for a, b in rows)
			+ "</table>"
		)
	if report.readiness is not None:
		ready = report.readiness.ready_for_inspection()
		verdict = "<b style='color:#2e7d32'>OUI</b>" if ready else "<b style='color:#c62828'>NON</b>"
		rows = "".join(
			f"<tr><th>{esc(m.name)}</th><td>{esc(m.state.value)}</td></tr>"
			for m in report.readiness.monitors
			if m.state != MonitorState.UNSUPPORTED
		)
		parts.append(
			f"<h2>Moniteurs de préparation</h2><p>Prêt pour un contrôle anti-pollution : {verdict}</p><table>{rows}</table>"
		)
	if report.tests is not None:
		if report.tests.unavailable:
			body = f"<p><i>{esc(report.tests.unavailable)}</i></p>"
		else:
			rows = "".join(
				f"<tr><td>{esc(r.mid_name)}</td><td>{esc(r.tid_name)}</td><td>{esc(pids.format_value(r.value))}</td>"
				f"<td>{esc(pids.format_value(r.minimum))} – {esc(pids.format_value(r.maximum))}</td><td>{esc(r.unit)}</td>"
				f"<td>{esc(r.status)}</td></tr>"
				for r in report.tests.results
			)
			body = (
				"<table><tr><th>Moniteur</th><th>Test</th><th>Valeur</th><th>Limites</th><th>Unité</th><th>Statut</th></tr>"
				+ rows
				+ "</table>"
			)
		parts.append(f"<h2>Tests embarqués <small>(mode 06)</small></h2>{body}")
	return "\n".join(parts)
