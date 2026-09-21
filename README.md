# Claude Unchained Forge

Application de chat auto-hébergée : FastAPI + MongoDB côté backend, React
(Vite) côté frontend. Authentification JWT, conversations persistées, upload
d'image.

Déployée sur : https://forge.quentin-astro.fr

## Structure

```
backend/    API FastAPI (server.py), tests pytest
frontend/   Interface React + Vite
deploy/     Gabarits Nginx et systemd (à COMPARER avant d'appliquer, cf. plus bas)
memory/     Notes de conception (PRD)
install.sh  Installation / mise à jour automatisée
```

## Installation / mise à jour sur la Dedibox

```bash
cd /var/www/forge
git pull origin claude-ai
./install.sh
```

Le script :
- crée/actualise le venv Python et installe `requirements.txt`
- **ne touche jamais un `backend/.env` existant** (s'il est absent, il est
  créé depuis `env.example` et le script s'arrête pour vous laisser le
  compléter — JWT_SECRET, MONGO_URL, FRONTEND_URL)
- installe les dépendances frontend (`npm install`) et lance le build Vite
- ne remplace le service systemd qu'après confirmation, et seulement s'il
  diffère du gabarit versionné
- redémarre le backend (avec confirmation)
- **ne touche jamais à Nginx** — voir la section dédiée ci-dessous

## Nginx

`deploy/nginx-forge.conf` est un gabarit de référence, pas la vérité absolue :
la config réellement en place a été adaptée à la main lors de l'installation
initiale (le certificat TLS n'existait pas encore, `nginx -t` échouait sur le
gabarit brut). Avant d'appliquer quoi que ce soit :

```bash
diff deploy/nginx-forge.conf /etc/nginx/sites-available/forge
```

N'écrasez le fichier en place que si le diff est explicitement voulu, puis :
```bash
sudo nginx -t && sudo systemctl reload nginx
```

## Démarrage en local (développement)

**Backend**
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp env.example .env   # renseigner JWT_SECRET, MONGO_URL, FRONTEND_URL
uvicorn server:app --reload --port 8001
```

**Frontend**
```bash
cd frontend
npm install
cp .env.example .env   # valeurs par défaut OK si l'API tourne sur :8001
npm run dev             # proxy /api -> :8001, ouvert sur :3000
```

## Réinitialiser le mot de passe d'un compte

Le compte admin n'est créé qu'au tout premier démarrage (voir `ADMIN_EMAIL` /
`ADMIN_PASSWORD` dans `.env`). Pour changer un mot de passe existant sans
toucher au `.env` :

```bash
cd backend
venv/bin/python reset_password.py admin@forge.dev
```

## 📄 Licence

Ce projet est distribué sous licence **GNU General Public License v3 (GPLv3)**.

Copyright (C) 2026 Quentin Dumont

Vous êtes libre de l'utiliser, de l'étudier, de le modifier et de le redistribuer,
y compris modifié, à condition que toute redistribution reste sous la même licence
GPLv3 et conserve le code source disponible.

Ce programme est fourni SANS AUCUNE GARANTIE. Voir le fichier [LICENSE](./LICENSE)
pour le texte intégral officiel de la licence, ou <https://www.gnu.org/licenses/gpl-3.0.html>.
