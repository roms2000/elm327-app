"""Garde-fou de la console : classe les commandes selon leur risque pour le calculateur ou l'adaptateur."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Level(Enum):
	"""Niveau d'autorisation requis."""

	ALLOWED = "autorisée"
	CONFIRM = "confirmation requise"
	EXPERT = "mode expert requis"
	INVALID = "invalide"


@dataclass(frozen=True)
class Verdict:
	level: Level
	reason: str = ""


# Services de lecture (OBD-II, KWP2000, UDS) : sans effet sur le calculateur.
_READ_SERVICES = {
	0x01: "Mode 01 : valeurs en direct",
	0x02: "Mode 02 : freeze frame",
	0x03: "Mode 03 : codes mémorisés",
	0x05: "Mode 05 : sondes lambda",
	0x06: "Mode 06 : tests embarqués",
	0x07: "Mode 07 : codes en attente",
	0x09: "Mode 09 : informations véhicule",
	0x0A: "Mode 0A : codes permanents",
	0x12: "KWP : lecture freeze frame",
	0x13: "KWP : lecture des codes",
	0x17: "KWP : état des codes",
	0x18: "KWP : codes par état",
	0x19: "UDS : lecture des codes",
	0x1A: "KWP : identification du calculateur",
	0x21: "KWP : lecture par identifiant local",
	0x22: "UDS : lecture par identifiant",
	0x23: "Lecture mémoire par adresse",
	0x3E: "Présence testeur",
}
# Services à confirmer : effacement des codes (même effet que l'écran Défauts).
_CONFIRM_SERVICES = {
	0x04: "Mode 04 : effacement des codes défaut et des données associées",
	0x14: "Effacement des informations de diagnostic (KWP/UDS)",
}
# Services d'écriture, de programmation ou de commande : mode expert.
_EXPERT_SERVICES = {
	0x08: "Mode 08 : commande d'un organe du véhicule",
	0x10: "Changement de session de diagnostic",
	0x11: "Réinitialisation du calculateur",
	0x27: "Accès sécurisé (déverrouillage)",
	0x28: "Contrôle des communications",
	0x2E: "Écriture par identifiant",
	0x2F: "Commande d'entrées/sorties",
	0x30: "KWP : commande d'entrées/sorties",
	0x31: "Lancement de routine",
	0x34: "Demande de téléchargement (programmation)",
	0x35: "Demande de téléversement",
	0x36: "Transfert de données (programmation)",
	0x37: "Fin de transfert",
	0x3B: "KWP : écriture par identifiant local",
	0x3D: "Écriture mémoire par adresse",
	0x83: "Paramètres de temporisation",
	0x85: "Contrôle de l'enregistrement des codes",
	0x87: "Contrôle de la liaison (débit)",
}

# Commandes AT qui modifient durablement l'adaptateur ou coupent la liaison.
_AT_EXPERT = (
	(re.compile(r"^PP[0-9A-F]{2}(SV|ON|OFF)", re.I), "modifie un paramètre programmable (mémoire de l'adaptateur)"),
	(re.compile(r"^PP(FF)?(ON|OFF)", re.I), "modifie les paramètres programmables de l'adaptateur"),
	(re.compile(r"^@3", re.I), "écrit l'identifiant de l'adaptateur (une seule fois possible)"),
	(re.compile(r"^BR[DT]", re.I), "change le débit série : la liaison peut être perdue"),
	(re.compile(r"^LP$", re.I), "met l'adaptateur en veille : la liaison sera perdue"),
)
_AT_MONITOR = re.compile(r"^(MA|MR|MT|MP)", re.I)
# Commandes qui réinitialisent l'adaptateur : la configuration doit être rétablie ensuite.
RESET_COMMANDS = {"Z", "WS", "D"}
# Commandes qui modifient le format des réponses attendu par les autres écrans.
_AT_FORMAT = re.compile(r"^(H0|E1|L1|S0|CAF0|R0|SP|TP|SH|CRA|AR|ST|AT0)", re.I)


def normalize(command: str) -> str:
	"""Commande en majuscules, sans espaces ni retour chariot."""
	return re.sub(r"\s+", "", command.strip().upper())


def classify(command: str) -> Verdict:
	"""Niveau d'autorisation requis pour envoyer `command` depuis la console."""
	text = normalize(command)
	if not text:
		return Verdict(Level.INVALID, "commande vide")
	if text.startswith("AT"):
		body = text[2:]
		if _AT_MONITOR.match(body):
			return Verdict(Level.INVALID, "surveillance continue du bus : utilisez l'onglet « Écoute du bus »")
		for pattern, reason in _AT_EXPERT:
			if pattern.match(body):
				return Verdict(Level.EXPERT, reason)
		if body in RESET_COMMANDS:
			return Verdict(Level.ALLOWED, "réinitialise l'adaptateur (configuration rétablie automatiquement)")
		if _AT_FORMAT.match(body):
			return Verdict(Level.ALLOWED, "modifie la configuration : les autres écrans peuvent être perturbés")
		return Verdict(Level.ALLOWED, "commande de l'adaptateur")
	if text.startswith("ST"):
		return Verdict(Level.EXPERT, "commande propriétaire des puces STN")
	if not re.fullmatch(r"[0-9A-F]+", text) or len(text) % 2:
		return Verdict(Level.INVALID, "requête hexadécimale invalide (nombre pair de chiffres 0-9 A-F)")
	service = int(text[:2], 16)
	if service == 0x10 and len(text) >= 4 and text[2:4] in ("01", "81"):
		return Verdict(Level.ALLOWED, "session de diagnostic par défaut")
	if service in _READ_SERVICES:
		return Verdict(Level.ALLOWED, _READ_SERVICES[service])
	if service in _CONFIRM_SERVICES:
		return Verdict(Level.CONFIRM, _CONFIRM_SERVICES[service])
	if service in _EXPERT_SERVICES:
		return Verdict(Level.EXPERT, _EXPERT_SERVICES[service])
	return Verdict(Level.EXPERT, f"service 0x{service:02X} inconnu ou propre au constructeur")


def changes_format(command: str) -> bool:
	"""Vrai si la commande change le format des réponses (les autres écrans peuvent être perturbés)."""
	text = normalize(command)
	return text.startswith("AT") and bool(_AT_FORMAT.match(text[2:]))


def is_reset(command: str) -> bool:
	text = normalize(command)
	return text.startswith("AT") and text[2:] in RESET_COMMANDS


SHORTCUTS: tuple[tuple[str, str], ...] = (
	("ATZ", "Réinitialiser l'adaptateur"),
	("ATI", "Version"),
	("AT@1", "Description"),
	("ATRV", "Tension"),
	("ATDP", "Protocole"),
	("ATDPN", "Numéro de protocole"),
	("ATH1", "En-têtes affichés"),
	("ATH0", "En-têtes masqués"),
	("ATSH7E0", "En-tête : calculateur moteur (CAN)"),
	("ATSH7DF", "En-tête : diffusion OBD (CAN)"),
	("ATCS", "Statistiques CAN"),
	("ATPPS", "Paramètres programmables"),
	("0100", "PID supportés"),
	("03", "Codes mémorisés"),
	("0902", "VIN"),
)
