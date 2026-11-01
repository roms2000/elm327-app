# OBDTool

Application de bureau Linux (Python, PySide6) de **diagnostic OBD-II** pour adaptateurs **ELM327**
(USB, Bluetooth rfcomm, Wi-Fi). Interface en français, thèmes clair et sombre.

Profil par défaut : **Renault Clio III 1.6 16v (K4M), essence, 2005**, mais l'application reste générique :
tous les protocoles OBD-II sont gérés et le profil véhicule est modifiable.

> ⚠ **Ne manipulez jamais l'application en conduisant.** Installez l'ordinateur de façon stable ou faites-vous
> assister par un passager. Les chronos de performance ne doivent être utilisés que sur circuit ou terrain privé.

---

## Sommaire

1. [Installation](#installation)
2. [Droits sur le port série](#droits-sur-le-port-série)
3. [Connexion de l'adaptateur](#connexion-de-ladaptateur) (USB, Bluetooth, Wi-Fi, démo)
4. [Les écrans](#les-écrans)
5. [Fonctions ajoutées](#fonctions-ajoutées-au-cahier-des-charges)
6. [Limites connues](#limites-connues)
7. [Hypothèses sur le matériel et le véhicule](#hypothèses-sur-le-matériel-et-le-véhicule)
8. [Fichiers, réglages et journaux](#fichiers-réglages-et-journaux)
9. [Ligne de commande](#ligne-de-commande)
10. [Développement et tests](#développement-et-tests)

---

## Installation

Prérequis : Python 3.11 ou plus récent, le module `venv`, et la bibliothèque `libxcb-cursor0` utilisée par Qt 6.

```bash
# Debian / Ubuntu / Mint / Zorin
sudo apt install python3-venv libxcb-cursor0
# Fedora
sudo dnf install python3 xcb-util-cursor
# Arch / Manjaro
sudo pacman -S python xcb-util-cursor
```

Installation dans un environnement virtuel :

```bash
git clone https://github.com/roms2000/elm327-app.git
cd elm327-app
python3 -m venv .venv
.venv/bin/pip install "setuptools<81" wheel
.venv/bin/pip install --no-build-isolation ELM327-emulator   # mode démo (facultatif)
.venv/bin/pip install -e ".[demo]"
```

> ELM327-emulator n'est distribué qu'en sources et son installation a besoin de `pkg_resources`, retiré de
> setuptools 81 : d'où les deux premières lignes. Sans mode démo, `pip install -e .` suffit.

Lancement :

```bash
./obdtool.sh            # ou .venv/bin/obdtool
./obdtool.sh --demo     # connexion directe à l'émulateur
./obdtool.sh --debug    # journal détaillé (trames échangées)
```

Ajout au menu des applications (sans droits administrateur) :

```bash
packaging/install-desktop.sh
```

---

## Droits sur le port série

Les adaptateurs USB apparaissent en `/dev/ttyUSB0` (puces FTDI, CH340, CP210x) ou `/dev/ttyACM0`. Ces fichiers
appartiennent au groupe `dialout` (Debian, Ubuntu, Fedora…) ou `uucp` (Arch, Manjaro). Au démarrage, et sur
l'écran Connexion, l'application vérifie les droits et indique la commande à lancer si besoin :

```bash
sudo usermod -aG dialout $USER     # ou uucp selon la distribution
```

Fermez puis rouvrez votre session pour que le groupe soit pris en compte (`id` doit afficher `dialout`).

---

## Connexion de l'adaptateur

Écran **Connexion** : choisissez le type, le port, le débit (automatique par défaut), le protocole
(automatique ou forcé de `ATSP1` à `ATSPC`), le mode rapide, le délai de réponse, le nombre de tentatives et
la reconnexion automatique. La dernière configuration est mémorisée.

- **Se connecter** : ouvre la liaison et lit les informations de l'adaptateur.
- **Tester la connexion** : même chose, affiche un résumé puis déconnecte.
- **Réessayer le véhicule** : après avoir mis le contact (nouvelle détection du protocole). Avec la reconnexion
  automatique, une tentative est faite toutes les 10 s.

Une fois connecté, l'écran affiche : version (`ATI`, `AT@1`), puce STN éventuelle, protocole (`ATDPN`), tension
(`ATRV`) commentée (batterie faible, moteur arrêté ou tournant, mesure incohérente si l'adaptateur n'est pas sur
la prise), état du véhicule, entrée IgnMon (`ATIGN`), commandes reconnues, et **avertissements de clone** (versions
inexistantes comme v1.5, v2.1 douteuse, commandes manquantes pour la version annoncée).

### USB

Branchez l'adaptateur sur l'ordinateur et sur la prise OBD (en général sous le tableau de bord, côté conducteur ;
sur la Clio III, derrière un cache), puis choisissez le port dans la liste (**Actualiser** si besoin).

### Bluetooth (rfcomm)

1. Appairez l'adaptateur (code souvent `1234` ou `0000`) avec les paramètres Bluetooth du bureau ou `bluetoothctl`.
2. Associez-le à un port série :

   ```bash
   sudo rfcomm bind 0 AA:BB:CC:DD:EE:FF 1
   ```

3. Choisissez `/dev/rfcomm0` dans l'écran Connexion (même procédure que l'USB).

Les adaptateurs « BLE » (Bluetooth Low Energy, souvent pour iPhone) n'offrent pas de port série et ne sont pas
pris en charge.

### Wi-Fi

Connectez l'ordinateur au réseau Wi-Fi de l'adaptateur (souvent `WiFi_OBDII`), puis indiquez l'adresse et le port :
`192.168.0.10:35000` par défaut.

### Mode démo

Type **Démo (émulateur)** : ELM327-emulator tourne dans l'application, avec un **simulateur de conduite** cohérent
(ralenti, accélération avec passages de rapports, croisière, reprise 80-120, freinage, montée en température,
sonde amont qui oscille) et un profil proche du K4M (capteur MAP, pas de débitmètre). Deux scénarios :

- *Véhicule en marche, sans défaut* ;
- *Véhicule avec défauts* : P0171 et P0300 mémorisés, P0420 en attente, voyant allumé, freeze frame réaliste.
  L'effacement (mode 04) fonctionne et remet les moniteurs à « non terminé ».

---

## Les écrans

| Écran | Rôle |
|---|---|
| **Connexion** | Choix et test de l'adaptateur, informations, détection de clones |
| **Tableau de bord** | Jauges configurables (cadran, barre, valeur), zones de couleur, mises en page enregistrables, plein écran |
| **Valeurs** | PID supportés par catégorie, sélection, tableau valeur / min / max / moyenne, fréquence réelle |
| **Graphiques** | Courbes empilées ou superposées, curseur, zoom, export PNG et CSV |
| **Défauts** | Codes mémorisés / en attente / permanents, voyant, freeze frame, effacement, historique, rapport, bilan complet |
| **Moniteurs** | Readiness depuis l'effacement et sur le cycle en cours, synthèse « prêt pour le contrôle » |
| **Tests embarqués** | Mode 06, Mode 05, Mode 08 (désactivé par défaut) |
| **Infos véhicule** | VIN, CAL ID, CVN, nom du calculateur, compteurs de performance, normes, distances et temps |
| **Performances** | Valeurs calculées, chronos 0-50 / 0-100 / 80-120, historique |
| **Analyses** | Sondes lambda et catalyseur, corrections de richesse, test batterie / charge |
| **Sessions** | Enregistrement, relecture, renommage, suppression, export |
| **Console** | Commandes AT et requêtes hexadécimales, garde-fou, écoute du bus (ATMA) |
| **Paramètres** | Thème, unités, son des alertes, journaux, alertes |

### Tableau de bord

Trois mises en page fournies : *Conduite*, *Diagnostic moteur*, *Sondes lambda*. Double-clic sur une jauge pour
choisir la valeur, le type, les bornes et les zones colorées ; clic droit pour la déplacer ou la supprimer.
**Plein écran** (ou Ctrl+Maj+F) ; Échap pour revenir.

### Valeurs en direct

Seuls les PID supportés par le calculateur sont proposés (case « Afficher les PID non supportés » pour voir les
autres). **Moins il y a de valeurs cochées, plus elles sont rafraîchies souvent** ; les valeurs lentes
(températures, niveaux, compteurs) ne sont interrogées qu'un cycle sur cinq. Un PID qui ne répond plus est mis
en pause 15 s. L'intervalle minimal d'un cycle est réglable. La fréquence obtenue s'affiche en haut à droite et
dans la barre d'état.

Certaines valeurs sont extraites des mêmes octets qu'une autre (correction de richesse associée à une sonde,
lambda des sondes large bande) : une seule requête pour plusieurs valeurs.

### Graphiques

Jusqu'à 8 courbes, palette lisible par les daltoniens. *Empilées* : un graphique par courbe, axes de temps liés.
*Superposées* : un seul graphique, un axe Y coloré par courbe. Fenêtre de 10 s à 30 min. **Pause** pour zoomer
(molette) et faire défiler dans tout l'historique de la session ; clic pour placer le curseur, qui affiche toutes
les valeurs à cet instant. Export **PNG** de l'image et **CSV** de la plage affichée.

### Défauts

**Lire les codes** : modes 03, 07 et 0A, état du voyant (PID 01), freeze frame (mode 02), distance depuis
l'effacement et VIN. Chaque code est décrit :

1. vos descriptions personnelles (`~/.config/obdtool/dtc_custom.json`) ;
2. descriptions constructeur connues (avec la mention « non vérifié » si la source est incertaine) ;
3. base française de 305 codes SAE courants ;
4. base SAE anglaise de python-OBD (2 066 codes, mention « description SAE en anglais ») ;
5. à défaut, la structure du code (système, générique ou constructeur, sous-système).

**Effacer les codes** : double confirmation, rappel des conditions (contact mis, moteur arrêté, vérifié par
l'application) et des conséquences (moniteurs remis à zéro, freeze frames perdus). Sur les véhicules sans clé dont
le calculateur n'est joignable que moteur tournant (cas de la Clio III à carte), la case « Autoriser moteur au
ralenti » permet l'effacement au ralenti (moins de 1200 tr/min), véhicule à l'arrêt. Si le calculateur ne
confirme pas dans le délai par défaut de l'ELM327, une seconde tentative est faite avec un délai allongé.

**Historique local** : chaque lecture et chaque effacement sont enregistrés (date, codes, voyant, distance depuis
l'effacement, kilométrage saisi à la main). Un code revenu après un effacement est signalé.

**Exporter le rapport** (HTML ou texte) et **Bilan complet** (codes, freeze frame, moniteurs, tests mode 06,
identification).

Format du fichier de descriptions personnelles :

```json
{
	"renault": { "P1525": { "description": "Ma description", "verified": false } },
	"P0171": "Description prioritaire pour toutes les marques"
}
```

### Moniteurs de préparation

PID 01 (depuis l'effacement) et PID 41 (cycle en cours) : *non supporté / terminé / non terminé*, moniteurs
continus et non continus, essence et diesel. Synthèse **prêt pour un contrôle anti-pollution : oui / non** (voyant
éteint et tous les moniteurs non continus terminés), avec rappel de la tolérance américaine d'un moniteur.

### Tests embarqués

- **Mode 06** (CAN) : valeur, limites et statut OK/KO, noms français des moniteurs et des tests standard ; les tests
  propres au constructeur sont affichés avec leur numéro.
- **Mode 05** : surveillance des sondes lambda pour les protocoles non CAN ; en CAN, l'écran explique que ce mode
  n'existe pas (résultats en mode 06).
- **Mode 08** : désactivé par défaut, derrière un avertissement explicite ; seul le test standard d'étanchéité EVAP
  peut être lancé.

### Performances et valeurs calculées

À partir du **profil véhicule** (Fichier → Profil véhicule…) :

- **débit d'air** : débitmètre si présent, sinon **speed-density** (MAP, température d'admission, régime, cylindrée,
  rendement volumétrique) — le cas du K4M ;
- **consommation** instantanée (L/h, L/100 km), moyenne, carburant consommé et distance de la session ; corrections
  de richesse et coupure d'injection en décélération prises en compte ; PID 5E utilisé s'il existe ;
- **rapport engagé** estimé (régime / vitesse, rapports de boîte, pont, pneus) ;
- **puissance** à la roue (indicative, ±15 % au mieux) ;
- **chronos** 0-50 et 0-100 km/h (départ arrêté), 80-120 km/h (reprise), avec interpolation des instants de
  franchissement et historique. Le **mode chrono** n'interroge que la vitesse et le régime pour plus de précision.

Les valeurs calculées sont aussi disponibles dans Valeurs, Graphiques et Tableau de bord ; leurs PID d'entrée sont
interrogés automatiquement.

### Sessions

**Démarrer l'enregistrement** (ou Ctrl+R) enregistre toutes les valeurs interrogées dans un CSV avec métadonnées
(véhicule, date, protocole, adaptateur). **Relire** rejoue la session dans les graphiques, le tableau de bord et les
valeurs, de ×0,25 à ×16, avec une barre de déplacement. Les valeurs calculées sont recalculées avec le profil
courant. Export « une colonne par valeur » pour les tableurs.

### Alertes

Paramètres → Alertes : seuils par valeur (supérieure / inférieure), durée minimale de dépassement, marge de retour
à la normale, son optionnel. Par défaut : température moteur > 105 °C, tension < 11,8 V ou > 15,2 V, régime
> 6300 tr/min. Un bandeau rouge s'affiche en haut de la fenêtre ; les valeurs surveillées sont interrogées en
permanence.

### Console experte

Commandes AT et requêtes hexadécimales, historique (↑/↓), raccourcis (`ATZ`, `ATI`, `ATRV`, `ATDP`, `ATH1`,
`ATSH7E0`…). Le **garde-fou** classe chaque commande :

- **libre** : lectures OBD (modes 01, 02, 03, 05, 06, 07, 09, 0A) et UDS/KWP (0x19, 0x1A, 0x21, 0x22…) ;
- **confirmation** : effacement des codes (04, 14) ;
- **mode expert** : sessions de diagnostic étendues, accès sécurisé, écriture, routines, programmation,
  réinitialisation, Mode 08, paramètres programmables de l'adaptateur (`ATPP…`), changement de débit.

Après `ATZ`, `ATWS` ou `ATD`, la configuration attendue par les autres écrans est rétablie automatiquement (bouton
« Rétablir la configuration » aussi). L'acquisition est suspendue pendant l'utilisation de la console (désactivable).

**Écoute du bus** (`ATMA`, expérimental) avec filtre d'identifiant (`ATCRA`). Sur beaucoup de véhicules, la prise
OBD est isolée du bus principal par une passerelle : peu ou pas de trames sont visibles.

---

## Fonctions ajoutées au cahier des charges

- **Simulateur de conduite** pour le mode démo et scénario « véhicule avec défauts » (codes, freeze frame, moniteurs,
  effacement fonctionnel).
- **Bilan complet** exportable en HTML imprimable.
- **Écran Analyses** :
  - sondes lambda : fréquence de bascule de la sonde amont, activité relative de la sonde aval, estimation de
    l'efficacité du catalyseur ;
  - corrections de richesse par plage de régime, avec repérage des cas typiques (prise d'air, pression d'essence…) ;
  - test guidé batterie / démarrage / charge à partir de la tension mesurée par l'adaptateur.
- **Lecture des PID d'une requête unique** pour plusieurs valeurs (sondes), valeurs lentes interrogées moins souvent,
  pause automatique des PID muets, détection du contact coupé pendant l'acquisition.
- **Détection de perte de liaison** même quand la socket reste ouverte (Wi-Fi hors de portée), reconnexion
  automatique et nouvelle détection du véhicule quand le contact est remis.
- **Base de codes personnalisable** (`dtc_custom.json`) et historique avec détection des codes récurrents.
- **Outil en ligne de commande** `obdtool-cli` (ports, informations, commandes brutes).
- **Journaux par session** avec visionneuse, ouverture du dossier, suppression et purge automatique.

---

## Limites connues

- **Python 3.11 et PySide6 6.12.0** : cette version de PySide6 dérègle un compteur interne de Python 3.11 à chaque
  émission de signal et finit par faire planter l'application. Sous Python 3.11, l'installation retient donc
  PySide6 6.11 ou antérieur ; l'application compense aussi le défaut si la version fautive est présente.

- **OBD-II générique uniquement.** Un ELM327 ne dialogue qu'avec les fonctions antipollution normalisées du
  calculateur moteur. Les calculateurs Renault spécifiques (ABS, airbag, UCH, direction assistée, climatisation,
  tableau de bord…) et les paramètres constructeur nécessitent un outil comme **DDT4All** (avec un adaptateur
  compatible) ou l'outil constructeur.
- **Clones ELM327 peu fiables** : la grande majorité des adaptateurs « v1.5 » ou « v2.1 » bon marché sont des clones
  au micrologiciel incomplet (commandes manquantes, protocoles K-Line défaillants, mesure de tension fantaisiste,
  saturation en écoute du bus). L'application les détecte et les signale.
- **Fréquence de rafraîchissement** limitée par l'adaptateur et le calculateur : de 5 à 30 requêtes par seconde
  sur un véhicule réel (bien plus sur l'émulateur).
- **Valeurs calculées** : estimations dépendant du profil (rendement volumétrique surtout). Pour fiabiliser la
  consommation, comparez à un plein et ajustez le rendement volumétrique.
- **Kilométrage** : non disponible en OBD générique sur la plupart des véhicules avant 2019 ; saisie manuelle dans
  l'historique des défauts.
- **Mode 06 hors CAN** : le format des protocoles K-Line / J1850 (identifiants constructeur) n'est pas décodé.
- **Descriptions constructeur P1xxx** : aucune n'est fournie par défaut faute de source fiable ; Renault utilise
  surtout des codes génériques P0 en EOBD. Ajoutez les vôtres dans `dtc_custom.json`.
- Adaptateurs Bluetooth Low Energy non pris en charge (pas de port série).

---

## Hypothèses sur le matériel et le véhicule

À confirmer sur votre véhicule (l'application gère les autres cas, avec un affichage « non supporté ») :

- Clio III 2005 en **ISO 15765-4 CAN 11 bit 500 kbaud** ; certaines Renault de cette époque sont en KWP2000, la
  détection automatique s'en charge.
- Moteur K4M avec **capteur MAP (PID 0B) et sans débitmètre (PID 10)** : débit d'air en speed-density.
- Peu probables sur ce calculateur : Mode 0A (codes permanents), PID 41, la plupart du Mode 06, Mode 05 (CAN),
  Mode 08, VIN et compteurs du Mode 09.
- Profil par défaut : 1598 cm³, 1150 kg avec conducteur, boîte JH3 (3,727 / 2,048 / 1,393 / 1,029 / 0,820, pont
  4,214), pneus 185/60 R15, SCx 0,33 × 2,10 m², rendement volumétrique 0,85. **Valeurs indicatives à vérifier.**
- Adaptateur testé pendant le développement : ELM327 v1.4 sur puce FTDI FT232R (sans véhicule). Hors véhicule,
  il renvoie `CAN ERROR` / `UNABLE TO CONNECT` et une tension incohérente, ce que l'application signale.

---

## Fichiers, réglages et journaux

| Contenu | Emplacement |
|---|---|
| Réglages (TOML) | `~/.config/obdtool/config.toml` |
| Profils véhicule | `~/.config/obdtool/profiles/*.toml` |
| Descriptions de codes personnelles | `~/.config/obdtool/dtc_custom.json` |
| Journaux | `~/.config/obdtool/logs/` |
| Sessions enregistrées | `~/.local/share/obdtool/sessions/` |
| Historique des défauts | `~/.local/share/obdtool/dtc_history.sqlite` |
| Historique des performances | `~/.local/share/obdtool/performances.json` |

**Journaux** : un fichier par lancement (`obdtool_AAAAMMJJ_HHMMSS_<pid>.log`), rotation à 5 Mo dans une même
session, purge au démarrage au-delà de 10 sessions ou 50 Mo (réglable). Paramètres → Journaux ou Aide → Journaux :
visionneuse (filtre par niveau, recherche, suivi en direct), ouverture du dossier, suppression d'une sélection ou de
tous les anciens journaux. Le niveau *Débogage* enregistre les trames échangées avec l'adaptateur.

---

## Ligne de commande

```bash
.venv/bin/obdtool-cli ports                       # ports série et droits
.venv/bin/obdtool-cli info --port /dev/ttyUSB0    # informations adaptateur / véhicule
.venv/bin/obdtool-cli raw --demo ATI 0100 03      # commandes brutes
.venv/bin/obdtool-cli info --wifi 192.168.0.10:35000
```

---

## Développement et tests

```bash
.venv/bin/pip install "setuptools<81" wheel
.venv/bin/pip install --no-build-isolation ELM327-emulator
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff format . && .venv/bin/ruff check .
.venv/bin/pytest                      # tests unitaires, intégration (émulateur) et interface (Qt offscreen)
.venv/bin/pytest -m hardware          # avec un adaptateur réel branché (OBDTOOL_PORT=/dev/ttyUSB0)
```

- Code indenté par tabulations (`ruff format`, `indent-style = "tab"`), sauf les fichiers YAML où la syntaxe
  l'interdit.
- Architecture : voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Le cœur (`obdtool/core/`) n'importe pas Qt.
- Intégration continue GitHub Actions : Python 3.11, 3.12 et 3.13.

## Licence

GPL-3.0-or-later (voir `LICENSE`). Utilise python-OBD (GPL), PySide6 (LGPL), pyqtgraph (MIT), numpy (BSD).
ELM327-emulator (CC-BY-NC-SA 4.0, usage non commercial) est une dépendance **facultative** pour le mode démo.
