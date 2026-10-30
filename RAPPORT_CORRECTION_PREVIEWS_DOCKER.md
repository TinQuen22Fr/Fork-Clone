# Rapport de correction — Previews Docker (temps réel & fiabilité)


> **STATUT : CORRECTIONS IMPLEMENTEES ET VERIFIEES (syntaxe OK)**
> - Fichiers modifies : `backend/preview_runtime.py`, `deploy/docker/forge-preview`
> - Etapes realisees : 1 (flush), 2 (ressources), 3 (serialisation), 4 (run streaming), 5 (detection ASGI), G (install idempotente)
> - Reste a faire : deployer en prod (`upgrade.sh` + wrapper) et valider sur Fork-Clone.
> - Contrainte reelle : serveur 4 Go RAM + 9 GiB swap, CPU barebone (pas de vCPU scalable).

> Contexte : serveur perso en datacenter, **4 Go de RAM physique**, **9 GiB de swap** (swapfile), **CPU barebone** (pas de vCPU scalable).
> Il est envisagé, plus tard, un VPS Scaleway START-2-S (option future) (2 vCPU / 2 Go / 30 Go NVMe) pour déporter Docker+previews hors de la machine physique. **Les corrections ci-dessous sont dimensionnées pour la machine réelle actuelle.**

---

## 1. Contraintes réelles mesurées dans le code (faits)

| Élément | Valeur réelle | Fichier / ligne |
|---|---|---|
| RAM / CPU du conteneur preview | `--cpus 1.0 --memory 1g` | `deploy/docker/forge-preview` (defaults ligne ~250) |
| Swap accessible au conteneur | **0** (`--memory-swap 1g` = swap = memory ⇒ aucun swap) | `deploy/docker/forge-preview` ligne 321 |
| `tmpfs /tmp` (HOME, cache npm **et** pip) | **256 Mo** seulement | `deploy/docker/forge-preview` ligne 325 |
| `tmpfs /run` | 64 Mo | ligne 326 |
| `pids-limit` | 256 | ligne 322 |
| Mode exécution | `--read-only`, `--cap-drop ALL`, `no-new-privileges`, `USER preview` | lignes 323-329 + Dockerfile |
| Exécution des scripts | `docker exec` **sans TTY** (`/bin/bash -lc`) | wrapper verbe `exec` |
| Lancement du run | `docker exec --detach` | `backend/preview_runtime.py` ligne ~925 |
| Install des targets | **en parallèle** (une `asyncio.create_task` par target) | `_start_docker` lignes 868-876 |
| Journal | install dans un fichier hôte, run via `docker logs` séparé | `logs()` lignes 1355-1400 |

---

## 2. Bugs trouvés (classés par gravité)

### 🔴 BUG A — Journal d'installation figé (cause racine du « en installation » sans log)
- Le wrapper exécute `dk exec <ctr> /bin/bash -lc "/workspace/.forge-preview/<target>.install.sh"` **sans `-t`**.
- Sans pseudo-TTY, `bash`, `python` et `pip` passent `stdout`/`stderr` en **bufferisé par blocs** (8 Ko), pas en « line buffered ».
- Résultat : le fichier log hôte reste vide/figé **jusqu'à EOF ou un signal**. C'est exactement le symptôme observé : les logs apparaissent d'un coup **au clic « Arrêter »** (SIGTERM → vidange des tampons).
- **Correction** : débuffering explicite (voir Étape 1).

### 🔴 BUG B — Contrainte mémoire/swap inadaptée à la machine réelle
- Conteneur plafonné à **1 Go de RAM et 0 swap**, alors que :
  - l'install `api` (serveur Forge complet : Mongo, litellm, google-auth, opentelemetry, websockets) monte **facilement au-dessus de 1 Go** en pic de build,
  - `npm install` (frontend vite) **en parallèle** consomme aussi plusieurs centaines de Mo,
  - les caches npm+pip partagent un `tmpfs /tmp` de **256 Mo** seulement.
- Résultat : OOM killer / contention CPU extrême / install qui « ne finit jamais » ou crashe sans log lisible.
- **Correction** : sérialiser les installs + relever mémoire/swap/tmpfs en respectant les 4 Go réels (voir Étape 2).

### 🟠 BUG C — Lancement du run en `--detach` : sortie ni streamée ni surveillée
- `docker exec --detach` (ligne ~925) détache uvicorn dans les logs du conteneur ; le backend n'écrit **rien** dans le fichier log hôte.
- On attend 180 s la boucle `_port_open`, puis `_record_failure("port non ouvert apres 180s — voir les logs")` : **aucun traceback remonté**.
- De plus, le run étant **non-TTY**, `python` bufferise aussi sa sortie → le vrai message d'erreur n'apparaît pas non plus en temps réel.
- **Correction** : run streamé + échec rapide avec 20 dernières lignes (voir Étape 3).

### 🟠 BUG D — `tmpfs /tmp` de 256 Mo trop petit
- `HOME`, `npm_config_cache` et `PIP_CACHE_DIR` pointent **tous** sur `/tmp` (voir Dockerfile `ENV`).
- `pip install` du serveur Forge + `npm install` de vite dépassent très vite 256 Mo de cache cumulé → erreurs « No space left on device » ou crashs de cache.
- **Correction** : porter `/tmp` à au moins 1,5 Go (ou déplacer les caches sous `/forge-state`, monté `rw` sans limite tmpfs) — voir Étape 2.

### 🟡 BUG E — Détection Python fragile
- `_detect_python` (lignes 755-815) :
  - lit seulement les **2 premiers Mo** du fichier ; `backend/server.py` fait **322 Ko**, mais la règle est fragile pour d'autres projets,
  - si `FastAPI(`/`Flask(` n'est pas trouvé, il tombe en `kind="python"` → `python server.py` qui s'importe puis **sort aussitôt (code 0)** → port jamais ouvert,
  - si `FastAPI(` est détecté, il génère `uvicorn server:app` sans vérifier que la variable ASGI s'appelle bien `app` (peut être `api`, `application`).
- **Correction** : détection robuste + remontée du traceback (voir Étape 4).

### 🟡 BUG F — Marqueur `.forge-installed` trop confiant
- En docker, si `state_root/<proj>/venv/.forge-installed` existe, `__venv__` est sauté.
- Après un `rm -rf` partiel, le marqueur peut exister **sans** venv complet → install sautée → run crashe à l'import.
- **Correction** : réinstallation idempotente systématique ou marqueur versionné (voir Étape 4).

### 🟡 BUG G — Journal incohérent (install vs run)
- `logs()` mélange fichier hôte (install) + `docker logs` (run) sans garantie d'ordre, et le run n'est pas streamé.
- **Correction** : un seul fichier log hôte par target, alimenté par l'install **et** le run, dans l'ordre (voir Étape 5).

---

## 3. Plan d'implémentation (étapes / sous-étapes)

> Chaque étape est indépendante, committable et vérifiable seule.

### Étape 1 — Flush temps réel des scripts générés
**Objectif** : journal lisible en direct, ligne par ligne, dès le clic « Démarrer ».

**Fichiers** : `backend/preview_runtime.py` (`_venv_install_script`, `_script_run`, `_script_cmd`) et `deploy/docker/forge-preview` (verbe `exec`, option `-t`).

**Sous-étapes** :
1. Ajouter `export PYTHONUNBUFFERED=1` en tête de `_venv_install_script` et `_script_run`.
2. Préfixer les invocations `pip` par `python -u -m pip` (ou `stdbuf -oL -eL`).
3. Évaluer l'ajout de `-t` (pseudo-TTY) au `docker exec` du wrapper, en nettoyant les éventuels `\r` dans `_docker_stream`.

**Vérification** : sur un projet à install lourde, les lignes `Collecting …` apparaissent dans l'UI **au fur et à mesure**, < 5 s après le clic.
**Critère de fin** : plus de journal « figé », logs visibles avant la fin de l'install.

---

### Étape 2 — Adapter les ressources au serveur réel (4 Go RAM / 9 GiB swap)
**Objectif** : plus d'OOM, installs fiables dans les limites physiques.

**Fichiers** : `deploy/docker/forge-preview` (defaults + `RUN_ARGS`), éventuellement `backend/preview_runtime.py` (passage des flags).

**Sous-étapes** :
1. **2a — Sérialiser les installs** : dans `_start_docker`, remplacer les `asyncio.create_task(self._run_docker(...))` parallèles par un enchaînement séquentiel (install `web` puis `api`, ou l'inverse).
2. **2b — Mémoire** : porter le plafond conteneur de `1g` à une valeur sûre pour l'hôte 4 Go, ex. `--memory 2560m`.
3. **2c — Swap** : autoriser du swap conteneur en fixant `--memory-swap` au-dessus de `--memory` (ex. `--memory-swap 3g`), pour profiter des 9 GiB de swap hôte et éviter l'OOM killer immédiat.
4. **2d — tmpfs /tmp** : porter `--tmpfs /tmp` de `256m` à au moins `1536m`, ou rediriger `PIP_CACHE_DIR`/`npm_config_cache` sous `/forge-state` (monté `rw`, sans limite tmpfs).

**Vérification** : sur Fork-Clone, `npm install` (web) **puis** `pip install` (api) s'enchaînent sans OOM, sans « No space left on device ».
**Critère de fin** : les deux installs réussissent dans le journal, sans crash mémoire.

---

### Étape 3 — Run streamé et échec rapide
**Objectif** : voir le traceback du run en direct, et échouer en < 10 s au lieu de 180 s.

**Fichiers** : `backend/preview_runtime.py` (`_run_docker`, `_docker_stream`, `_kill`).

**Sous-étapes** :
1. **3a** : remplacer `docker exec --detach` du run par un streaming `_docker_stream` dans le même fichier log hôte, dans une `asyncio.create_task` dédiée (le run reste vivant pendant la sonde de port).
2. **3b** : si le process de run meurt avant l'ouverture du port, lire le fichier log et remonter les **20 dernières lignes** dans `_record_failure` au lieu de « voir les logs ».
3. **3c** : adapter `_kill`/`stop` pour annuler proprement la task de streaming et tuer l'`exec`.

**Vérification** : sur un backend qui crashe à l'import, l'UI affiche le traceback réel < 10 s après « Démarrer ».
**Critère de fin** : plus aucun `"port non ouvert apres 180s — voir les logs"` sans détail.

---

### Étape 4 — Robustifier détection Python + marqueur d'install
**Objectif** : ne plus générer `python server.py` (import puis exit) ni sauter une install incomplète.

**Fichiers** : `backend/preview_runtime.py` (`_detect_python`).

**Sous-étapes** :
1. **4a** : détecter la variable ASGI réelle (`app`, `application`, `api`, `asgi_app`) et lire au-delà de 2 Mo si nécessaire.
2. **4b** : si aucun framework ASGI/WSGI identifié et que le fichier est un « serveur » générique, ne pas choisir `kind="python"` aveuglément.
3. **4c** : remplacer la confiance aveugle dans `.forge-installed` par une réinstallation idempotente (ou un marqueur incluant un hash de `requirements.txt`).

**Vérification** : sur Fork-Clone, le plan `api` contient bien `uvicorn <module>:<app>` avec la **bonne** variable, et l'install ne saute plus après un `rm -rf` partiel.
**Critère de fin** : la commande de run détectée est valide et vérifiable dans le journal.

---

### Étape 5 — Unifier le journal (un seul fichier par target)
**Objectif** : un journal chronologique unique, install + run mélangés dans l'ordre.

**Fichiers** : `backend/preview_runtime.py` (`logs`, `_docker_stream`).

**Sous-étapes** :
1. Faire écrire l'install **et** le run dans le même `st.log_path`.
2. Conserver `docker logs` uniquement comme fallback de diagnostic (ou le retirer du flux principal).
3. Ajouter des séparateurs horodatés (`===== install / run =====`).

**Vérification** : `GET /preview/logs` renvoie install puis run, dans l'ordre, sans blocs manquants.
**Critère de fin** : le journal unique montre tout le cycle, sans recours à `docker logs` pour voir le run.

---

### Étape 6 — Redéploiement et validation de bout en bout
**Objectif** : mettre en prod les correctifs (dogfooding) et valider sur Fork-Clone.

**Fichiers** : `backend/preview_runtime.py`, `deploy/docker/forge-preview`, image `forge-preview-base` (si `-t`/cache l'exigent), `frontend` (a priori aucun changement).

**Sous-étapes** :
1. **6a** : déployer `preview_runtime.py` + le wrapper via `upgrade.sh`.
2. **6b** : rebuilder l'image si nécessaire (`build-d3.sh`).
3. **6c** : test réel « Arrêter » → « Rafraîchir » → « Démarrer » sur Fork-Clone.
4. **6d** : vérifier que le journal défile en direct, que les deux installs s'enchaînent sans OOM, et que le run affiche soit l'ouverture du port soit un traceback explicite.

**Critère de fin** : plus de statut « installation » figé, plus de journal vide, échec ou succès visibles et expliqués en < 10 s.

---

## 4. Ordre de mise en œuvre recommandé

1. **Étape 1** (flush) — impact immédiat sur la visibilité, prérequis des étapes 3/5.
2. **Étape 2** (ressources) — impact immédiat sur la fiabilité des installs.
3. **Étape 3** (run streamé) — élimine les suppositions en cas d'échec.
4. **Étape 4** (détection) — évite les faux succès d'install.
5. **Étape 5** (journal unifié) — consolidation lisible.
6. **Étape 6** (déploiement + validation réelle).
