# Architecture d'OBDTool

## Principes

- **Cœur sans Qt** (`obdtool/core/`) : communication, décodage, calculs, enregistrement. Testable avec pytest seul.
- **Interface PySide6** (`obdtool/ui/`) : ne parle jamais directement à l'adaptateur.
- **Un seul thread propriétaire de l'adaptateur** : l'ELM327 est half-duplex. Interrogation cyclique et tâches
  ponctuelles (lecture DTC, console…) passent par une file sérialisée dans un thread de travail (`threading`,
  équivalent d'un QThread mais utilisable sans Qt). L'interface reçoit les événements via `ui/bridge.py`.
- **python-OBD** : sa classe `ELM327` gère l'initialisation (débit, protocole) et le décodage ISO-TP ; ses décodeurs
  Mode 01/02/03/06/09 sont réutilisés. L'envoi brut (pour voir `NO DATA`, `CAN ERROR`…, la console et `ATMA`) passe
  par une sous-classe isolée dans `core/elm_link.py`, seul endroit qui accède aux attributs internes de python-OBD.
- **Source de données abstraite** : tableau de bord, graphiques et alertes consomment un flux d'échantillons, qui
  vient soit de l'adaptateur, soit de la relecture d'une session.

## Modules

| Cœur (`obdtool/core/`, sans Qt) | Rôle |
|---|---|
| `transport.py` | Ports série, droits dialout/uucp, URL Wi-Fi |
| `elm_link.py` | Liaison ELM327 (python-OBD + envoi brut à échéance unique), protocole ATSP, écoute ATMA |
| `adapter_info.py`, `protocols.py`, `errors.py` | Identification, clones, tension ; protocoles ; erreurs `NO DATA`, `CAN ERROR`… |
| `service.py` | Thread unique propriétaire de l'adaptateur : tâches, interrogation, reconnexion |
| `pids.py`, `poller.py`, `units.py`, `samples.py`, `timeseries.py` | Catalogue Mode 01, interrogation cyclique, unités, statistiques, séries |
| `dtc.py`, `dtc_history.py`, `report.py`, `data/` | Codes défaut, freeze frame, historique SQLite, rapports, bases de descriptions |
| `readiness.py`, `onboard_tests.py`, `vehicle_info.py` | Moniteurs, modes 05/06/08, mode 09 |
| `profile.py`, `calc.py`, `perf.py`, `analysis.py` | Profil véhicule, valeurs calculées, chronos, analyses |
| `sessions.py`, `dashboard.py`, `alerts.py`, `console_guard.py` | Sessions, mises en page, alertes, garde-fou console |
| `emulator.py`, `demo_sim.py` | Mode démo (ELM327-emulator + simulateur de conduite) |

Côté interface (`obdtool/ui/`) : `bridge.py` relaie les événements du thread de travail vers le thread Qt par une
file (jamais d'émission de signal depuis un thread Python, cf. défaut PySide6 sous Python 3.11) ; `acquisition.py`
centralise les besoins en valeurs des écrans, les PID supportés, les valeurs calculées et la diffusion des lots ;
`session_control.py` et `alert_control.py` pilotent enregistrement, relecture et alertes ; `pages/` contient un
module par écran.

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
| 10 | Extras (analyses, bilan complet), README, `.desktop`, finitions |

Toutes les étapes sont réalisées.

## Conventions

- Indentation par tabulations partout (`ruff format` avec `indent-style = "tab"`), sauf les fichiers YAML
  (`.github/workflows`), où la syntaxe YAML interdit les tabulations.
- Annotations de type, docstrings courtes, `logging` uniquement.
