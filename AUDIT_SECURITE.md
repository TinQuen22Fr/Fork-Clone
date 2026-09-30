# 🔒 Audit de sécurité défensif — Fork-Clone

## Synthèse
Le code est globalement soigné (CORS strict, cookies `HttpOnly`, JWT avec expiration, masquage des secrets, anti-zip-slip, contrôle de propriété sur les données). Les principaux risques viennent de **l'exécution de code non sandboxée** (fonction même de la plateforme), de **l'outil agentique `bash`/`fetch_url`**, de **l'absence de rate limiting** et d'un **contrôle d'accès multi-utilisateurs incomplet au niveau du système de fichiers**.

---

## 🔴 Risques CRITIQUES

### C1. Exécution de code arbitraire via les previews (RCE par design)
- **Fichier** : `backend/preview_runtime.py` + `server.py` (`/workspace/projects/*/preview/start`)
- **Constats** : `PreviewManager` exécute `npm install`, `uvicorn`, `python`, `node`, `http.server` sur du code importé par ZIP ou poussé via Git, **sans sandbox ni conteneur** (`subprocess.Popen` avec `env` modifié mais même machine). Le processus a accès au reste du système selon les permissions du service.
- **Correctif** : exécuter chaque preview dans un conteneur/namespace isolé (Docker/Podman, `systemd` `PrivateTmp`, user dédié), restreindre les permissions système, limiter le réseau et les ressources (CPU/RAM/port).

### C2. SSRF via l'outil `fetch_url`
- **Fichier** : `backend/server.py`, `_tool_fetch_url()` (l. 1110+)
- **Constats** : `httpx.get(url, follow_redirects=True)` accepte **toute URL http/https sans blocage des adresses internes/privées** : `http://localhost:*`, `http://169.254.169.254/latest/meta-data/…` (métadonnées cloud), `127.0.0.1`, réseaux RFC1918. Le modèle peut être incité à les lire, et `redact_secrets` ne masque pas les métadonnées cloud.
- **Correctif** : résoudre le DNS et rejeter les IP privées/lien-local/loopback (`ipaddress`), interdire les schémas non-http(s), limiter les ports, désactiver les redirections vers du privé.

---

## 🟠 Risques ÉLEVÉS

### E1. Outil `bash` = exécution de commandes arbitraires
- **Fichier** : `backend/server.py`, `_tool_bash()` (l. 928)
- **Constats** : `subprocess.run(command, shell=True, …)`. Le cloisonnement au projet (`cwd=project_root()`) est réel, mais `shell=True` + absence de validation = exécution arbitraire si le modèle est détourné. Le prompt « interdit » de lire les secrets, mais c'est une barrière **non technique** contournable par injection de prompt.
- **Correctifs** : bannir `shell=True` (découper la commande), ajouter une liste d'autorisation de binaires, bloquer explicitement `cat/grep/env/printenv/curl` sur les fichiers de secrets au niveau **code** (pas seulement prompt), journaliser toutes les commandes.

### E2. Contrôle d'accès multi-utilisateurs absent au niveau du système de fichiers
- **Fichier** : `backend/server.py` (`resolve_project_dir`, `_ensure_project_dir`, `list_workspace_projects`)
- **Constats** : les métadonnées (`projects`) sont par `user_id`, **mais** les dossiers `WORKSPACE_ROOT/{name}` sont **partagés**. `resolve_project_dir` accepte tout nom de dossier existant sans vérifier la propriété ; `list_workspace_projects()` liste tous les dossiers pour tous les utilisateurs. Un utilisateur A peut lire/écrire le projet de B via `/chat/send` (project lié) ou `/workspace/projects`.
- **Correctif** : associer une propriété au dossier (fichier `.owner` ou collection Mongo) et vérifier `user_id` avant tout accès disque ; sinon restreindre l'instance à un seul utilisateur.

### E3. Pas de rate limiting sur l'authentification
- **Fichier** : `backend/server.py`, `POST /auth/login` et `/auth/register`
- **Constats** : aucune limitation de débit ni verrouillage après échecs → brute-force des mots de passe possible.
- **Correctif** : `slowapi` (ou middleware) avec fenêtre glissante (ex. 5 tentatives/min/IP + compte), délai progressif, journalisation.

### E4. `GITHUB_PAT` / jetons stockés en clair
- **Fichier** : `backend/server.py`, `POST /github/token` (l. 5400)
- **Constats** : le jeton est persisté **en clair dans MongoDB** (`settings`). Le token est aussi injecté dans l'URL `https://x-access-token:{token}@github.com/…` passé en argument `git push` (l. 5507) → visible dans `ps`/journaux pendant l'exécution (la redaction n'est appliquée qu'aux réponses d'erreur).
- **Correctifs** : chiffrer au repos (Fernet/KMS), utiliser `git` avec `GIT_ASKPASS` ou un credential helper éphémère au lieu de l'URL, purger les journaux, ne jamais retourner le token.

---

## 🟡 Risques MOYENS

### M1. Import ZIP sans limite de taille (DoS mémoire)
- **`POST /workspace/projects/import`** : `await file.read()` charge tout le fichier en RAM, **aucun plafond** de taille (contrairement aux pièces jointes du chat). Correctif : limite type `MAX_IMPORT_MB` + lecture par flux.

### M2. `/workspace/preview-map/refresh` accessible à tout utilisateur authentifié
- Déclenche la régénération de la map Nginx (commande système configurée). **Aucun contrôle de rôle** (le champ `role: "admin"` n'est jamais vérifié). Correctif : restreindre aux admins ou désactiver en multi-utilisateur.

### M3. La commande `PREVIEW_MAP_REFRESH_CMD` est exécutée par le service
- `asyncio.create_subprocess_exec(*shlex.split(cmd))` (l. 4701) : pas de `shell=True` (bien), mais si cette variable est modifiable par un utilisateur (`.env` partagé), c'est une exécution de commande. Correctif : commande fixe en lecture seule, exécution via un sous-processus non-privilégié.

### M4. Token JWT en `localStorage` côté frontend
- `frontend/src/lib/api.js` et `AuthContext.jsx` stockent le token dans `localStorage` (fallback Bearer). Exposé à tout XSS. Le cookie `HttpOnly` est posé, mais le fallback affaiblit la posture. Correctif : privilégier uniquement le cookie `HttpOnly`, supprimer le fallback `localStorage` ou le limiter.

### M5. Barrière anti-secrets « soft » sur l'outil `bash`
- `_tool_read_file` bloque bien les fichiers de secrets, **mais** `_tool_bash` peut lire `.env` via `cat/grep`. La protection repose sur le prompt système uniquement. Correctif : interception au niveau de `_tool_bash` (blacklist de motifs dans la commande).

---

## 🟢 Risques FAIBLES

- **F1. Erreurs AI renvoyées avec détails internes** : `detail=f"AI error: {e}"` peut fuiter des messages d'erreur internes. → logger côté serveur, renvoyer un message générique.
- **F2. `rate`/`pitch` TTS non validés** : `_synth_edge` passe `rate`/`pitch` bruts à `edge_tts` (la voix est validée par regex, pas ces champs). Pas d'injection shell, mais erreurs possibles. → valider `^[+-]?\d+%$`.
- **F3. Fuite d'infra via `/workspace/preview-map`** : expose le chemin `/etc/nginx/forge-preview-ports.map`. → restreindre ou masquer.
- **F4. Rôle `admin` sans effet** : le seed admin (`role: "admin"`) n'est utilisé par aucune autorisation. → soit implémenter la vérification de rôle, soit supprimer le concept.
- **F5. Regex de détection de secrets légèrement imprécise** : `gh[pousr]_` (l. 1211) est un motif inhabituel ; la redaction est néanmoins renforcée par `SECRET_ASSIGN`. → corriger en `gh[prous]_` si l'intention est `ghp_/gho_/ghu_/ghs_/ghr_`.

---

## ✅ Points déjà bien protégés
- **CORS** : origines explicites, jamais `*` avec `credentials`, headers limités (l. 5853+).
- **Cookies** : `HttpOnly`, `secure`, `SameSite` validé (`none` impose `secure`).
- **JWT** : `HS256`, expiration, secret requis (clé éphémère + alerte si absent), vérification `type=access`.
- **Autorisation sur les données** : chaque endpoint filtre par `user_id` (pas d'IDOR évident sur conversations/messages/projets).
- **Path traversal** : `_valid_project_name` (regex stricte) + `resolve().relative_to(root.resolve())` sur export/delete.
- **Zip-slip** : rejet des chemins absolus et `..` dans les archives (l. 4906).
- **Injection SQL/NoSQL** : MongoDB + requêtes paramétrées par dict ; aucun `$where` ni concaténation de filtre.
- **XSS** : `ReactMarkdown` **sans `rehype-raw`** → HTML brut échappé ; aucun `dangerouslySetInnerHTML`/`innerHTML` dans le frontend.
- **Masquage des secrets** : `redact_secrets` appliqué aux réponses IA et sorties d'outils ; `_tool_read_file` refuse `.env`, `.key`, `.pem`, `id_rsa`, etc.
- **Limites d'upload chat** : tailles, nombre de pièces jointes, budgets de caractères bien bornés.

---

## Priorisation recommandée
1. **Sandboxer les previews** (C1) et **bloquer le SSRF** (C2).
2. **Rate limiting auth** (E3) + **contrôle de propriété des dossiers** (E2).
3. **Durcir l'outil bash** (E1) + **chiffrer les jetons GitHub** (E4).
4. Traiter les risques moyens (M1–M5) puis faibles.
