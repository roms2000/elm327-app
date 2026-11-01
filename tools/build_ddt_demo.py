"""Génère la base DDT4All de démonstration (FICTIVE) : obdtool/core/data/ddt_demo/.

Deux calculateurs inventés (UCH et tableau de bord) au format DDT4All, utilisés par le mode démo et les tests.
Les requêtes, positions et valeurs ne correspondent à aucun véhicule réel.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "obdtool" / "core" / "data" / "ddt_demo"


def bit(name: str, offset: int, labels: dict[int, str], firstbyte: int = 3, bits: int = 1) -> tuple[dict, dict]:
	item = {"firstbyte": firstbyte, "bitoffset": offset}
	data = {"bitscount": bits, "lists": {str(k): v for k, v in labels.items()}, "description": f"{name} (démo)"}
	return item, data


def ident_request() -> dict:
	return {"name": "Identification", "sentbytes": "2180", "replybytes": "6180", "receivebyte_dataitems": {}}


def uch() -> dict:
	items, data = {}, {}
	for name, offset, labels, bits in (
		("Clignotants impulsionnels (3 clignotements)", 0, {0: "Inactif", 1: "Actif"}, 1),
		("Reverrouillage automatique", 1, {0: "Inactif", 1: "Actif"}, 1),
		("Verrouillage automatique en roulant", 2, {0: "Inactif", 1: "Actif"}, 1),
		("Déverrouillage porte conducteur seule", 3, {0: "Inactif", 1: "Actif"}, 1),
		("Éclairage d'accompagnement", 4, {0: "Inactif", 1: "30 s", 2: "60 s", 3: "90 s"}, 2),
	):
		items[name], data[name] = bit(name, offset, labels, bits=bits)
	items["Fermeture des vitres à la condamnation"], data["Fermeture des vitres à la condamnation"] = bit(
		"Fermeture des vitres à la condamnation", 0, {0: "Inactif", 1: "Actif"}, firstbyte=4
	)
	return {
		"ecuname": "UCH_DEMO (fictif)",
		"obd": {"protocol": "CAN", "send_id": "745", "recv_id": "765", "funcaddr": "26", "funcname": "UCH"},
		"endian": "Big",
		"devices": [],
		"requests": [
			ident_request(),
			{
				"name": "LC_Configuration habitacle",
				"sentbytes": "2101",
				"replybytes": "6101",
				"receivebyte_dataitems": items,
			},
			{
				"name": "Ecriture configuration habitacle",
				"sentbytes": "3B01",
				"replybytes": "7B01",
				"sendbyte_dataitems": {"Clignotants impulsionnels (3 clignotements)": {"firstbyte": 3}},
			},
		],
		"data": data,
	}


def tdb() -> dict:
	items, data = {}, {}
	for name, offset, labels, bits in (
		("Volume du bip de réserve carburant", 0, {0: "Faible", 1: "Moyen", 2: "Fort"}, 2),
		("Bip de ceinture de sécurité", 2, {0: "Inactif", 1: "Actif"}, 1),
		("Unité de consommation", 3, {0: "L/100 km", 1: "km/L"}, 1),
	):
		items[name], data[name] = bit(name, offset, labels, bits=bits)
	items["Seuil d'alerte réserve carburant"] = {"firstbyte": 4}
	data["Seuil d'alerte réserve carburant"] = {
		"bitscount": 8,
		"scaled": True,
		"step": 1,
		"unit": "L",
		"description": "Seuil d'alerte réserve carburant (démo)",
	}
	return {
		"ecuname": "TDB_DEMO (fictif)",
		"obd": {"protocol": "CAN", "send_id": "743", "recv_id": "763", "funcaddr": "51", "funcname": "Tableau de bord"},
		"endian": "Big",
		"devices": [],
		"requests": [
			ident_request(),
			{
				"name": "LC_Configuration tableau de bord",
				"sentbytes": "2110",
				"replybytes": "6110",
				"receivebyte_dataitems": items,
			},
		],
		"data": data,
	}


def main() -> None:
	OUT.mkdir(parents=True, exist_ok=True)
	files = {"UCH_DEMO.json": uch(), "TDB_DEMO.json": tdb()}
	index = {
		"UCH_DEMO.json": {
			"ecuname": "UCH_DEMO (fictif)",
			"group": "UCH",
			"address": "26",
			"protocol": "CAN",
			"projects": ["DEMO"],
			"autoidents": [
				{"diagnostic_version": "12", "supplier_code": "DEM", "soft_version": "0123", "version": "0001"}
			],
		},
		"TDB_DEMO.json": {
			"ecuname": "TDB_DEMO (fictif)",
			"group": "Tableau de bord",
			"address": "51",
			"protocol": "CAN",
			"projects": ["DEMO"],
			"autoidents": [
				{"diagnostic_version": "8", "supplier_code": "DMO", "soft_version": "0456", "version": "0002"}
			],
		},
	}
	for name, content in files.items():
		(OUT / name).write_text(json.dumps(content, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")
	(OUT / "db.json").write_text(json.dumps(index, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")
	print(f"Base de démonstration écrite dans {OUT}")


if __name__ == "__main__":
	main()
