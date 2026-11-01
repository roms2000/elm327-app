"""Alertes sur seuils : déclenchement après une durée minimale, retour à la normale avec hystérésis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from obdtool.core import pids
from obdtool.core.samples import SampleBatch


@dataclass
class AlertRule:
	"""Règle : la valeur `key` passe au-dessus (`>`) ou au-dessous (`<`) du seuil."""

	key: str
	operator: str  # > ou <
	threshold: float
	enabled: bool = True
	delay_s: float = 2.0  # durée minimale au-delà du seuil avant l'alerte
	hysteresis: float = 0.0  # marge de retour à la normale
	sound: bool = True
	message: str = ""

	def describe(self) -> str:
		definition = pids.get(self.key)
		name = definition.name if definition else self.key
		unit = definition.unit if definition else ""
		symbol = "supérieure à" if self.operator == ">" else "inférieure à"
		return self.message or f"{name} {symbol} {pids.format_value(self.threshold, unit)} {unit}".strip()

	def exceeded(self, value: float) -> bool:
		return value > self.threshold if self.operator == ">" else value < self.threshold

	def cleared(self, value: float) -> bool:
		if self.operator == ">":
			return value <= self.threshold - self.hysteresis
		return value >= self.threshold + self.hysteresis


@dataclass
class AlertEvent:
	"""Changement d'état d'une alerte."""

	rule: AlertRule
	active: bool
	value: float
	t: float


@dataclass
class _RuleState:
	since: float | None = None
	active: bool = False


@dataclass
class AlertEngine:
	"""Évalue les règles sur chaque lot d'échantillons."""

	rules: list[AlertRule] = field(default_factory=list)
	_states: dict[int, _RuleState] = field(default_factory=dict)

	def set_rules(self, rules: list[AlertRule]) -> None:
		self.rules = list(rules)
		self._states.clear()

	def keys(self) -> list[str]:
		return list(dict.fromkeys(r.key for r in self.rules if r.enabled))

	def active(self) -> list[AlertRule]:
		return [r for i, r in enumerate(self.rules) if self._states.get(i, _RuleState()).active]

	def evaluate(self, batch: SampleBatch) -> list[AlertEvent]:
		events: list[AlertEvent] = []
		for sample in batch.samples:
			if sample.error or not isinstance(sample.value, int | float) or isinstance(sample.value, bool):
				continue
			value = float(sample.value)
			for index, rule in enumerate(self.rules):
				if not rule.enabled or rule.key != sample.key:
					continue
				state = self._states.setdefault(index, _RuleState())
				if not state.active:
					if rule.exceeded(value):
						state.since = sample.t if state.since is None else state.since
						if sample.t - state.since >= rule.delay_s:
							state.active = True
							events.append(AlertEvent(rule, True, value, sample.t))
					else:
						state.since = None
				elif rule.cleared(value):
					state.active = False
					state.since = None
					events.append(AlertEvent(rule, False, value, sample.t))
		return events

	def reset(self) -> None:
		self._states.clear()


def default_rules() -> list[AlertRule]:
	"""Règles proposées par défaut."""
	return [
		AlertRule("COOLANT_TEMP", ">", 105.0, delay_s=3.0, hysteresis=3.0, message="Température moteur trop élevée"),
		AlertRule("ADAPTER_VOLTAGE", "<", 11.8, delay_s=10.0, hysteresis=0.3, message="Tension batterie basse"),
		AlertRule("ADAPTER_VOLTAGE", ">", 15.2, delay_s=5.0, hysteresis=0.3, message="Surtension (régulateur ?)"),
		AlertRule("RPM", ">", 6300.0, delay_s=0.5, hysteresis=300.0, sound=False, message="Régime élevé"),
	]


def load_rules(raw: Any) -> list[AlertRule]:
	if not isinstance(raw, list):
		return default_rules()
	rules = []
	for item in raw:
		if not isinstance(item, dict) or "key" not in item or item.get("operator") not in (">", "<"):
			continue
		try:
			rules.append(
				AlertRule(
					key=str(item["key"]),
					operator=str(item["operator"]),
					threshold=float(item.get("threshold", 0)),
					enabled=bool(item.get("enabled", True)),
					delay_s=float(item.get("delay_s", 2.0)),
					hysteresis=float(item.get("hysteresis", 0.0)),
					sound=bool(item.get("sound", True)),
					message=str(item.get("message", "")),
				)
			)
		except (TypeError, ValueError):
			continue
	return rules


def dump_rules(rules: list[AlertRule]) -> list[dict[str, Any]]:
	return [asdict(rule) for rule in rules]
