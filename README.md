# OBDTool

Application de bureau Linux (PySide6) de diagnostic OBD-II pour adaptateurs ELM327 (USB, Bluetooth rfcomm, Wi-Fi).

> Projet en cours de développement : voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) pour le plan.
> Le README complet (installation, utilisation, limites) sera rédigé à la fin du développement.

## Démarrage rapide (développement)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/obdtool
```
