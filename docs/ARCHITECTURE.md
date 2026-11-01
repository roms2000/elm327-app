# Architecture d'OBDTool

## Principes

- **Cœur sans Qt** (`obdtool/core/`) : communication, décodage, calculs, enregistrement. Testable avec pytest seul.
- **Interface PySide6** (`obdtool/ui/`) : ne parle jamais directement à l'adaptateur.
- **Un seul thread propriétaire de l'adaptateur** : l'ELM327 est half-duplex. Interrogation cyclique et tâches
  ponctuelles (lecture DTC, console…) passent par une file sérialisée dans un `QThread`. L'interface reçoit des signaux.
- **python-OBD** : sa classe `ELM327` gère l'initialisation (débit, protocole) et le décodage ISO-TP ; ses décodeurs
  Mode 01/02/03/06/09 sont réutilisés. L'envoi brut (pour voir `NO DATA`, `CAN ERROR`…, la console et `ATMA`) passe
  par une sous-classe isolée dans `core/elm_link.py`, seul endroit qui accède aux attributs internes de python-OBD.
- **Source de données abstraite** : tableau de bord, graphiques et alertes consomment un flux d'échantillons, qui
  vient soit de l'adaptateur, soit de la relecture d'une session.

## Fichiers utilisateur (XDG)

| Contenu | Emplacement |
|---|---|
| Réglages (TOML) | `~/.config/obdtool/config.toml` |
| Journaux | `~/.config/obdtool/logs/` |
| Sessions enregistrées, historique des défauts | `~/.local/share/obdtool/` |

### Journaux

- Un fichier par lancement : `obdtool_AAAAMMJJ_HHMMSS_<pid>.log`.
- Rotation à l'intérieur d'une session (5 Mo × 3 fichiers au plus).
- Purge au démarrage : on garde les N dernières sessions (10 par défaut) et au plus X Mo au total (50 par défaut).
- Paramètres → Journaux (ou Aide → Journaux) : visionneuse avec filtre par niveau et recherche, ouverture du dossier,
  suppression d'une sélection ou de tous les anciens journaux. La session en cours n'est jamais supprimée.
- Le niveau « Débogage » ajoute les trames brutes échangées avec l'adaptateur.

## Étapes

| # | Étape |
|---|---|
| 0 | Socle : pyproject, ruff (tabulations), journaux, réglages, fenêtre, thème, CI |
| 1 | Connexion + mode démo |
| 2 | Valeurs en direct |
| 3 | Codes défaut + freeze frame |
| 4 | Graphiques temps réel |
| 5 | Enregistrement, relecture, sessions |
| 6 | Tableau de bord + alertes |
| 7 | Moniteurs, tests embarqués (05/06/08), infos véhicule (09) |
| 8 | Profil véhicule, valeurs calculées, performances |
| 9 | Console experte |
| 10 | Extras, README, `.desktop`, finitions |

## Conventions

- Indentation par tabulations partout (`ruff format` avec `indent-style = "tab"`), sauf les fichiers YAML
  (`.github/workflows`), où la syntaxe YAML interdit les tabulations.
- Annotations de type, docstrings courtes, `logging` uniquement.
