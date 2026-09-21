# Déploiement des nouveautés sur la Dedibox

Cible : Ubuntu 26.04 LTS · app dans `/var/www/forge` · service `forge-backend`
· Nginx `forge.quentin-astro.fr` · branche `claude-ai`.

Toutes les commandes sont copiables telles quelles, dans l'ordre.

---

## 0. Sauvegarde rapide (2 min, à ne pas sauter)

```bash
cd /var/www/forge

# .env backend (jamais dans git, c'est le seul fichier irremplaçable)
cp backend/.env ~/forge-env-$(date +%F-%H%M).bak

# dump MongoDB
mongodump --db forge --out ~/forge-mongo-$(date +%F-%H%M)

# état git actuel (pour pouvoir revenir)
git rev-parse --short HEAD
```

---

## 1. Récupérer le code

```bash
cd /var/www/forge
git status                      # doit être propre ; sinon : git stash
git fetch origin
git checkout claude-ai
git pull --ff-only origin claude-ai
git log --oneline -8            # vérifier qu'on a bien LICENSE / NOTICE / install-playwright.sh
```

Si `git pull` refuse à cause de fichiers locaux modifiés :

```bash
git stash push -m "prod-local"  # puis, après le pull : git stash pop
```

---

## 2. Mise à jour backend + frontend (script rejouable)

```bash
cd /var/www/forge
./install.sh
```

Ce que fait `install.sh` : venv, `pip install -r backend/requirements.txt`,
`npm install`, `npm run build` (sortie `frontend/build/`), propose la mise à
jour du service systemd (**réponds `y`** : le fichier contient maintenant
`PLAYWRIGHT_BROWSERS_PATH`), puis redémarre `forge-backend`.

### Si tu préfères le faire à la main

```bash
cd /var/www/forge/backend
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

cd /var/www/forge/frontend
npm install
npm run build

sudo cp /var/www/forge/deploy/forge-backend.service /etc/systemd/system/forge-backend.service
sudo systemctl daemon-reload
sudo systemctl restart forge-backend
```

---

## 3. Chromium headless pour l'outil de capture d'écran

```bash
cd /var/www/forge
sudo bash deploy/install-playwright.sh
```

Le script installe les dépendances système Ubuntu 26.04 (paquets `t64`),
le module `playwright` dans le venv, télécharge Chromium dans
`/var/www/forge/.playwright`, donne les droits au user du service, puis
vérifie un lancement headless réel.

Variables surchargeables si ton arborescence diffère :

```bash
sudo APP_DIR=/var/www/forge \
     VENV_DIR=/var/www/forge/backend/venv \
     PLAYWRIGHT_BROWSERS_PATH=/var/www/forge/.playwright \
     SERVICE_USER=quentin \
     bash deploy/install-playwright.sh
```

> ⚠️ Le service tourne sous `User=quentin` : passe bien `SERVICE_USER=quentin`
> (le défaut du script est `www-data`), sinon le cache navigateur ne sera pas
> lisible par le backend.

---

## 4. Compléter le `.env` backend

```bash
sudo nano /var/www/forge/backend/.env
```

Variables **nouvelles** à ajouter / vérifier :

```ini
# --- Captures d'écran -------------------------------------------------------
ENABLE_SCREENSHOT=true
SCREENSHOT_RETENTION_HOURS=48
PLAYWRIGHT_BROWSERS_PATH=/var/www/forge/.playwright

# --- Dictée vocale (repli serveur ; le navigateur gère le cas nominal) ------
STT_MODEL=whisper-large-v3-turbo
STT_MAX_MB=20

# --- Providers gratuits OpenAI-compatibles (laisse vide ceux que tu n'as pas)
GROQ_API_KEY=
GROQ_BASE_URL=https://api.groq.com/openai/v1
CEREBRAS_API_KEY=
CEREBRAS_BASE_URL=https://api.cerebras.ai/v1
SAMBANOVA_API_KEY=
SAMBANOVA_BASE_URL=https://api.sambanova.ai/v1
NVIDIA_API_KEY=
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_FREE_ONLY=true
OPENROUTER_HTTP_REFERER=https://forge.quentin-astro.fr
OPENROUTER_TITLE=Claude Unchained Forge

# --- Catalogue de modèles + résumé d'historique -----------------------------
MODEL_CATALOG_TTL=3600
HISTORY_SUMMARY_ENABLED=true
HISTORY_SUMMARY_THRESHOLD_TOKENS=6000
HISTORY_SUMMARY_KEEP_RECENT=6
HISTORY_SUMMARY_MAX_CHARS=3000

# --- Ordre de bascule -------------------------------------------------------
PROVIDER_PRIORITY=claude,gemini,ollama_cloud,opencode,ollama
ENABLE_FALLBACK=true
```

Référence complète (toutes les clés commentées) :

```bash
diff <(grep -o '^[A-Z_]*' /var/www/forge/backend/env.example | sort -u) \
     <(grep -o '^[A-Z_]*' /var/www/forge/backend/.env | sort -u)
```

Puis :

```bash
sudo chmod 600 /var/www/forge/backend/.env
sudo systemctl restart forge-backend
```

Où obtenir les clés gratuites : Groq → console.groq.com/keys ·
Cerebras → cloud.cerebras.ai · SambaNova → cloud.sambanova.ai ·
NVIDIA NIM → build.nvidia.com · OpenRouter → openrouter.ai/keys.

---

## 5. Nginx (uniquement si la config a changé)

```bash
diff /var/www/forge/deploy/nginx-forge.conf /etc/nginx/sites-available/forge
```

Si le diff est voulu :

```bash
sudo cp /var/www/forge/deploy/nginx-forge.conf /etc/nginx/sites-available/forge
sudo nginx -t && sudo systemctl reload nginx
```

Pour que la PWA se mette bien à jour (le service worker ne doit jamais être
mis en cache), vérifie ce bloc dans le `server` HTTPS :

```nginx
    location = /sw.js {
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        expires off;
    }
    location = /manifest.webmanifest {
        add_header Cache-Control "no-cache";
        default_type application/manifest+json;
    }
```

---

## 6. Vérifications

```bash
# service
sudo systemctl status forge-backend --no-pager
sudo journalctl -u forge-backend -n 50 --no-pager

# API en local
curl -s http://127.0.0.1:8001/api/ | head -c 200; echo

# API via Nginx + login
curl -s -X POST https://forge.quentin-astro.fr/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@forge.dev","password":"TON_MOT_DE_PASSE"}' | head -c 300; echo

# modèles découverts dynamiquement (montre quels providers sont actifs)
TOKEN=$(curl -s -X POST https://forge.quentin-astro.fr/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@forge.dev","password":"TON_MOT_DE_PASSE"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s https://forge.quentin-astro.fr/api/models -H "Authorization: Bearer $TOKEN" | python3 -m json.tool | head -40

# Chromium headless vu par le backend
sudo -u quentin env PLAYWRIGHT_BROWSERS_PATH=/var/www/forge/.playwright \
  /var/www/forge/backend/venv/bin/python -c \
  "from playwright.sync_api import sync_playwright;\
p=sync_playwright().start();b=p.chromium.launch(args=['--no-sandbox']);print('chromium OK');b.close();p.stop()"
```

Dans l'interface (https://forge.quentin-astro.fr) :

1. Vider le cache PWA : `Ctrl+Shift+R`, ou Application → Service Workers →
   Unregister si l'ancienne version persiste.
2. Test capture : « Capture https://example.com et affiche l'image dans ta réponse. »
3. Test recherche : « Cherche sur le web la capitale de la Mongolie. »
4. Test dictée : clic sur le micro dans la barre de saisie.
5. Pied de page de la sidebar : badge **GPLv3** + lien **Code source**.

---

## 7. Réinitialiser le mot de passe admin (si besoin)

```bash
cd /var/www/forge/backend
venv/bin/python reset_password.py admin@forge.dev
```

---

## 8. Retour arrière

```bash
cd /var/www/forge
git log --oneline -10
git checkout <sha-precedent>
cd frontend && npm run build
sudo systemctl restart forge-backend
cp ~/forge-env-AAAA-MM-JJ-HHMM.bak backend/.env   # si le .env a été touché
```

---

## Résumé express (si tout est déjà configuré)

```bash
cd /var/www/forge \
  && cp backend/.env ~/forge-env-$(date +%F).bak \
  && git pull --ff-only origin claude-ai \
  && ./install.sh \
  && sudo SERVICE_USER=quentin bash deploy/install-playwright.sh \
  && sudo systemctl restart forge-backend \
  && sudo systemctl status forge-backend --no-pager | head -5
```
