# Migration de la Forge et des autres projets : Dedibox vers OVH

## 1. Cible

| Élément | Valeur |
|---|---|
| CPU | Xeon E-2274G, 4c/8t, 4,0/4,9 GHz, AVX2, AES-NI, VT-x/VT-d, iGPU UHD P630 (Quick Sync) |
| RAM | 32 Go ECC |
| Swap | 2 × 512 Mo (partitions) + swapfile 10 Go |
| Disques | 2 × 4 To HDD SATA, RAID 1 logiciel (mdadm) |
| OS | Ubuntu Server 26.04 LTS, Ubuntu Pro actif |
| sudo | sans mot de passe, commandes lancées depuis `/var/www/...` |

Principe : la migration est aussi une **montée de version générale**. Chaque logiciel et chaque dépendance est installé dans sa **dernière version stable** disponible sur 26.04, et non recopié à l'identique de la Dedibox. Les scripts du dépôt (`install.sh`, `deploy/docker/*`) servent de base ; tout écart de version qu'ils imposent est relevé en phase 2 et corrigé dans le dépôt avant installation (voir section 4bis).

## 2. Cartographie de l'existant à migrer

| Composant | Où | Mode de migration |
|---|---|---|
| Code Forge | `/var/www/forge`, branche `claude-ai` | `git clone`, pas de copie |
| Secrets | `backend/.env` (109 clés) | `scp` |
| Service backend | `forge-backend.service`, uvicorn `127.0.0.1:8001`, 1 worker, user `quentin` | posé par `install.sh` |
| Base | MongoDB `forge` (+ `forge_preview`, recréée par `upgrade.sh`) | `mongodump` / `mongorestore` |
| Workspace projets | `/var/www/forge/workspace` | `rsync` |
| Previews Docker | `deploy/docker/` (D1→D14), wrapper `/usr/local/bin/forge-preview`, sudoers, `daemon.json`, image `preview-base` (node 26) | réinstallation par scripts, image reconstruite |
| Nginx | `deploy/nginx-forge.conf`, `deploy/nginx-forge-preview.conf`, maps `/etc/nginx/forge-preview-*.map` | conf du dépôt, maps régénérées |
| TLS | `forge.quentin-astro.fr` + wildcard `*.preview.quentin-astro.fr` | copie de `/etc/letsencrypt` |
| LiteLLM | venv `/var/www/forge/litellm-venv`, service `litellm`, `litellm_config.yaml` | venv recréé, unit copiée |
| 9Router | `localhost:20128` | **mode d'installation inconnu**, à inventorier |
| Ollama | `localhost:11434`, `OLLAMA_NUM_THREAD=2` | réinstall + modèles copiés ou repull |
| Kokoro TTS | `backend/models/kokoro-v1.0.onnx` | `rsync` du dossier `models/` |
| Playwright | `/var/www/forge/.playwright` | `deploy/install-playwright.sh` |
| Autres projets | `/var/www/*`, autres vhosts | inventaire en phase 0 |

**Valeurs figées dans les scripts, à reconfigurer pour OVH (phase 3bis) :**
- user de service `quentin` : `deploy/forge-backend.service`, `deploy/docker/forge-preview.sudoers`, `deploy/docker/install-d1.sh` (en dur) ; `install.sh`, `upgrade.sh`, `deploy/docker/install-d2.sh` (défaut de `SERVICE_USER`). Cible : le user existant sur OVH.
- chemin `/var/www/forge` : service, `install.sh`, `upgrade.sh`, `deploy/ensure-rwpaths.sh`, `deploy/setup-preview*.sh`, `deploy/install-playwright.sh`.
- domaines `*.quentin-astro.fr` : `deploy/nginx-forge*.conf`, `deploy/setup-preview*.sh`.
- versions : `PREVIEW_NODE_VERSION` (`build-d3.sh`), `requirements.txt`, `package.json` (voir 4bis).

## 4. Procédure

Sauf mention contraire, chaque phase se lance sur la machine indiquée. **S** = Dedibox source, **O** = OVH.

### Phase 0 : inventaire (S, lecture seule)

Objectif : ne rien oublier hors Forge.

```bash
cd /var/www
ls -la /var/www
ls /etc/nginx/sites-enabled
systemctl list-units --type=service --state=running --no-pager
crontab -l; sudo crontab -l; ls /etc/cron.d
sudo docker ps -a; sudo docker images
mongod --version; apt-cache policy mongodb-org mongodb-server 2>/dev/null | head
node -v; npm -v; python3 --version
ollama list 2>/dev/null
systemctl cat litellm 2>/dev/null | head -30
ss -ltnp                                  # 9Router (20128) : identifier le process et son origine
sudo ls /etc/letsencrypt/live
df -h; du -sh /var/www/forge/workspace /var/lib/docker /var/lib/mongodb
```

Fin : liste écrite de tous les services, ports, vhosts, crons, versions et méthode d'installation de MongoDB et 9Router.

### Phase 1 : système (O)

```bash
cd /var/www
sudo pro status
grep -o -m1 'avx2' /proc/cpuinfo; ls /dev/dri 2>/dev/null
```

Actions :
1. `sudo apt update && sudo apt full-upgrade`.
2. `timedatectl set-timezone Europe/Paris`.

Fin : système à jour.

### Phase 2 : stack (O). Requiert la phase 1.

1. Nginx, certbot, `python3-certbot-nginx`, `python3-venv`, `git`, `build-essential`, `rsync`.
2. MongoDB : **dernière version stable** via le dépôt officiel `repo.mongodb.org`. Si aucun dépôt n'existe encore pour `resolute`, prendre celui de la LTS Ubuntu précédente la plus récente supportée. AVX2 présent : aucune limite CPU pour Mongo ≥ 5.
3. Node : **dernière LTS** (dépôt NodeSource ou binaire officiel), npm à jour (`npm i -g npm@latest`).
4. Docker : `deploy/docker/install-d1.sh`, qui installe déjà la dernière `docker-ce` + compose + buildx depuis le dépôt Docker officiel.
5. Python : version par défaut de 26.04 (`python3 --version`). `backend/requirements.txt` porte déjà des marqueurs `python_version >= "3.13"` qui sélectionnent les versions récentes de fastapi/uvicorn.

Vérif : `nginx -v`, `mongosh --eval 'db.version()'`, `node -v`, `docker info`.
Fin : chaque composant est à sa dernière version stable ; versions notées dans le rapport.

### Phase 2bis : validation des nouvelles versions (O). Requiert la phase 3.

1. Base vide : `./install.sh`, puis `curl` sur `/api/health`.
2. Restaurer un `mongodump --db forge --gzip --archive` fait sur S pour l'occasion sur une base de test (`--nsFrom 'forge.*' --nsTo 'forge_test.*'`) et vérifier qu'elle se lit avec la nouvelle MongoDB, puis aligner la compatibilité sur la version installée :
   `mongosh --eval "db.adminCommand({setFeatureCompatibilityVersion: db.version().split('.').slice(0,2).join('.'), confirm: true})"`
3. Front : `cd frontend && npm install && npm run build` sans erreur.
4. Si une dépendance casse : correction dans le dépôt, commit via **Enregistrer sur GitHub**, `git pull` sur O.

Fin : backend, front et base fonctionnent sur les dernières versions.

### Phase 3 : code et secrets (O). Requiert la phase 2.

```bash
cd /var/www
git clone -b claude-ai <url-du-depot> /var/www/forge
```

Depuis S :

```bash
cd /var/www/forge
scp backend/.env <user>@<ip-ovh>:/var/www/forge/backend/.env
rsync -aH --info=progress2 backend/models/ <user>@<ip-ovh>:/var/www/forge/backend/models/
```

Sur O : phase 3bis, puis `cd /var/www/forge && sudo ./install.sh`.

Vérif : `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/api/health` renvoie `200`.
Fin : le backend tourne sur une base vide.

### Phase 3bis : reprogrammer les scripts pour OVH. Requiert la phase 3.

1. Remplacer les valeurs figées listées en section 2 par celles d'OVH (user, chemins, domaines).
2. Monter les versions des scripts et manifestes (section 4bis).
3. Commit via **Enregistrer sur GitHub**, `git pull` sur O.

Vérif : `grep -rn 'quentin' deploy install.sh upgrade.sh` ne renvoie plus que les valeurs voulues ; `bash -n` sur chaque script modifié.
Fin : `install.sh` et `deploy/` tournent tels quels sur OVH.

### Phase 4 : previews Docker (O). Requiert la phase 3.

Lancer les scripts du dépôt dans l'ordre, chacun suivi de son `verify-dN.sh` :

```bash
cd /var/www/forge
sudo bash deploy/docker/install-d1.sh   && sudo bash deploy/docker/verify-d1.sh
sudo bash deploy/docker/install-d2.sh   && sudo bash deploy/docker/verify-d2.sh
sudo bash deploy/docker/build-d3.sh     && sudo bash deploy/docker/verify-d3.sh
sudo bash deploy/docker/install-d4.sh   && sudo bash deploy/docker/verify-d4.sh
sudo bash deploy/docker/install-d5.sh   && sudo bash deploy/docker/verify-d5.sh
sudo bash deploy/docker/verify-d6.sh
sudo bash deploy/docker/install-d7.sh   && sudo bash deploy/docker/verify-d7.sh
sudo bash deploy/docker/install-d8.sh   && sudo bash deploy/docker/verify-d8.sh
sudo bash deploy/docker/install-d9.sh   && sudo bash deploy/docker/verify-d9.sh
sudo bash deploy/docker/install-d10.sh  && sudo bash deploy/docker/verify-d10.sh
for t in d10a d10b d10c d10d d10e d11 d12 d13a d13b d14; do sudo bash deploy/docker/verify-$t.sh || break; done
```

Puis `sudo bash deploy/setup-preview.sh`.

Fin : tous les `verify-d*.sh` passent au vert.

### Phase 5 : outils annexes (O). Requiert la phase 3.

1. `sudo bash deploy/install-playwright.sh`
2. `sudo bash deploy/install-kokoro.sh` (si les modèles n'ont pas été copiés en phase 3)
3. LiteLLM : recréer le venv, copier l'unit `litellm.service` depuis S, `systemctl enable --now litellm`.
4. 9Router : réinstaller selon l'inventaire de la phase 0, écoute `localhost:20128`.
5. Ollama : installer. Modèles : `rsync` de leur dossier depuis S (évite des Go de re-téléchargement) ou `ollama pull`.

Vérif : `curl localhost:20128/v1/models`, `curl localhost:11434/api/tags`, capture d'écran test faite par le script Playwright.

### Phase 6 : données à blanc (S vers O). Requiert les phases 3 et 4.

Première copie, sans arrêter la prod :

```bash
cd /var/www/forge
mongodump --db forge --gzip --archive=/tmp/forge-pre.gz
scp /tmp/forge-pre.gz <user>@<ip-ovh>:/tmp/
rsync -aH --delete /var/www/forge/workspace/ <user>@<ip-ovh>:/var/www/forge/workspace/
```

Sur O : `mongorestore --gzip --archive=/tmp/forge-pre.gz --drop`.

Fin : connexion à la Forge OVH par `/etc/hosts` local pointant sur l'IP OVH, conversations visibles.

### Phase 7 : Nginx et TLS (O). Requiert la phase 6.

```bash
cd /var/www/forge
sudo rsync -a root@<ip-source>:/etc/letsencrypt/ /etc/letsencrypt/   # ou tar via scp
sudo cp deploy/nginx-forge.conf /etc/nginx/sites-available/forge
sudo ln -s /etc/nginx/sites-available/forge /etc/nginx/sites-enabled/
sudo bash deploy/setup-preview-domain.sh
sudo nginx -t && sudo systemctl reload nginx
```

Ajouter les vhosts des autres projets recensés en phase 0.

Vérif : depuis le poste local avec `/etc/hosts` pointé sur O, `https://forge.quentin-astro.fr` répond et une preview `*.preview` s'ouvre.

### Phase 8 : autres projets (O). Requiert la phase 2.

Pour chaque projet de l'inventaire : code, `.env`, base, service ou conteneur, vhost, cron. Même schéma que les phases 3 à 7.

### Phase 9 : bascule (S puis O). Requiert les phases 4 à 8.

**Opérations dans l'ordre, sans en sauter :**

1. **J-2** : TTL des enregistrements DNS `forge`, `*.preview` et des autres projets à 300 s.
2. **S** : `sudo systemctl stop forge-backend` (gel des écritures).
3. **S** : `mongodump` final, puis `rsync` final du `workspace`.
4. **O** : `mongorestore --drop`, puis `cd /var/www/forge && sudo ./upgrade.sh --no-pull` (recrée `forge_preview`, vérifie les services).
5. DNS : enregistrements A/AAAA vers l'IP OVH.
6. **O** : `sudo tail -f /var/log/nginx/access.log` jusqu'à l'arrivée du trafic.

Rollback : remettre les DNS sur S, puis `sudo systemctl start forge-backend` sur S. Toute donnée écrite sur O entre-temps est perdue. **Ne rien supprimer sur S avant J+7.**

### Phase 10 : après la bascule (O)

1. Lancer `sudo ./upgrade.sh` à vide depuis `/var/www/forge` : la procédure habituelle fonctionne.
2. Le bouton **Enregistrer sur GitHub** fonctionne.
3. J+7 : arrêt et résiliation de la Dedibox.

## 4bis. Montée de versions : cibles

| Composant | Dedibox | OVH (cible) | Où agir |
|---|---|---|---|
| Python | version de la source | défaut 26.04 | `install.sh` crée le venv avec `python3` système |
| Deps backend | `requirements.txt` | dernières compatibles (`pip install --upgrade`), puis figer | `backend/requirements.txt` |
| Node (hôte) | version de la source | dernière LTS | phase 2 |
| Deps front | `package.json` | `npx npm-check-updates` puis `npm install`, build validé | `frontend/package.json` |
| Node (previews) | image `preview-base` | dernière version (`PREVIEW_NODE_VERSION`, défaut 26) | `deploy/docker/build-d3.sh` |
| Docker | docker-ce source | dernière docker-ce | `install-d1.sh` |
| MongoDB | version de la source | dernière stable | phase 2 + 2bis |
| Nginx / certbot | paquets source | paquets 26.04 | phase 2 |
| LiteLLM | venv source | dernière (`pip install -U 'litellm[proxy]'`) | phase 5 |
| Ollama / modèles | source | dernière Ollama, modèles re-pull si format changé | phase 5 |
| Playwright | source | dernière + navigateurs | `deploy/install-playwright.sh` |

Règle : monter les versions **avant** la bascule (phase 2bis), jamais après. Chaque correction de compatibilité passe par un commit du dépôt.

## 5. Ce que le nouveau matériel ouvre (après migration, pas pendant)

| Capacité | Usage concret | Priorité |
|---|---|---|
| 8 threads, 32 Go | `OLLAMA_NUM_THREAD` de 2 à 4 (cœurs physiques), modèles locaux 7B/8B quantisés utilisables | haute |
| 32 Go | `--memory` des conteneurs preview relevé, plusieurs previews en parallèle | haute |
| AVX2 | builds récents d'onnxruntime (Kokoro), numpy et Ollama/llama.cpp exploitent AVX2 : installer leurs dernières versions (pip `--upgrade`, Ollama à jour) | haute |
| AES-NI | TLS et HTTP/3 moins coûteux : dernière Nginx + OpenSSL de 26.04, HTTP/3 déjà déclaré dans les confs | haute |
| VT-x / VT-d | isolation renforcée des previews (gVisor `runsc` ou Kata) au lieu du simple runc. Chantier à part | moyenne |
| Quick Sync (iGPU P630) | transcodage vidéo `ffmpeg -hwaccel qsv` si un projet en a besoin. Possible seulement si OVH expose `/dev/dri` | basse |
| TSX-NI, Turbo, SpeedStep | gérés par le noyau HWE de 26.04 (à garder à jour via Ubuntu Pro / livepatch) | — |
| SGX, TXT | aucun usage pour la Forge | — |
| MPX | retiré du noyau Linux et de GCC. Inutilisable | — |

## 6. Non vérifiable depuis le dépôt

- Méthode d'installation de MongoDB, 9Router, Ollama et LiteLLM sur la Dedibox (phase 0).
- Liste et nature des autres projets hébergés.
- Exposition de l'iGPU (`/dev/dri`).
- URL du dépôt git et mode d'authentification du bouton GitHub sur le serveur.
