# Bug connu (corrigé) — PREVIEW en 503 : map nginx non écrite à cause de `ProtectSystem=strict`

*Claude Unchained Forge — GPLv3. Diagnostic d'origine : Quentin Dumont (branche
`claude-ai`). Ce fichier est la copie canonique conservée dans le dépôt : il ne
doit pas être supprimé, la logique de génération de l'unité systemd s'y réfère.*

## Symptôme

- Le bouton **PREVIEW** renvoie systématiquement **503** pour tous les projets.
- Aucune erreur explicite côté API : l'échec est silencieux.
- Dans les cas extrêmes, le déploiement de la Forge elle-même casse (nginx non
  rechargé / configuration partiellement écrite).

## Cause racine

`forge-backend.service` tourne avec `ProtectSystem=strict` (bonne pratique).
Conséquence : **tout le système de fichiers est monté en lecture seule pour le
service et pour tous ses process enfants** — y compris ceux lancés via `sudo`,
car le namespace mount est hérité. Seuls les chemins listés dans
`ReadWritePaths=` restent inscriptibles.

La liste d'origine ne contenait que :

```
ReadWritePaths=/var/www/forge/workspace /var/www/forge/.playwright /var/www/forge/backend/static/screenshots /dev/shm
```

Or la régénération de la map des previews
(`deploy/setup-preview-domain.sh --map-only`, déclenchée automatiquement par le
backend à chaque création de projet / attribution de port) a besoin de :

| Chemin | Pourquoi |
|---|---|
| `/etc/nginx` | écriture de `/etc/nginx/forge-preview-ports.map` (et du vhost) |
| `/var/log/nginx` | `nginx -t` ouvre les fichiers de log déclarés |
| `/run` | fichier PID / socket de contrôle lors du reload nginx |

Sans ces trois chemins, l'écriture échoue (`Read-only file system`), la map
reste vide, aucun projet n'y figure → le vhost renvoie **503** (projet inconnu
de la map), ce qui donnait l'impression que la preview « ne marchait jamais ».

## Correctif (appliqué)

```
ReadWritePaths=/var/www/forge/workspace /var/www/forge/.playwright /var/www/forge/backend/static/screenshots /dev/shm /etc/nginx /run /var/log/nginx
```

Intégré de façon permanente à trois endroits, pour qu'aucune régénération
future ne réintroduise une liste incomplète :

1. **`deploy/forge-backend.service`** — gabarit du dépôt, avec un commentaire
   explicite « ne rien retirer ».
2. **`deploy/ensure-rwpaths.sh`** — source unique de vérité. Patch **idempotent
   et additif** : il complète la liste de l'unité installée sans jamais
   supprimer un chemin ajouté localement. Appelé par `install.sh`,
   `upgrade.sh` et `deploy/fix-previews.sh`.
3. **`deploy/setup-preview.sh`** — l'unité de l'instance de preview parallèle
   (`forge-backend-preview`) utilise la même liste.

## Vérification

```bash
systemctl cat forge-backend | grep ReadWritePaths
sudo bash deploy/ensure-rwpaths.sh --print /var/www/forge   # liste attendue
sudo /usr/bin/bash /var/www/forge/deploy/setup-preview-domain.sh --map-only
cat /etc/nginx/forge-preview-ports.map                      # doit lister les projets
```

En cas de doute sur une installation existante :

```bash
sudo bash deploy/fix-previews.sh    # complète l'unité, régénère la map, recharge nginx
```

## Rappel : 503 ≠ 502

- **503** = le projet n'est pas dans la map nginx (map vide / non régénérée) →
  problème de droits d'écriture ou de map, c'est le bug décrit ici.
- **502** = le projet est dans la map, mais rien n'écoute sur son port → la
  preview n'est pas démarrée (bouton PREVIEW → Démarrer, puis consulter le
  journal `workspace/.forge-preview/<projet>.log`).
