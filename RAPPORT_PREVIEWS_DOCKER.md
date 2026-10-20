# Rapport — Bascule des previews Forge en conteneurs Docker isolés

**Date** : 2026-10-19 (CEST)
**Périmètre** : `backend/preview_runtime.py`, `backend/server.py`, `deploy/`, unité systemd `forge-backend`
**Statut** : en exécution — **D1 et D2 conformes et vérifiées** (voir section 7) ; étapes D3 à D12 à venir

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
   - `"log-driver": "local"`, `"log-opts": {"max-size": "10m", "max-file": "3"}`
   - `"default-ulimits": {"nofile": {"Name": "nofile", "Hard": 4096, "Soft": 4096}}`
   - `"exec-opts": ["native.cgroupdriver=systemd"]`, `"storage-driver": "overlay2"`
   - `"default-address-pools"` : `172.31.0.0/16` en `/24` (évite les collisions avec les réseaux existants).
   - **Note** : `no-new-privileges` n'est **pas** une clé valide de `daemon.json` (dockerd refuse toute clé inconnue). Cette protection est par-conteneur (`--security-opt no-new-privileges`) et relève de D4.
3. Prérequis noyau persistés : `br_netfilter` (`/etc/modules-load.d/forge-docker.conf`) + `ip_forward`/`bridge-nf-call-*` (`/etc/sysctl.d/99-forge-docker.conf`).
4. Vérifier : `docker run --rm hello-world`, puis `docker info` (cgroup v2, driver `overlay2`, live-restore actif).

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
- **Non vérifié à ce stade** : D1 est installé et conforme (voir section 7). Les étapes D2 à D12 restent à exécuter ; les contrôles moteur exigent `sudo` car `quentin` est hors du groupe `docker`.

---

## 7. Suivi d'exécution

### Étape D1 — Installation et durcissement de Docker
- **Date** : 2026-10-19 23:47 CEST
- **Statut** : **installé et conforme** — vérification moteur reprise avec privilège, 19/19 contrôles OK (voir ci-dessous)
- **Commits** : `ba69b7c` (artefacts D1), `c2e9424` (correction du script de vérification)

**Livré** — `deploy/docker/`, 249 lignes :
| Fichier | Lignes | Rôle |
|---|---|---|
| `install-d1.sh` | 119 | Installation idempotente : dépôt officiel Docker, `daemon.json`, sysctl/modules, activation du service, contrôle `hello-world` |
| `verify-d1.sh` | 96 | Contrôles de conformité D1 en lecture seule (code retour 0 = conforme) |
| `daemon.json` | 26 | `live-restore`, `overlay2`, `cgroupdriver=systemd`, logs bornés 10m×3, `nofile` 4096, pool `172.31.0.0/16` en `/24` |
| `99-forge-docker.conf` | 5 | `ip_forward`, `bridge-nf-call-iptables`, `bridge-nf-call-ip6tables` |
| `forge-docker-modules.conf` | 3 | `br_netfilter` chargé au boot |

**Installation exécutée** — `sudo bash deploy/docker/install-d1.sh` (commande lancée par l'utilisateur) :
```
docker-ce 5:29.9.1, containerd.io 2.4.2, docker-compose-plugin 5.6.0, docker-buildx-plugin 0.38.0
version serveur : 29.9.1
cgroup          : 2 (driver systemd)
storage driver  : overlay2
live-restore    : true
data-root       : /var/lib/docker
hello-world : OK
groupe docker : quentin n'en est pas membre (conforme à D2)
```

**Vérification** — `sudo bash deploy/docker/verify-d1.sh` (2026-10-19 23:51 CEST) :
```
== D1 : prérequis noyau ==
  OK   cgroup v2 supporté
  OK   cgroup monté en v2
  OK   module br_netfilter chargé
  OK   module overlay chargé
  OK   ip_forward actif
== D1 : moteur ==
  OK   docker présent : Docker version 29.9.1, build dfc520d
  OK   démon répond
  OK   cgroup v2 côté moteur
  OK   storage-driver overlay2
  OK   live-restore actif
== D1 : configuration ==
  OK   /etc/docker/daemon.json présent
  OK   JSON valide
  OK   clés durcissement présentes
  OK   aucune clé invalide dans daemon.json
  OK   sysctl persisté
  OK   modules persistés
== D1 : isolation de l'accès ==
  OK   docker.service activé au boot
  OK   docker.service actif
  OK   quentin hors du groupe docker

RÉSULTAT : D1 conforme.
```
Lancé sans privilège, le script affiche « partiellement vérifié » (les 5 contrôles moteur ne sont pas re-testables : `quentin` est hors groupe `docker`, `docker info` renvoie `permission denied`). C'est le comportement voulu, pas un échec.

**Défaut corrigé dans le script de vérification** : la première version concluait 4 fois « KO » sur le moteur alors que l'installation avait réussi. Cause : `verify-d1.sh` était lancé en tant que `quentin`, volontairement hors du groupe `docker` ; `docker info` renvoyait donc `permission denied` et tous les champs (`CgroupVersion`, `Driver`, `LiveRestoreEnabled`) ressortaient vides. Le script bascule désormais sur `sudo -n docker info` et, si le socket reste inaccessible sans privilège, affiche « non vérifié » au lieu de « KO » — un défaut de privilège n'est pas un défaut du moteur.

**Correction apportée à la spécification** : D1 listait `"no-new-privileges": true` dans `daemon.json`. Cette clé **n'existe pas** dans la configuration du démon : `dockerd` refuse de démarrer sur une clé inconnue. La protection est par-conteneur (`--security-opt no-new-privileges`) et relève de D4. La spécification D1 a été corrigée en conséquence, `verify-d1.sh` détecte activement cette clé comme non conforme.

**Point d'attention pour D2** : `quentin` dispose de `(ALL : ALL) ALL` dans le sudoers actuel — la contrainte « pas de root pour le backend » de D2 devra traiter ce point, pas seulement le groupe `docker`. De plus, `sudo -n docker info` exige que `quentin` soit autorisé à lancer `docker` via sudo ; le compte actuel a un `NOPASSWD` limité à des scripts de preview, à étendre de façon ciblée si la vérification privilégiée doit rester automatisable.

---

### Étape D2 — Contrôle d'accès sans donner root au backend
- **Date** : 2026-10-20 02:10 CEST (vérification finale 02:22 CEST)
- **Statut** : **conforme** — installation posée, vérification privilégiée 30/30 OK, `RÉSULTAT : D2 conforme.` (2026-10-20 02:22 CEST)
- **Commits** : `aa22b35` (code D2 : wrapper, sudoers modèle, installateur, vérificateur), `20846d6` (correction des 3 faux négatifs du vérificateur) — ce bloc de suivi est figé par le commit qui le contient, voir `git log -- RAPPORT_PREVIEWS_DOCKER.md`
- **Installé le** : 2026-10-20

**Livrables** (`deploy/docker/`) :

| Fichier | Lignes | Rôle |
|---|---:|---|
| `forge-preview` | 217 | Wrapper strict : verbes `start\|stop\|rm\|ps\|logs\|inspect\|health`, projet validé par regex, port contraint à la plage, workspace confiné à `FORGE_PREVIEW_WORKSPACE_ROOTS` |
| `forge-preview.sudoers` | 6 | Modèle de règle sudoers (`quentin ALL=(root) NOPASSWD: /usr/local/bin/forge-preview *`) |
| `install-d2.sh` | 102 | Installation idempotente : wrapper root:root 0755, répertoire d'état, règle sudoers validée par `visudo`, test `sudo -n` |
| `verify-d2.sh` | 165 | 30+ contrôles conformité, à lancer en root |

**Conception du wrapper** — garanties appliquées :
- **Verbes fermés** : tout verbe hors liste renvoie le code `64` (`usage`).
- **Nom de projet** : `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, refus des `.`/`..`/slashes. Nom du conteneur forcé : `forge-preview-<projet>`.
- **Port** : entier contraint à `PREVIEW_PORT_BASE..PREVIEW_PORT_MAX+100` (le `+100` couvre `API_PORT_OFFSET` de `preview_runtime`). Un port hors plage (ex. `22`) est refusé.
- **Workspace** : chemin absolu, `readlink -f`, dossier existant, sans remontée `..`, et **obligatoirement sous `/var/www/forge/workspace`**. `/etc` → refus.
- **`--env`** : `CLE=VAL` uniquement, nom `[A-Za-z_][A-Za-z0-9_]*`, clés dangereuses interdites (`LD_PRELOAD`, `LD_LIBRARY_PATH`, `DOCKER_HOST`, `PATH`, `BASH_ENV`).
- **Aucune option libre** : pas de `--privileged`, pas de `-v` arbitraire, pas de `--network`. Les options en liste blanche sont consommées par la boucle, tout le reste est refusé.
- **Aucun socket** : le wrapper ne référence jamais `docker.sock` (contrôlé par l'installateur et le vérificateur).
- **Config docker déterministe** : `--config /var/lib/forge-preview/.docker` (fallback temporaire), pour éviter les warnings `~/.docker/config.json` en contexte `sudo`.
- **Contrat D4 déjà appliqué au `docker run`** : `--read-only`, tmpfs `/tmp` et `/run`, `--security-opt no-new-privileges`, `--cap-drop ALL`, `--cpus`, `--memory`/`--memory-swap`, `--pids-limit`, `--restart=no`, `-p 127.0.0.1:<port>:<port>`, `-v <workspace>:/workspace:rw`. D5 branchera `preview_runtime.py` sur ce wrapper.

**Tests de refus réellement exécutés** (hors privilège, code `64` attendu) :
```
start '../evil' --port 8090 --workspace /tmp          -> 64  nom de projet invalide
start demo --port 22 --workspace /tmp                 -> 64  port hors plage (8090-8289)
start demo --port 8090 --workspace /etc               -> 64  workspace hors racine
start demo --port 8090 --workspace /tmp --privileged  -> 64  option non reconnue
stop demo --force                                     -> 64  le verbe stop n'accepte aucune option
exec demo                                             -> 64  verbe inconnu (usage)
start demo                                            -> 64  --workspace exigé
```
`bash -n` OK sur les trois scripts, `jq` non concerné.

**Installation exécutée** — `sudo bash deploy/docker/install-d2.sh` (2026-10-20, lancée par l'utilisateur) :
```
[D2] installation du wrapper /usr/local/bin/forge-preview
[D2] écriture de la règle sudoers /etc/sudoers.d/forge-preview-docker
/tmp/forge-preview-sudoers.i4gqy2: parsed OK
[D2] règle sudoers installée et validée par visudo
[D2] quentin hors du groupe docker (conforme).
[D2] aucune règle sudoers n'autorise docker directement (conforme).
[D2] sudo -n /usr/local/bin/forge-preview health : OK (29.9.1)
[D2] D2 terminée. Wrapper : /usr/local/bin/forge-preview — Sudoers : /etc/sudoers.d/forge-preview-docker
```

**Première vérification privilégiée** — `sudo bash deploy/docker/verify-d2.sh` : **3 KO, tous faux négatifs du vérificateur**, aucune faille réelle :

| Contrôle KO | Cause réelle | Nature |
|---|---|---|
| « une règle sudoers autorise docker directement » | le `grep` matchait le **commentaire** d'en-tête du fichier généré (« AUCUNE autre commande docker n'est autorisée ») | défaut du script |
| « quentin accède à Docker sans privilège » | `docker info` imprime son bloc `Client: Docker Engine - Community` sur stdout **avant** d'échouer sur le socket ; le contrôle ne lisait que `head -n1` | défaut du script |
| « sudo -n docker a répondu » | le refus réel s'écrit `interactive authentication is required`, motif absent de la liste attendue (`password is required` seulement) | défaut du script |

Preuve que la configuration était déjà correcte : `sudo grep -rEn 'docker' /etc/sudoers /etc/sudoers.d/` ne renvoie **aucune ligne active**, et `sudo -n docker ps` échoue bien.

**Correction appliquée** (`20846d6`, dans `verify-d2.sh` *et* `install-d2.sh`) :
- le contrôle sudoers n'examine plus que les **lignes actives** (`grep -hvE '^[[:space:]]*(#|$)'`) ;
- l'accès non privilégié est testé par `docker ps` (et non `docker info`), sur la **sortie complète** ;
- les motifs de refus `sudo -n` couvrent `interactive authentication is required`, `a terminal is required`, `no tty present`, `askpass`, `not allowed`.

**Vérification finale** — `sudo bash deploy/docker/verify-d2.sh` (2026-10-20 02:22 CEST) : **30/30 OK**, `RÉSULTAT : D2 conforme.`
```
== D2 : wrapper ==
  OK   wrapper présent : /usr/local/bin/forge-preview
  OK   propriétaire root:root
  OK   mode 755 (non modifiable par le backend)
  OK   wrapper identique au dépôt (sha256)
  OK   le wrapper ne mentionne pas docker.sock
== D2 : règle sudoers ==
  OK   fichier présent : /etc/sudoers.d/forge-preview-docker
  OK   propriétaire root:root
  OK   mode 0440
  OK   syntaxe validée par visudo
  OK   exactement 1 règle active
  OK   règle conforme pour quentin (wrapper seul, NOPASSWD)
  OK   aucune règle sudoers n'autorise docker directement
== D2 : isolation de l'accès ==
  OK   quentin hors du groupe docker
  OK   quentin n'accède pas au socket Docker sans privilège
  OK   sudo -n docker refusé pour quentin (aucun accès direct)
== D2 : fonctionnement du wrapper ==
  OK   sudo -n forge-preview health : OK (moteur 29.9.1)
  OK   refus attendu (projet avec traversée) : code 64
  OK   refus attendu (port hors plage) : code 64
  OK   refus attendu (workspace hors racine) : code 64
  OK   refus attendu (verbe inconnu) : code 64
  OK   refus attendu (option non listée) : code 64
  OK   sudo -n forge-preview ps -- : le verbe atteint le moteur
== D2 : contrat d'exécution (garde-fous présents dans le wrapper) ==
  OK   garde-fou présent : security-opt no-new-privileges
  OK   garde-fou présent : cap-drop ALL
  OK   garde-fou présent : read-only
  OK   garde-fou présent : pids-limit
  OK   garde-fou présent : memory-swap
  OK   garde-fou présent : restart=no
  OK   garde-fou présent : /workspace:rw
  OK   garde-fou présent : 127.0.0.1

RÉSULTAT : D2 conforme.
```

**Point d'attention hérité de D1, traité** : le sudoers de `quentin` contient toujours `(ALL : ALL) ALL` (règle d'administration personnelle, hors périmètre applicatif). D2 ne s'appuie pas dessus : le backend n'obtient l'accès Docker que par le wrapper, et `verify-d2.sh` contrôle activement qu'aucune règle sudoers n'autorise `/usr/bin/docker` directement. Aucun ajout au groupe `docker`.

**Réserves honnêtes** :
- L'image `forge-preview-base` (D3) n'existe pas encore : un `start` réel échouera jusqu'à D3. Les tests D2 valident les refus et le passage au moteur, pas le démarrage d'un conteneur.
- `--read-only` sans volume persistant sur `/home/preview` (D3/D4) devra être complété au moment du build de l'image.
- Le wrapper est en `0755` : lisible par tous, exécutable par tous, mais ne fait rien sans l'autorisation sudoers. Les refus sont appliqués avant tout appel moteur.

**Retour arrière** : `git reset --hard aa22b35^` annule l'intégralité de D2 (code + correction du vérificateur) ; pour ne retirer que la correction des faux négatifs, `git revert 20846d6`. Désinstallation système : `sudo rm -f /usr/local/bin/forge-preview /etc/sudoers.d/forge-preview-docker`.

---

## 8. Point de reprise — 2026-10-20 02:25 CEST

Session arrêtée à la demande de l'utilisateur. Rien n'a été poussé sur le remote : la branche `claude-ai` est en avance de **11 commits** sur `origin/claude-ai`.

### 8.1 Étapes terminées et vérifiées

| Étape | Statut | Preuve |
|---|---|---|
| **D1** — Installation et durcissement Docker | **conforme** | `sudo bash deploy/docker/verify-d1.sh` → 19/19 OK, « RÉSULTAT : D1 conforme. » (2026-10-19 23:51 CEST) |
| **D2** — Contrôle d'accès sans root pour le backend | **conforme** | `sudo bash deploy/docker/verify-d2.sh` → 30/30 OK, « RÉSULTAT : D2 conforme. » (2026-10-20 02:22 CEST) |

### 8.2 État du système au moment de l'arrêt

- Docker Engine `29.9.1` actif, `cgroup v2`, `overlay2`, `live-restore` actif, activé au boot.
- `/etc/docker/daemon.json` présent et durci ; sysctl et modules noyau persistés (`deploy/docker/99-forge-docker.conf`, `deploy/docker/forge-docker-modules.conf`).
- `/usr/local/bin/forge-preview` installé — `root:root`, mode `0755`, sha256 identique au dépôt.
- `/etc/sudoers.d/forge-preview-docker` installé — `root:root`, mode `0440`, validé par `visudo`, **exactement 1 règle active** : `quentin ALL=(root) NOPASSWD: /usr/local/bin/forge-preview *`.
- `quentin` **hors** du groupe `docker` ; `sudo -n docker` refusé ; aucune règle sudoers n'autorise `docker` directement.
- Rappel d'exploitation : `quentin` conserve `(ALL : ALL) ALL` dans le sudoers principal. Ce n'est **pas** une faille du dispositif (le backend n'utilise pas sudo) mais cela signifie que l'isolation repose sur le code applicatif, pas sur une impossibilité technique. À traiter si l'on veut une garantie forte.

### 8.3 Artefacts livrés

| Fichier | Lignes | Étape |
|---|---:|---|
| `deploy/docker/install-d1.sh` | 119 | D1 |
| `deploy/docker/verify-d1.sh` | 79 | D1 |
| `deploy/docker/daemon.json` | 26 | D1 |
| `deploy/docker/99-forge-docker.conf` | — | D1 |
| `deploy/docker/forge-docker-modules.conf` | — | D1 |
| `deploy/docker/forge-preview` | 217 | D2 |
| `deploy/docker/forge-preview.sudoers` | 6 | D2 |
| `deploy/docker/install-d2.sh` | 102 | D2 |
| `deploy/docker/verify-d2.sh` | 165 | D2 |

### 8.4 Commits locaux de référence

```
99bc98f  docs(previews/docker): D2 conforme — verification privilegiee 30/30 OK   ← suivi D2
145bfb8  docs(previews/docker): section 7 — D2 installee, 3 faux negatifs corriges
20846d6  fix(previews/docker): verify-d2 — 3 faux negatifs
238d0ba  docs(previews/docker): section 7 — D2
aa22b35  feat(previews/docker): D2 — wrapper forge-preview + sudoers dedie      ← code D2
f8918fe  docs(previews/docker): D1 conforme — verification privilegiee 19/19 OK   ← suivi D1
dc0ca8e  docs(previews/docker): section 7 — statut D1 exact
43655cf  docs(previews/docker): section 7 — D1 executee et conforme
c2e9424  fix(previews/docker): verify ne conclut plus KO sans privilege
ba69b7c  feat(previews/docker): D1 — installation et durcissement                ← code D1
```

Points de retour arrière :
- annuler tout D2 : `git reset --hard aa22b35^`
- annuler tout D1 : `git reset --hard ba69b7c^`
- ne retirer que la correction des faux négatifs D2 : `git revert 20846d6`
- désinstallation système D2 : `sudo rm -f /usr/local/bin/forge-preview /etc/sudoers.d/forge-preview-docker`

### 8.5 Ce qui reste à faire

Ordre recommandé (section 5) : `D3 → D4 → D5 → D6 → D11 → D12` (socle fonctionnel) puis `D7 → D8 → D9 → D10` (durcissement, quotas, observabilité).

**Prochaine étape : D3 — Image de base commune.**

Points déjà identifiés comme devant être tranchés en D3 :
- `--read-only` (contrat D4) impose de traiter `/home/preview` : volume dédié ou `tmpfs`, sinon npm/pip échouent au build ou à l'exécution.
- L'UID fixe de l'utilisateur `preview` doit être réconcilié avec l'UID hôte de `quentin` (`1000`) pour les permissions du workspace monté en `rw`.
- Aucun secret, aucun `.env` embarqué dans l'image.

### 8.6 État du dépôt

- Branche `claude-ai`, working tree propre **hors `.forge-context`** (fichier de contexte de l'agent, modifié et volontairement non commité — hors périmètre du rapport).
- 11 commits locaux non poussés : le `git push` est à la charge de l'utilisateur via l'interface GitHub.
