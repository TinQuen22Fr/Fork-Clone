# Rapport — Bascule des previews Forge en conteneurs Docker isolés

**Date** : 2026-10-19 (CEST)
**Périmètre** : `backend/preview_runtime.py`, `backend/server.py`, `deploy/`, unité systemd `forge-backend`
**Statut** : analyse de faisabilité + plan d'implémentation — **aucune modification appliquée**

---

## 1. Intention retenue

Faire tourner les previews de projets dans des **conteneurs Docker jetables et bornés**, au lieu de processus natifs sur l'OS principal.

Objectifs :

1. **Confinement des boucles infinies** : un projet qui se relance en boucle ne doit plus saturer la machine ni faire tomber le SSH.
2. **Docker uniquement comme sandbox de preview** : pas de service applicatif, pas de base de données.
3. **Veille après inactivité** : conteneur arrêté, **workspace conservé** (bind mount) pour le push GitHub ; **aucun réveil automatique**.
4. **Zéro exécution opportuniste sur l'hôte** : plus de compilation ni de service lancé hors sandbox par les agents.
5. **Secrets maîtrisés** : `.env` jamais copié dans l'image, injection filtrée à l'exécution, masquage textuel systématique.

---

## 2. État des lieux constaté

| Élément | Constat |
|---|---|
| Docker / Compose | **absent** du serveur |
| cgroup | **v2** — support natif de `--memory`, `--cpus`, `pids.max` |
| systemd | 259 |
| Espace disque `/` | **828 Go** libres |
| Service backend | `User=quentin`, `WorkingDirectory=/var/www/forge/backend` |
| Driver preview actuel | `backend/preview_runtime.py` — `subprocess.Popen` / `asyncio.create_subprocess_exec` **sur l'hôte** |
| Ports preview | `PREVIEW_PORT_BASE=8090` → `PREVIEW_PORT_MAX=8189` |
| Exposition | Nginx + wildcard `*.<PREVIEW_DOMAIN_SUFFIX>` |
| Inactivité | `PREVIEW_IDLE_TIMEOUT=900` s, reaper déjà présent |
| Autostart | **déjà OFF** par défaut |
| Masquage existant | `redact_secrets()` (`server.py` l.2042), `_redact()` (l.6474) — réutilisables |

**Conclusion de faisabilité : OUI.** L'architecture actuelle (manager de previews + reaper d'inactivité + proxy Nginx) est déjà la bonne forme. Il s'agit de remplacer le **driver d'exécution** (process natif → `docker run`) sans changer le contrat d'API côté frontend.

---

## 3. Risques et parades

| Risque | Parade |
|---|---|
| Consommation CPU/RAM en boucle | `--cpus`, `--memory`, `--memory-swap` égal, `--pids-limit`, `--restart=no` |
| Fork-bomb | `--pids-limit` (cgroup v2 `pids.max`) |
| Écriture hors du projet | bind mount du seul dossier projet + `--read-only` + `tmpfs /tmp` |
| Agent reprenant le contrôle de l'hôte | **jamais** de montage de `/var/run/docker.sock` dans une preview |
| Backend `User=quentin` pilotant Docker (= root de fait) | **wrapper sudoers restreint** plutôt que groupe `docker` |
| Fuite de secrets dans les logs | `.env` lu côté backend, injecté en `--env`, sorties via `redact_secrets()` |
| Perte du travail non poussé | workspace en **bind mount** → survit à `docker stop` / `docker rm` |
| Réveil incontrôlé | pas de `restart: always`, pas de relance au boot, `docker start` sur action utilisateur uniquement |

---

## 4. Étapes structurées indispensables

### Étape D1 — Installation et durcissement de Docker
1. Installer `docker-ce` + `docker-compose-plugin` depuis le dépôt officiel, activer `docker.service`.
2. `/etc/docker/daemon.json` :
   - `"live-restore": true`
   - `"no-new-privileges": true`
   - `"log-driver": "local"`, `"log-opts": {"max-size": "10m", "max-file": "3"}`
   - `"default-ulimits": {"nofile": {"Name": "nofile", "Hard": 4096, "Soft": 4096}}`
   - pool d'adresses bridge dédié (évite les collisions avec les réseaux existants).
3. Vérifier : `docker run --rm hello-world`, puis `docker info` (cgroup v2, driver `overlay2`).

### Étape D2 — Contrôle d'accès sans donner root au backend
1. Créer `/usr/local/bin/forge-preview` : wrapper strict, verbes fermés (`start|stop|rm|ps|logs|inspect`), identifiant de projet validé par regex.
2. Sudoers dédié : `quentin ALL=(root) NOPASSWD: /usr/local/bin/forge-preview *` — **aucune** autre commande docker.
3. **Ne pas** ajouter `quentin` au groupe `docker` (équivalent root total).
4. Vérifier qu'une preview ne peut pas invoquer Docker (socket non monté).

### Étape D3 — Image de base commune
1. `deploy/docker/preview-base.Dockerfile` : Node LTS + Python 3 + `git`, utilisateur non-root `preview` (UID fixe).
2. `USER preview`, `WORKDIR /workspace`.
3. Aucun secret, aucun `.env`, aucun code projet dans l'image.
4. Build unique, tag `forge-preview-base:<version>`.

### Étape D4 — Contrat d'exécution imposé
- `--name forge-preview-<project>`
- réseau bridge dédié, `-p 127.0.0.1:<port>:<port>` (**jamais** `0.0.0.0` ; Nginx reste le seul point d'entrée public)
- `--cpus=1.0`, `--memory=1g`, `--memory-swap=1g`, `--pids-limit=256`
- `--read-only` + `--tmpfs /tmp:rw,size=256m` + `--tmpfs /run:rw,size=64m`
- `-v <workspace_projet>:/workspace:rw`
- `--security-opt no-new-privileges`, `--cap-drop ALL`
- `--restart=no`
- pas de `--env-file` : variables injectées une à une après filtrage.

### Étape D5 — Remplacement du driver dans `preview_runtime.py`
1. Conserver l'API publique (`start`, `stop`, `status`, `logs`, `idle_projects`) : le frontend ne change pas.
2. Remplacer le corps de `start()` / `_run()` / `_spawn_wait()` / `_kill()` par des appels au wrapper `forge-preview`.
3. Supprimer l'exécution `subprocess.Popen` sur l'hôte pour les previews.
4. Conserver la détection de port (`plan()`), l'image recevant `PORT` en variable.
5. `logs()` : `docker logs --tail` au lieu du fichier local.

### Étape D6 — Veille et conservation du workspace
1. Le reaper existant (`PREVIEW_IDLE_TIMEOUT`) appelle `docker stop` — **pas** `docker rm`.
2. Workspace en bind mount → push GitHub toujours possible après arrêt.
3. **Aucune** relance automatique : ni `restart: always`, ni reprise au démarrage (`PREVIEW_AUTOSTART` reste OFF).
4. Démarrage uniquement sur action explicite.

### Étape D7 — Nettoyage et quotas disque
1. Tâche périodique : `docker rm` des conteneurs arrêtés au-delà de N jours, `docker image prune`.
2. Contrôle `du` du workspace avant lancement, refus au-delà du seuil.
3. Alerte si l'espace libre `/` passe sous un seuil (ex. 50 Go).

### Étape D8 — Secrets et masquage
1. Backend lit le `.env` du projet, **filtre les clés** (liste blanche), injecte en `--env`.
2. **Interdiction** de monter le `.env` global de la Forge dans un conteneur.
3. Toute sortie (`logs`, erreurs de build, `docker inspect`) passe par `redact_secrets()` avant affichage/stockage.
4. Agents : `sed`/`grep` avec troncature (`| sed -n '1,80p'`, `| cut -c1-200`), jamais `cat` d'un `.env` ; valeurs sensibles remplacées par `***REDACTED***`.
5. Test : un `.env` contenant `API_KEY=xxx` ne doit jamais apparaître en clair dans les logs.

### Étape D9 — Interdiction d'exécution sur l'OS principal
1. Revue des points de lancement de processus : toute exécution liée à un projet passe par le sandbox.
2. Les outils agent qui compilent/lancent un service ciblent le conteneur ou le répertoire projet monté, jamais l'hôte.
3. Journaliser chaque `forge-preview start|stop` (qui, quand, projet) pour audit.

### Étape D10 — Observabilité et garde-fous anti-boucle
1. Compteur de redémarrages par preview ; au-delà de **3 échecs consécutifs en 5 min**, preview **désactivée** et signalée.
2. Backoff obligatoire entre deux démarrages du même projet (ex. 30 s).
3. Métriques dans `/chat/status` : état conteneur, CPU/RAM, uptime, dernier code de sortie.

### Étape D11 — Tests et recette
1. Unitaire : construction de la ligne `docker run` (toutes les limites, pas de `0.0.0.0`, pas de socket).
2. Intégration : démarrage, arrêt par inactivité, workspace intact, redémarrage manuel.
3. Charge : une preview qui boucle ne dégrade pas l'hôte (CPU plafonné, SSH réactif).
4. Non-régression : les routes preview existantes répondent à l'identique.

### Étape D12 — Bascule et retour arrière
1. Feature flag `PREVIEW_DRIVER=docker|native` (défaut `native` jusqu'à validation).
2. Bascule progressive : un projet pilote, puis généralisation.
3. Retour arrière immédiat : repasser le flag à `native`, les workspaces n'ayant jamais quitté l'hôte.

---

## 5. Ordre d'exécution recommandé

`D1 → D2 → D3 → D4 → D5 → D6 → D11 → D12` (socle fonctionnel)
puis `D7 → D8 → D9 → D10` (durcissement, quotas, observabilité)

D8 (secrets) doit être traité **avant** la bascule générale si des projets contiennent des `.env` sensibles.

---

## 6. Points d'attention

- **Socket Docker** : ne jamais le monter dans une preview, sous aucun prétexte (outil, debug, script de projet).
- **Pas de réveil automatique** : cela suppose d'assumer qu'une preview reste éteinte après un reboot serveur.
- **Ports** : le binding reste `127.0.0.1` ; l'exposition publique demeure Nginx + wildcard.
- **Pas de gain de sécurité contre du code malveillant déterminé** : Docker borne les ressources et isole le système de fichiers, il ne remplace pas une VM. Pour un durcissement supérieur, envisager `gVisor` ou `Firecracker` ultérieurement.
- **Non vérifié à ce stade** : aucun test d'exécution réel n'a été mené, Docker n'étant pas installé. Les étapes D1 à D12 restent à exécuter.

---

## 7. Suivi d'exécution

*(section à compléter à chaque étape réalisée, avec date et heure CEST)*
