"""Génère obdtool/core/data/dtc_fr.json : descriptions françaises des codes SAE génériques courants.

Les libellés suivent SAE J2012 / ISO 15031-6 ; les codes absents de ce fichier sont décrits en anglais
(base de python-OBD).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "obdtool" / "core" / "data" / "dtc_fr.json"


def build() -> dict[str, str]:
	d: dict[str, str] = {}
	# Distribution variable
	for base, bank in ((0x10, 1), (0x20, 2)):
		for offset, cam in ((0, "A"), (3, "B")):
			d[f"P{base + offset:04X}"] = f"Actionneur de position d'arbre à cames {cam}, banc {bank} : circuit"
			d[f"P{base + offset + 1:04X}"] = (
				f"Arbre à cames {cam}, banc {bank} : calage trop avancé ou performance du système"
			)
			d[f"P{base + offset + 2:04X}"] = f"Arbre à cames {cam}, banc {bank} : calage trop retardé"
	for code, bank, cam in (("P0016", 1, "A"), ("P0017", 1, "B"), ("P0018", 2, "A"), ("P0019", 2, "B")):
		d[code] = f"Corrélation position vilebrequin / arbre à cames (banc {bank}, capteur {cam})"
	# Réchauffage des sondes lambda
	heaters = {0x30: (1, 1), 0x36: (1, 2), 0x42: (1, 3), 0x50: (2, 1), 0x56: (2, 2), 0x62: (2, 3)}
	for base, (bank, sensor) in heaters.items():
		where = f"banc {bank} capteur {sensor}"
		d[f"P{base:04X}"] = f"Sonde lambda {where} : circuit de commande du réchauffeur"
		d[f"P{base + 1:04X}"] = f"Sonde lambda {where} : circuit de commande du réchauffeur, niveau bas"
		d[f"P{base + 2:04X}"] = f"Sonde lambda {where} : circuit de commande du réchauffeur, niveau haut"
	for code, (bank, sensor) in {
		"P0053": (1, 1),
		"P0054": (1, 2),
		"P0055": (1, 3),
		"P0059": (2, 1),
		"P0060": (2, 2),
		"P0061": (2, 3),
	}.items():
		d[code] = f"Sonde lambda banc {bank} capteur {sensor} : résistance du réchauffeur"
	d["P0040"] = "Signaux des sondes lambda inversés : banc 1 capteur 1 / banc 2 capteur 1"
	d["P0041"] = "Signaux des sondes lambda inversés : banc 1 capteur 2 / banc 2 capteur 2"
	# Air et carburant
	simple = {
		"P0100": "Débitmètre d'air (MAF) : circuit",
		"P0101": "Débitmètre d'air (MAF) : plage ou performance",
		"P0102": "Débitmètre d'air (MAF) : signal trop bas",
		"P0103": "Débitmètre d'air (MAF) : signal trop haut",
		"P0104": "Débitmètre d'air (MAF) : signal intermittent",
		"P0105": "Capteur de pression collecteur (MAP) / pression atmosphérique : circuit",
		"P0106": "Capteur de pression collecteur (MAP) / pression atmosphérique : plage ou performance",
		"P0107": "Capteur de pression collecteur (MAP) / pression atmosphérique : signal trop bas",
		"P0108": "Capteur de pression collecteur (MAP) / pression atmosphérique : signal trop haut",
		"P0109": "Capteur de pression collecteur (MAP) / pression atmosphérique : signal intermittent",
		"P0110": "Capteur de température d'air d'admission 1 : circuit",
		"P0111": "Capteur de température d'air d'admission 1 : plage ou performance",
		"P0112": "Capteur de température d'air d'admission 1 : signal trop bas",
		"P0113": "Capteur de température d'air d'admission 1 : signal trop haut",
		"P0114": "Capteur de température d'air d'admission 1 : signal intermittent",
		"P0115": "Capteur de température de liquide de refroidissement : circuit",
		"P0116": "Capteur de température de liquide de refroidissement : plage ou performance",
		"P0117": "Capteur de température de liquide de refroidissement : signal trop bas",
		"P0118": "Capteur de température de liquide de refroidissement : signal trop haut",
		"P0119": "Capteur de température de liquide de refroidissement : signal intermittent",
		"P0125": "Température de liquide insuffisante pour la régulation de richesse en boucle fermée",
		"P0126": "Température de liquide insuffisante pour un fonctionnement stable",
		"P0127": "Température d'air d'admission trop élevée",
		"P0128": "Thermostat : température de liquide inférieure à la température de régulation",
		"P0129": "Pression atmosphérique trop basse",
		"P0148": "Erreur de débit de carburant",
		"P0149": "Erreur de calage de l'injection",
		"P0168": "Température de carburant trop élevée",
		"P0169": "Composition du carburant incorrecte",
		"P0170": "Correction de richesse, banc 1",
		"P0171": "Mélange trop pauvre, banc 1",
		"P0172": "Mélange trop riche, banc 1",
		"P0173": "Correction de richesse, banc 2",
		"P0174": "Mélange trop pauvre, banc 2",
		"P0175": "Mélange trop riche, banc 2",
		"P0190": "Capteur de pression de rampe d'injection : circuit",
		"P0191": "Capteur de pression de rampe d'injection : plage ou performance",
		"P0192": "Capteur de pression de rampe d'injection : signal trop bas",
		"P0193": "Capteur de pression de rampe d'injection : signal trop haut",
		"P0200": "Injecteurs : circuit ouvert ou défaillant",
		"P0217": "Surchauffe du liquide de refroidissement",
		"P0218": "Surchauffe de l'huile de boîte de vitesses",
		"P0219": "Surrégime moteur",
		"P0230": "Pompe à carburant : circuit primaire",
		"P0231": "Pompe à carburant : circuit secondaire, niveau bas",
		"P0232": "Pompe à carburant : circuit secondaire, niveau haut",
		"P0233": "Pompe à carburant : circuit secondaire intermittent",
		"P0234": "Suralimentation excessive (turbo ou compresseur)",
	}
	d.update(simple)
	for cyl in range(1, 13):
		d[f"P{0x200 + cyl:04X}"] = f"Injecteur du cylindre {cyl} : circuit ouvert ou défaillant"
		d[f"P{0x300 + cyl:04X}"] = f"Ratés d'allumage détectés sur le cylindre {cyl}"
	for letter, base in (("A", 0x120), ("B", 0x220), ("C", 0x225)):
		d[f"P{base:04X}"] = f"Capteur de position papillon/pédale {letter} : circuit"
		d[f"P{base + 1:04X}"] = f"Capteur de position papillon/pédale {letter} : plage ou performance"
		d[f"P{base + 2:04X}"] = f"Capteur de position papillon/pédale {letter} : signal trop bas"
		d[f"P{base + 3:04X}"] = f"Capteur de position papillon/pédale {letter} : signal trop haut"
		d[f"P{base + 4:04X}"] = f"Capteur de position papillon/pédale {letter} : signal intermittent"
	# Sondes lambda
	sensors = {0x130: (1, 1), 0x136: (1, 2), 0x142: (1, 3), 0x150: (2, 1), 0x156: (2, 2), 0x162: (2, 3)}
	for base, (bank, sensor) in sensors.items():
		where = f"Sonde lambda banc {bank} capteur {sensor}" + (
			" (amont)" if sensor == 1 else " (aval)" if sensor == 2 else ""
		)
		d[f"P{base:04X}"] = f"{where} : circuit"
		d[f"P{base + 1:04X}"] = f"{where} : tension trop basse"
		d[f"P{base + 2:04X}"] = f"{where} : tension trop haute"
		d[f"P{base + 3:04X}"] = f"{where} : réponse lente"
		d[f"P{base + 4:04X}"] = f"{where} : aucune activité détectée"
		d[f"P{base + 5:04X}"] = f"{where} : circuit du réchauffeur"
	# Allumage
	d.update(
		{
			"P0300": "Ratés d'allumage aléatoires ou sur plusieurs cylindres",
			"P0315": "Variation du système de position vilebrequin non apprise",
			"P0316": "Ratés d'allumage détectés au démarrage (1000 premiers tours)",
			"P0320": "Signal de régime (allumage/distributeur) : circuit",
			"P0321": "Signal de régime (allumage/distributeur) : plage ou performance",
			"P0322": "Signal de régime (allumage/distributeur) : absence de signal",
			"P0323": "Signal de régime (allumage/distributeur) : signal intermittent",
			"P0324": "Erreur du système de contrôle du cliquetis",
			"P0335": "Capteur de position vilebrequin A : circuit",
			"P0336": "Capteur de position vilebrequin A : plage ou performance",
			"P0337": "Capteur de position vilebrequin A : signal trop bas",
			"P0338": "Capteur de position vilebrequin A : signal trop haut",
			"P0339": "Capteur de position vilebrequin A : signal intermittent",
			"P0340": "Capteur de position d'arbre à cames A (banc 1) : circuit",
			"P0341": "Capteur de position d'arbre à cames A (banc 1) : plage ou performance",
			"P0342": "Capteur de position d'arbre à cames A (banc 1) : signal trop bas",
			"P0343": "Capteur de position d'arbre à cames A (banc 1) : signal trop haut",
			"P0344": "Capteur de position d'arbre à cames A (banc 1) : signal intermittent",
			"P0345": "Capteur de position d'arbre à cames A (banc 2) : circuit",
			"P0350": "Bobine d'allumage : circuit primaire/secondaire",
		}
	)
	for sensor, base in ((1, 0x325), (2, 0x330)):
		d[f"P{base:04X}"] = f"Capteur de cliquetis {sensor} : circuit"
		d[f"P{base + 1:04X}"] = f"Capteur de cliquetis {sensor} : plage ou performance"
		d[f"P{base + 2:04X}"] = f"Capteur de cliquetis {sensor} : signal trop bas"
		d[f"P{base + 3:04X}"] = f"Capteur de cliquetis {sensor} : signal trop haut"
		d[f"P{base + 4:04X}"] = f"Capteur de cliquetis {sensor} : signal intermittent"
	for index, letter in enumerate("ABCDEFGHIJKL"):
		d[f"P{0x351 + index:04X}"] = f"Bobine d'allumage {letter} : circuit primaire/secondaire"
	# Émissions
	d.update(
		{
			"P0400": "Recirculation des gaz d'échappement (EGR) : débit",
			"P0401": "Recirculation des gaz d'échappement (EGR) : débit insuffisant",
			"P0402": "Recirculation des gaz d'échappement (EGR) : débit excessif",
			"P0403": "Recirculation des gaz d'échappement (EGR) : circuit de commande",
			"P0404": "Recirculation des gaz d'échappement (EGR) : plage ou performance du circuit de commande",
			"P0405": "Capteur EGR A : signal trop bas",
			"P0406": "Capteur EGR A : signal trop haut",
			"P0407": "Capteur EGR B : signal trop bas",
			"P0408": "Capteur EGR B : signal trop haut",
			"P0409": "Capteur EGR A : circuit",
			"P0410": "Système d'injection d'air secondaire",
			"P0411": "Injection d'air secondaire : débit incorrect",
			"P0420": "Efficacité du catalyseur sous le seuil, banc 1",
			"P0421": "Efficacité du catalyseur de démarrage sous le seuil, banc 1",
			"P0422": "Efficacité du catalyseur principal sous le seuil, banc 1",
			"P0423": "Efficacité du catalyseur chauffé sous le seuil, banc 1",
			"P0440": "Système antiévaporation (EVAP)",
			"P0441": "Système antiévaporation (EVAP) : débit de purge incorrect",
			"P0442": "Système antiévaporation (EVAP) : petite fuite détectée",
			"P0443": "Électrovanne de purge canister : circuit",
			"P0444": "Électrovanne de purge canister : circuit ouvert",
			"P0445": "Électrovanne de purge canister : court-circuit",
			"P0446": "Système antiévaporation (EVAP) : circuit de mise à l'air",
			"P0447": "Système antiévaporation (EVAP) : circuit de mise à l'air ouvert",
			"P0448": "Système antiévaporation (EVAP) : circuit de mise à l'air en court-circuit",
			"P0449": "Système antiévaporation (EVAP) : électrovanne de mise à l'air",
			"P0450": "Capteur de pression EVAP",
			"P0451": "Capteur de pression EVAP : plage ou performance",
			"P0452": "Capteur de pression EVAP : signal trop bas",
			"P0453": "Capteur de pression EVAP : signal trop haut",
			"P0454": "Capteur de pression EVAP : signal intermittent",
			"P0455": "Système antiévaporation (EVAP) : grosse fuite détectée",
			"P0456": "Système antiévaporation (EVAP) : très petite fuite détectée",
			"P0457": "Système antiévaporation (EVAP) : fuite (bouchon de réservoir mal fermé ou absent)",
			"P0460": "Capteur de niveau de carburant A : circuit",
			"P0461": "Capteur de niveau de carburant A : plage ou performance",
			"P0462": "Capteur de niveau de carburant A : signal trop bas",
			"P0463": "Capteur de niveau de carburant A : signal trop haut",
			"P0464": "Capteur de niveau de carburant A : signal intermittent",
			"P0480": "Motoventilateur 1 : circuit de commande",
			"P0481": "Motoventilateur 2 : circuit de commande",
			"P0482": "Motoventilateur 3 : circuit de commande",
			"P0483": "Motoventilateur : contrôle de cohérence",
			# Vitesse, ralenti
			"P0500": "Capteur de vitesse du véhicule A",
			"P0501": "Capteur de vitesse du véhicule A : plage ou performance",
			"P0502": "Capteur de vitesse du véhicule A : signal trop bas",
			"P0503": "Capteur de vitesse du véhicule A : signal intermittent, erratique ou trop haut",
			"P0504": "Corrélation des contacteurs de frein A/B",
			"P0505": "Régulation du ralenti",
			"P0506": "Régulation du ralenti : régime plus bas que prévu",
			"P0507": "Régulation du ralenti : régime plus haut que prévu",
			"P0508": "Régulation du ralenti : circuit, niveau bas",
			"P0509": "Régulation du ralenti : circuit, niveau haut",
			"P0560": "Tension du réseau de bord",
			"P0561": "Tension du réseau de bord instable",
			"P0562": "Tension du réseau de bord trop basse",
			"P0563": "Tension du réseau de bord trop haute",
			# Calculateur
			"P0600": "Liaison de communication série",
			"P0601": "Calculateur : erreur de somme de contrôle mémoire",
			"P0602": "Calculateur : erreur de programmation",
			"P0603": "Calculateur : erreur de mémoire permanente (KAM)",
			"P0604": "Calculateur : erreur de mémoire vive (RAM)",
			"P0605": "Calculateur : erreur de mémoire morte (ROM)",
			"P0606": "Calculateur moteur : processeur",
			"P0620": "Alternateur : circuit de commande",
			"P0621": "Alternateur : circuit du témoin / borne L",
			"P0622": "Alternateur : circuit d'excitation / borne F",
			"P0627": "Pompe à carburant A : circuit de commande ouvert",
			"P0628": "Pompe à carburant A : circuit de commande, niveau bas",
			"P0645": "Relais d'embrayage de climatisation : circuit de commande",
			"P0646": "Relais d'embrayage de climatisation : circuit de commande, niveau bas",
			"P0647": "Relais d'embrayage de climatisation : circuit de commande, niveau haut",
			"P0648": "Témoin d'antidémarrage : circuit de commande",
			"P0649": "Témoin de régulateur de vitesse : circuit de commande",
			"P0650": "Voyant de défaut moteur (MIL) : circuit de commande",
			"P0685": "Relais d'alimentation du calculateur : circuit de commande ouvert",
			"P0686": "Relais d'alimentation du calculateur : circuit de commande, niveau bas",
			"P0700": "Système de commande de boîte de vitesses (demande d'allumage du voyant)",
			"P2096": "Correction de richesse après catalyseur : trop pauvre, banc 1",
			"P2097": "Correction de richesse après catalyseur : trop riche, banc 1",
			"P2098": "Correction de richesse après catalyseur : trop pauvre, banc 2",
			"P2099": "Correction de richesse après catalyseur : trop riche, banc 2",
			"P2177": "Mélange trop pauvre hors ralenti, banc 1",
			"P2178": "Mélange trop riche hors ralenti, banc 1",
			"P2187": "Mélange trop pauvre au ralenti, banc 1",
			"P2188": "Mélange trop riche au ralenti, banc 1",
			"P2195": "Sonde lambda banc 1 capteur 1 : signal bloqué pauvre",
			"P2196": "Sonde lambda banc 1 capteur 1 : signal bloqué riche",
			"P2270": "Sonde lambda banc 1 capteur 2 : signal bloqué pauvre",
			"P2271": "Sonde lambda banc 1 capteur 2 : signal bloqué riche",
			"P2A00": "Sonde lambda banc 1 capteur 1 : plage ou performance du circuit",
			# Réseau
			"U0001": "Bus CAN haute vitesse",
			"U0073": "Bus de communication du calculateur hors service (bus off)",
			"U0100": "Perte de communication avec le calculateur moteur (ECM/PCM A)",
			"U0101": "Perte de communication avec le calculateur de boîte de vitesses (TCM)",
			"U0121": "Perte de communication avec le calculateur d'ABS",
			"U0140": "Perte de communication avec le boîtier de servitude (BCM)",
			"U0155": "Perte de communication avec le combiné d'instruments",
		}
	)
	return dict(sorted(d.items()))


def main() -> None:
	data = build()
	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(json.dumps(data, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")
	print(f"{len(data)} descriptions écrites dans {OUT}")


if __name__ == "__main__":
	main()
