"""Tests des mises en page du tableau de bord et du moteur d'alertes."""

from __future__ import annotations

from obdtool.core.alerts import AlertEngine, AlertRule, default_rules, dump_rules, load_rules
from obdtool.core.dashboard import DashboardLayout, GaugeConfig, Zone, default_layouts, dump_layouts, load_layouts
from obdtool.core.samples import Sample, SampleBatch


def test_layout_roundtrip_and_defaults() -> None:
	layouts = default_layouts()
	assert [layout.name for layout in layouts] == ["Conduite", "Diagnostic moteur", "Sondes lambda"]
	restored = load_layouts(dump_layouts(layouts))
	assert [layout.to_dict() for layout in restored] == [layout.to_dict() for layout in layouts]
	assert load_layouts(None)[0].name == "Conduite"
	assert load_layouts([{"name": "Vide", "gauges": [{"pas_de_cle": 1}], "columns": 99}])[0].columns == 6


def test_gauge_bounds_and_zones() -> None:
	gauge = GaugeConfig("COOLANT_TEMP", "bar", zones=[Zone(105, 130, "rouge")])
	assert gauge.bounds() == (-40.0, 130.0)  # bornes du catalogue
	assert gauge.zone_color(110) == "#d03b3b"
	assert gauge.zone_color(90) is None
	assert GaugeConfig("RPM", vmin=10, vmax=5).bounds() == (10, 11)
	layout = DashboardLayout("Test", 2, [GaugeConfig("RPM"), GaugeConfig("RPM"), GaugeConfig("SPEED")])
	assert layout.keys() == ["RPM", "SPEED"]


def _batch(key: str, *points: tuple[float, float]) -> SampleBatch:
	return SampleBatch([Sample(key, t, v) for t, v in points])


def test_alert_delay_and_hysteresis() -> None:
	rule = AlertRule("COOLANT_TEMP", ">", 105.0, delay_s=2.0, hysteresis=3.0)
	engine = AlertEngine([rule])
	assert engine.evaluate(_batch("COOLANT_TEMP", (0, 106), (1, 107))) == []
	events = engine.evaluate(_batch("COOLANT_TEMP", (2.5, 107)))
	assert len(events) == 1 and events[0].active
	assert engine.active() == [rule]
	assert engine.evaluate(_batch("COOLANT_TEMP", (3, 104))) == []  # dans la marge
	events = engine.evaluate(_batch("COOLANT_TEMP", (4, 101.5)))
	assert len(events) == 1 and not events[0].active
	assert engine.active() == []
	# Un dépassement bref ne déclenche rien.
	assert engine.evaluate(_batch("COOLANT_TEMP", (10, 110), (11, 90), (12, 110), (13, 90))) == []


def test_alert_below_and_ignored_values() -> None:
	rule = AlertRule("ADAPTER_VOLTAGE", "<", 11.8, delay_s=0)
	engine = AlertEngine([rule, AlertRule("RPM", ">", 1, enabled=False)])
	batch = SampleBatch([Sample("ADAPTER_VOLTAGE", 0, None, "erreur"), Sample("RPM", 0, 9000.0)])
	assert engine.evaluate(batch) == []
	assert engine.evaluate(_batch("ADAPTER_VOLTAGE", (1, 11.2)))[0].active
	assert engine.keys() == ["ADAPTER_VOLTAGE"]
	assert "inférieure" in rule.describe()


def test_rules_roundtrip() -> None:
	rules = default_rules()
	assert load_rules(dump_rules(rules)) == rules
	assert load_rules([{"key": "RPM", "operator": "=", "threshold": 1}]) == []
	assert load_rules("n'importe quoi") == default_rules()
