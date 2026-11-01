"""Erreurs de communication et analyse des réponses d'erreur de l'ELM327."""

from __future__ import annotations

from enum import Enum


class ErrorKind(Enum):
	"""Catégories d'erreurs, chacune associée à un message pour l'utilisateur."""

	NOT_CONNECTED = "Pas de connexion à l'adaptateur."
	ADAPTER_LOST = "L'adaptateur ne répond plus (débranché ?)."
	PORT_ERROR = "Impossible d'ouvrir le port."
	PERMISSION = "Accès au port refusé (droits insuffisants)."
	TIMEOUT = "Pas de réponse de l'adaptateur dans le délai imparti."
	NO_DATA = "Le calculateur n'a pas répondu à cette demande (NO DATA)."
	UNSUPPORTED = "Fonction non supportée par le calculateur."
	CAN_ERROR = "Erreur sur le bus CAN : contact coupé, prise mal branchée ou mauvais protocole."
	BUS_ERROR = "Erreur sur le bus de diagnostic."
	BUS_INIT = "Échec de l'initialisation du bus (protocoles K-Line)."
	UNABLE_TO_CONNECT = "Impossible de dialoguer avec le véhicule (contact coupé ou protocole non reconnu)."
	STOPPED = "Requête interrompue par l'adaptateur (STOPPED)."
	BUFFER_FULL = "Mémoire de l'adaptateur saturée (BUFFER FULL)."
	UNKNOWN_COMMAND = "Commande non reconnue par l'adaptateur (?)."
	LOW_VOLTAGE = "Tension trop faible : l'adaptateur a redémarré (LV RESET)."
	NEGATIVE_RESPONSE = "Réponse négative du calculateur."
	INVALID_RESPONSE = "Réponse illisible de l'adaptateur (clone défaillant ?)."
	BLOCKED = "Commande bloquée par sécurité."
	CANCELLED = "Opération annulée."
	INTERNAL = "Erreur interne."


class ObdError(Exception):
	"""Erreur de communication, avec un message en français destiné à l'utilisateur."""

	def __init__(self, kind: ErrorKind, detail: str = "", raw: list[str] | None = None) -> None:
		self.kind = kind
		self.detail = detail
		self.raw = raw or []
		super().__init__(self.user_message)

	@property
	def user_message(self) -> str:
		return f"{self.kind.value} {self.detail}".strip()

	@property
	def is_link_lost(self) -> bool:
		"""Vrai si la liaison avec l'adaptateur est perdue."""
		return self.kind in (ErrorKind.ADAPTER_LOST, ErrorKind.NOT_CONNECTED)


# Ordre important : les motifs les plus spécifiques d'abord.
_PATTERNS: tuple[tuple[str, ErrorKind], ...] = (
	("UNABLE TO CONNECT", ErrorKind.UNABLE_TO_CONNECT),
	("BUS INIT", ErrorKind.BUS_INIT),
	("CAN ERROR", ErrorKind.CAN_ERROR),
	("BUS ERROR", ErrorKind.BUS_ERROR),
	("BUS BUSY", ErrorKind.BUS_ERROR),
	("FB ERROR", ErrorKind.BUS_ERROR),
	("DATA ERROR", ErrorKind.BUS_ERROR),
	("RX ERROR", ErrorKind.BUS_ERROR),
	("ERR", ErrorKind.BUS_ERROR),
	("BUFFER FULL", ErrorKind.BUFFER_FULL),
	("LV RESET", ErrorKind.LOW_VOLTAGE),
	("STOPPED", ErrorKind.STOPPED),
	("NO DATA", ErrorKind.NO_DATA),
	("ACT ALERT", ErrorKind.LOW_VOLTAGE),
)

_NOISE = ("SEARCHING...", "SEARCHING", "BUS INIT: ...OK", "BUS INIT: OK", "BUS INIT...OK")


def clean_lines(lines: list[str]) -> list[str]:
	"""Retire les lignes de bruit (SEARCHING..., init OK) d'une réponse."""
	result = []
	for line in lines:
		stripped = line.strip()
		upper = stripped.upper()
		if not stripped or upper in _NOISE:
			continue
		if upper.startswith("SEARCHING"):
			stripped = stripped[len("SEARCHING...") :].strip()
			if not stripped:
				continue
		result.append(stripped)
	return result


def classify(lines: list[str]) -> ErrorKind | None:
	"""Détecte une réponse d'erreur de l'ELM327. Retourne None si la réponse contient des données."""
	cleaned = clean_lines(lines)
	if not cleaned:
		return None
	data_lines = [line for line in cleaned if not _error_kind(line)]
	if data_lines:
		return None
	return _error_kind(cleaned[0])


def _error_kind(line: str) -> ErrorKind | None:
	upper = line.upper().strip()
	if upper == "?":
		return ErrorKind.UNKNOWN_COMMAND
	for pattern, kind in _PATTERNS:
		if pattern == "ERR":
			if upper.startswith("ERR") and upper[3:].strip().isdigit():
				return kind
			continue
		if pattern in upper:
			return kind
	return None
