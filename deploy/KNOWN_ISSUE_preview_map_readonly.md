# Bug connu : refresh de la map preview Nginx échoue silencieusement (Read-only file system)

## Symptôme
Un nouveau projet créé dans la Forge (ou une conversation liée après coup à un
projet existant) reste bloqué en **503** sur son URL de preview
(`https://<projet>.preview.<domaine>`), alors que :
- le process du projet tourne bien et répond en local (`curl 127.0.0.1:<port>`),
- l'entrée existe bien côté Mongo (`projects.preview_port`).

Le endpoint backend `/api/workspace/preview-map/refresh` répond HTTP 200,
mais dans les logs du service `forge-backend`, on trouve :

```
OSError: [Errno 30] Read-only file system: '/etc/nginx/forge-preview-ports.map'
```

## Cause racine
Le service systemd `forge-backend.service` est durci avec
`ProtectSystem=strict`, qui isole le process dans son propre mount namespace :
**tout le système de fichiers hôte y est monté en lecture seule**, sauf les
chemins listés explicitement dans `ReadWritePaths=`.

Le script `deploy/setup-preview-domain.sh --map-only` est appelé par le
backend (en tant que root, via sudoers dédié) pour régénérer
`/etc/nginx/forge-preview-ports.map` et recharger Nginx (`nginx -t &&
systemctl reload nginx`). Or ces opérations touchent :
- `/etc/nginx/forge-preview-ports.map` (écriture de la map),
- `/run/nginx.pid` (lu par `nginx -t`/`nginx -s reload`),
- `/var/log/nginx/*.log` (ouvert par `nginx -t` pour valider la conf),

**aucun de ces chemins n'était listé dans `ReadWritePaths`** avant ce fix,
donc même en `sudo`/root, le process héritait du mount namespace restreint du
service parent et se prenait un `EROFS` sur chacun de ces fichiers — de façon
silencieuse côté API (HTTP 200 quand même, car la tâche est planifiée en
arrière-plan et son échec n'est pas remonté au client).

## Fix appliqué
Ajout de trois chemins à `ReadWritePaths=` dans
`deploy/forge-backend.service` :

```ini
ReadWritePaths=/var/www/forge /etc/nginx /run /var/log/nginx
```

(la valeur exacte dépend de la forme du `ReadWritePaths` existant — voir
diff du commit associé.)

Après modification du `.service`, il faut impérativement :
```bash
sudo systemctl daemon-reload
sudo systemctl restart forge-backend
```
Un simple `systemctl reload` ne suffit pas : `ReadWritePaths` fait partie de
la config du mount namespace, fixée au démarrage du process.

## Pourquoi il n'y avait aucune erreur visible côté utilisateur
1. `/api/workspace/preview-map/refresh` planifie une tâche async et répond
   200 immédiatement, sans attendre/remonter le résultat réel.
2. L'erreur `OSError` n'apparaissait que dans les logs `journalctl -u
   forge-backend`, jamais dans une réponse HTTP ni un toast frontend.
3. Aucune alerte/health-check ne compare le contenu de la map Nginx avec la
   collection `projects` en base pour détecter une dérive.

## Recommandations pour Emergent (corrections à faire dans le code, pas juste
l'infra)
1. **`/api/workspace/preview-map/refresh` doit attendre le résultat réel**
   du script (ou au minimum logguer/exposer un statut interrogeable), pas
   répondre 200 avant même l'exécution.
2. **Le endpoint doit remonter une erreur explicite** si `setup-preview-domain.sh
   --map-only` échoue (stdout/stderr du script), au lieu d'avaler
   l'exception silencieusement côté tâche de fond.
3. **Le hook de refresh doit être déclenché automatiquement** dès qu'un lien
   `conversation.project` est créé/modifié via l'API officielle — et pas
   uniquement à la création initiale du projet. Le bug initial de ce ticket
   venait d'un lien conversation↔projet fait par contournement (écriture
   directe Mongo) qui n'a jamais déclenché ce hook : il faudrait un watcher
   ou un endpoint dédié `link_conversation_to_project` qui déclenche
   toujours le refresh, plutôt que de compter sur le flux de création
   normal uniquement.
4. **Le fichier `deploy/forge-backend.service` versionné dans le repo était
   dé-synchronisé** de la version réellement déployée en prod
   (`/etc/systemd/system/forge-backend.service`) — pas de process qui
   garantisse que le fichier déployé == fichier du repo. Envisager un script
   `deploy/install.sh` qui copie/diffe systématiquement ce fichier plutôt que
   de le corriger à la main sur le serveur.
