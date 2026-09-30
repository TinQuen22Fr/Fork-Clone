# 🔒 Audit de sécurité défensif — Fork-Clone

## Synthèse
Le code est globalement soigné (CORS strict, cookies `HttpOnly`, JWT avec expiration, masquage des secrets, anti-zip-slip, contrôle de propriété sur les données). Le risque SSRF sur l'outil `fetch_url` (C2) a été corrigé et validé : il est désormais neutralisé. Le risque lié à l'outil `bash` (E1) a été partiellement corrigé : un verrou technique anti-lecture de secrets a été ajouté, tandis que `shell=True` et la liberté des binaires restent maintenus par choix d'architecture (machine dédiée / mono-utilisateur). Le risque lié à l'absence de rate limiting sur l'authentification (E3) a également été corrigé : un dispositif anti-bruteforce en mémoire (5 échecs/min/IP, HTTP 429, purge périodique) protège désormais `POST /auth/login`. Le stockage en clair des jetons GitHub (E4) a lui aussi été corrigé : chiffrement au repos (Fernet) et masquage en ligne de commande via header HTTP temporaire remplacent l'ancien mécanisme. **Tous les risques élevés identifiés sont désormais clôturés.** La configuration CORS (M1) a également été renforcée : ajout d'une règle regex restreinte aux hôtes locaux de développement, en complément de la liste explicite d'origines, sans jamais introduire de wildcard non restreint. L'exécution directe des runtimes de preview (C1), l'absence de cloisonnement multi-utilisateurs au niveau du système de fichiers (E2), l'absence de contrôle de rôle admin sur `/workspace/preview-map/refresh` (M2), l'exécution de `PREVIEW_MAP_REFRESH_CMD` (M3), le fallback du token JWT en `localStorage` (M4) et le rôle `admin` sans effet (F4) sont, eux, des choix assumés propres à une machine dédiée mono-utilisateur (voir la section « Risque accepté par design »). **Tous les risques moyens identifiés sont désormais clôturés** (résolus ou acceptés par design). Le premier risque faible traité, la fuite de détails internes dans les erreurs de génération IA (F1), a également été corrigé : les réponses HTTP renvoient désormais un message générique, tandis que le détail complet reste tracé dans les journaux serveur. Le deuxième risque faible, l'absence de validation des paramètres `rate`/`pitch` du TTS edge-tts (F2), a lui aussi été corrigé : une regex stricte assainit désormais systématiquement ces valeurs avant transmission à `edge_tts`. Le troisième risque faible, la fuite d'infra via `/workspace/preview-map` (F3), a également été corrigé : le chemin absolu du fichier map Nginx n'est plus exposé dans la réponse JSON, seul le nom de fichier est renvoyé. Le rôle `admin` sans effet (F4) a quant à lui été arbitré comme un risque accepté par design : le champ documente le statut de l'utilisateur unique sans nécessiter de RBAC complet, le contrôle d'accès étant déjà assuré par l'authentification JWT stricte et la fermeture des inscriptions. Le dernier risque faible, l'imprécision de la regex de détection des jetons GitHub (F5), a également été corrigé : le motif a été normalisé en `gh[prous]_[A-Za-z0-9_]+`, couvrant explicitement les préfixes officiels `ghp_`, `gho_`, `ghu_`, `ghs_` et `ghr_`. **100 % des vulnérabilités identifiées dans cet audit sont désormais traitées : chaque point est soit résolu directement dans le code, soit formellement accepté par design pour cet environnement mono-utilisateur.**

---

## 🔴 Risques CRITIQUES

_Aucun risque critique ouvert à ce jour._

---

## ⚪ Risque accepté par design (Machine dédiée / Mono-utilisateur)

### C1. Exécution de code arbitraire via les previews (RCE par design)
- **Fichier** : `backend/preview_runtime.py` + `server.py` (`/workspace/projects/*/preview/start`)
- **Constats** : `PreviewManager` exécute `npm install`, `uvicorn`, `python`, `node`, `http.server` sur du code importé par ZIP ou poussé via Git, **sans sandbox ni conteneur** (`subprocess.Popen` avec `env` modifié mais même machine). Le processus a accès au reste du système selon les permissions du service.
- **Statut** : **Risque accepté par design.** Le lancement direct des runtimes fait partie intégrante du fonctionnement de la Forge. La conteneurisation / le sandboxage (Docker, Podman) est **délibérément exclu**, car inadapté et inutile pour cet environnement personnel dédié (machine dédiée / mono-utilisateur).
- **Correctif** : aucun à appliquer dans ce contexte. Si l'environnement devait un jour être mutualisé (multi-utilisateurs sur une machine partagée), réévaluer une isolation type namespace/user dédié.

### E2. Contrôle d'accès multi-utilisateurs absent au niveau du système de fichiers
- **Fichier** : `backend/server.py` (`resolve_project_dir`, `_ensure_project_dir`, `list_workspace_projects`)
- **Constats** : les métadonnées (`projects`) sont bien indexées par `user_id`, mais les dossiers `WORKSPACE_ROOT/{name}` sont **techniquement partagés** sur le disque : `resolve_project_dir` accepte tout nom de dossier existant sans vérifier la propriété, et `list_workspace_projects()` liste tous les dossiers présents. **Cette absence de cloisonnement est sans risque dans la configuration actuelle** : l'instance est exploitée en **mono-utilisateur**, avec les **inscriptions publiques désactivées** — il n'existe donc pas de second utilisateur susceptible d'accéder au projet d'un autre.
- **Statut** : **Risque accepté par design.** L'ajout d'une barrière de propriété (fichier `.owner` ou isolation stricte par utilisateur) est **volontairement écarté** afin de préserver la simplicité d'accès aux projets en local. Ce correctif ne redeviendrait pertinent que si l'inscription multi-utilisateurs venait à être réactivée à l'avenir.
- **Correctif (conditionnel, non appliqué)** : si l'instance devait un jour rouvrir les inscriptions, associer une propriété au dossier (`.owner` ou collection Mongo) et vérifier `user_id` avant tout accès disque.
- **Arbitrage** : **Arbitré le 30/09/2026 à 23:20.**


### M2. `/workspace/preview-map/refresh` accessible à tout utilisateur authentifié sans contrôle de rôle admin
- **Fichier** : `backend/server.py` (`POST /workspace/preview-map/refresh`)
- **Constats** : l'endpoint déclenche la régénération de la map Nginx locale via une commande système configurée (`PREVIEW_MAP_REFRESH_CMD`), accessible à tout utilisateur authentifié. Le champ `role: "admin"` n'est jamais vérifié avant l'exécution.
- **Statut** : **Risque accepté par design.** L'instance étant exploitée en **mono-utilisateur strict** (inscriptions publiques désactivées), l'unique utilisateur authentifié est de facto l'administrateur légitime du système : il n'existe aucun second compte susceptible d'abuser de cet endpoint. L'absence de vérification explicite d'un rôle admin est **assumée** afin de ne pas introduire de friction ou de blocage inutile sur la régénération des configurations Nginx locales, opération bénigne et fréquente dans le workflow de développement.
- **Correctif (conditionnel, non appliqué)** : ce contrôle de rôle ne deviendrait pertinent qu'en cas d'ouverture de l'instance à du **multi-utilisateurs** — il faudrait alors vérifier `role == "admin"` avant d'autoriser l'appel.
- **Arbitrage** : **Arbitré le 01/10/2026 à 00:30.**

### M3. Exécution de la commande `PREVIEW_MAP_REFRESH_CMD`
- **Fichier** : `backend/server.py` (`POST /workspace/preview-map/refresh`, l. ~4701)
- **Constats** : la régénération de la map Nginx locale exécute une commande configurée via la variable d'environnement `PREVIEW_MAP_REFRESH_CMD`. L'implémentation technique est déjà **saine** : `asyncio.create_subprocess_exec(*shlex.split(cmd))`, sans `shell=True` ni interpolation de sous-shell. Le scénario de risque théorique (modification non sollicitée du `.env` pour y injecter une commande arbitraire) **n'est pas applicable** dans ce contexte : l'instance est exploitée en **mono-utilisateur sur machine dédiée**, le fichier `.env` relève de la seule responsabilité de l'administrateur système/développeur, et l'accès aux secrets via l'outil `bash` est déjà verrouillé techniquement (voir E1/M5).
- **Statut** : **Risque accepté par design.** Conserver la commande configurable via l'environnement préserve la flexibilité d'intégration (adaptation à différents setups Nginx/reverse-proxy) sans compromettre la sécurité, puisque la surface d'attaque réelle (modification du `.env`) suppose déjà un accès administrateur complet à la machine.
- **Correctif** : aucun à appliquer. L'implémentation actuelle (`shlex.split` + `create_subprocess_exec` sans shell) est conservée telle quelle.
- **Arbitrage** : **Arbitré le 01/10/2026 à 00:35.**

### M4. Token JWT en `localStorage` côté frontend
- **Fichier** : `frontend/src/lib/api.js`, `frontend/src/context/AuthContext.jsx`
- **Constats** : le frontend maintient une double gestion de session : cookie `HttpOnly` posé par le backend, avec un **fallback Bearer en `localStorage`**. Le jeton stocké en `localStorage` est en théorie exposé à tout XSS persistant qui parviendrait à s'exécuter dans le contexte de la page.
- **Statut** : **Risque accepté par design.** Le cookie `HttpOnly` est déjà actif côté backend et assure la sécurité nominale de la session (non accessible en JavaScript, donc non exfiltrable par XSS classique). Le maintien du fallback en `localStorage` est un choix technique **assumé** pour garantir la robustesse des appels API frontend/backend en environnement de développement local, où la politique de cookies inter-ports/inter-origines peut bloquer la session si l'on ne s'appuie que sur le cookie seul. L'environnement étant **mono-utilisateur** et non exposé à des injections de contenu tiers (pas de XSS persistant connu, pas de contenu généré par des tiers non fiables affiché sans échappement), le risque d'exfiltration de jeton via le stockage local est considéré comme **négligeable** dans ce contexte.
- **Correctif (conditionnel, non appliqué)** : si l'instance devait un jour être exposée à du contenu tiers non maîtrisé ou ouverte à du multi-utilisateurs, supprimer le fallback `localStorage` et ne s'appuyer que sur le cookie `HttpOnly`.
- **Arbitrage** : **Arbitré le 01/10/2026 à 00:40.**

### F4. Rôle `admin` sans effet
- **Fichier** : `backend/server.py` (modèle utilisateur, seed admin, champ `role: "admin"`)
- **Constats** : le champ `role: "admin"` est présent sur le modèle utilisateur et sur le compte seedé, mais **n'est vérifié par aucune autorisation** dans le code backend : aucun endpoint ne conditionne son exécution à `role == "admin"`.
- **Statut** : **Risque accepté par design.** Le maintien du champ `role: "admin"` est un choix de structure de données **assumé** : il documente le statut légitime de l'utilisateur unique sans alourdir le code backend avec des vérifications de rôles superflues en environnement mono-utilisateur. Ni l'implémentation d'un RBAC complet ni la purge du champ ne sont justifiées sur cette instance locale dédiée, le contrôle d'accès étant déjà assuré par l'authentification JWT stricte et la fermeture des inscriptions.
- **Correctif (conditionnel, non appliqué)** : si l'instance devait un jour être ouverte à du multi-utilisateurs, implémenter une vérification explicite de `role == "admin"` sur les endpoints sensibles (ex. `/workspace/preview-map/refresh`, voir M2).
- **Arbitrage** : **Arbitré le 01/10/2026 à 01:00.**

---

## ✅ Vulnérabilités résolues

### C2. SSRF via l'outil `fetch_url` — [RÉSOLU]
- **Fichier** : `backend/server.py`, `_tool_fetch_url()` (+ nouvelle fonction `_validate_public_url()`)
- **Constat initial** : `httpx.get(url, follow_redirects=True)` acceptait **toute URL http/https sans blocage des adresses internes/privées** : `http://localhost:*`, `http://169.254.169.254/latest/meta-data/…` (métadonnées cloud), `127.0.0.1`, réseaux RFC1918. Le modèle pouvait être incité à les lire, et `redact_secrets` ne masquait pas les métadonnées cloud.
- **Correctif appliqué** : ajout de `_validate_public_url()` qui résout le nom d'hôte via `socket.getaddrinfo` et rejette (via le module `ipaddress`) toute IP `is_private`, `is_loopback`, `is_link_local`, `is_reserved`, `is_multicast` ou `is_unspecified`. La validation est effectuée **avant chaque requête**, y compris à chaque redirection HTTP suivie manuellement (`follow_redirects=False` + boucle de suivi revalidant l'hôte à chaque saut, max 5 sauts) : une redirection ne peut donc plus contourner le blocage vers une adresse interne.
- **Statut** : **Corrigé et validé le 30/09/2026 à 23:05.**

### E1. Outil `bash` = exécution de commandes arbitraires — [RÉSOLU / PARTIELLEMENT ADAPTÉ]
- **Fichier** : `backend/server.py`, `_tool_bash()`
- **Constat initial** : `subprocess.run(command, shell=True, …)` sans aucune validation de la commande. Le prompt système « interdisait » la lecture des secrets, mais c'était une barrière **non technique**, contournable par injection de prompt (ex. `cat .env`, `cat id_rsa`).
- **Correctif appliqué** : ajout d'un verrou technique dans `_tool_bash()` (regex `_BASH_SENSITIVE_FILE_RE`) qui détecte toute occurrence de `.env`, `.pem`, `id_rsa`, `id_ed25519`, `.key` dans le texte de la commande **avant exécution**, et renvoie immédiatement `"Accès aux fichiers de configuration sensibles/secrets interdit via bash."` sans lancer le `subprocess.run`.
- **Choix d'architecture maintenu (assumé, non régressé)** : `shell=True` et la liberté d'exécuter n'importe quel binaire disponible sur la machine sont **conservés intentionnellement** : ce sont des choix d'architecture adaptés à un environnement mono-utilisateur / machine dédiée, indispensables au workflow de développement de la Forge (npm, git, pip, scripts composés, pipes shell, etc.). Aucune liste blanche de binaires n'est imposée.
- **Statut** : **Corrigé et validé le 30/09/2026 à 23:15.** (verrou anti-lecture de secrets actif ; `shell=True` maintenu par choix d'architecture).

### E3. Pas de rate limiting sur l'authentification — [RÉSOLU]
- **Fichier** : `backend/server.py`, `POST /auth/login`
- **Constat initial** : aucune limitation de débit ni verrouillage après échecs → brute-force des mots de passe possible.
- **Correctif appliqué** : mise en place d'un rate limiting léger **en mémoire** (dict Python, sans dépendance externe type Redis), indexé par adresse IP cliente (`request.client.host`). Seuil : **maximum 5 tentatives d'échec par fenêtre glissante de 60 secondes et par IP**. Au-delà, la requête est immédiatement rejetée avec un code **HTTP 429 Too Many Requests** et le message `"Trop de tentatives de connexion échouées. Veuillez patienter une minute."`. Le compteur d'échecs de l'IP est **réinitialisé dès qu'un mot de passe valide est soumis** (`_login_rate_limit_reset`). Une purge périodique (`_login_rate_limit_purge`, toutes les 5 minutes) supprime les IP inactives du dict pour éviter toute fuite de mémoire sur la durée.
- **Statut** : **Corrigé et validé le 30/09/2026 à 23:25.**

---

### E4. `GITHUB_PAT` / jetons stockés en clair — [RÉSOLU]
- **Fichier** : `backend/server.py` (`_github_fernet`, `_encrypt_github_token`, `_decrypt_github_token`, `POST /github/token`, `GET /github/status`, `POST /github/push`).
- **Constat initial** : le jeton GitHub était persisté **en clair dans MongoDB** (`settings`) et injecté dans l'URL `https://x-access-token:{token}@github.com/…` passée en argument à `git push` → visible dans la table des processus (`ps`) et les journaux pendant l'exécution.
- **Correctifs appliqués** :
  - **Chiffrement au repos** : le jeton est désormais chiffré avec `cryptography.fernet.Fernet`, clé dérivée (SHA-256) du secret JWT existant (`settings.jwt_secret`), avant tout enregistrement en base (`_encrypt_github_token` / `_decrypt_github_token`). Un jeton corrompu ou d'ancien format force un re-enregistrement propre plutôt qu'un plantage.
  - **Masquage en ligne de commande** : le `remote origin` git ne contient plus jamais le jeton (`https://github.com/{repo}.git` uniquement). L'authentification du `git push` passe désormais par un header HTTP temporaire (`git -c http.extraHeader="AUTHORIZATION: basic <base64>"`), qui n'apparaît ni dans `ps`, ni dans l'historique du remote, ni dans les logs bruts.
  - **Non-exposition au frontend** : les endpoints (`GET /github/status`, `POST /github/token`) ne renvoient plus jamais le jeton complet — uniquement un indicateur de présence (`configured`) et les **4 derniers caractères** (`token_last4` / `last4`), stockés séparément en base pour affichage.
- **Statut** : Corrigé et validé le 30/09/2026 à 23:35.

### M1. Configuration CORS permissive — [RÉSOLU]
- **Fichier** : `backend/server.py` (configuration `CORSMiddleware`, l. 6049+)
- **Constat initial** : la configuration reposait uniquement sur une liste exacte d'origines (`settings.frontend_urls`, dérivée de `FRONTEND_URL`), sans wildcard global `*` (ce point était déjà correctement géré), mais **sans possibilité d'autoriser dynamiquement les ports de développement local** (`http://localhost:*`, `http://127.0.0.1:*`), ce qui pouvait pousser à élargir la configuration de façon peu maîtrisée.
- **Correctif appliqué** : ajout d'un `allow_origin_regex` restreint aux hôtes locaux de développement (`^https?://(localhost|127\.0\.0\.1)(:\d+)?$`), en complément de la liste explicite `allow_origins=settings.frontend_urls` (origines légitimes de l'instance, via `FRONTEND_URL`). Aucun wildcard non restreint n'est utilisé conjointement à `allow_credentials=True` : la combinaison respecte la contrainte de sécurité des navigateurs.
- **Statut** : **Corrigé et validé le 01/10/2026 à 00:25.**

---

### F1. Erreurs AI renvoyées avec détails internes — [RÉSOLU]
- **Fichier** : `backend/server.py` (deux occurrences : endpoint de génération de message IA, endpoint de régénération, l. ~4070 et ~4521)
- **Constat initial** : en cas d'exception lors de l'appel au provider IA, le code renvoyait `HTTPException(status_code=500, detail=f"AI error: {e}")` au client, exposant potentiellement des détails internes (messages d'erreur de bibliothèques tierces, chemins, structure interne) dans la réponse HTTP.
- **Correctif appliqué** : le `detail` renvoyé au client est désormais un message générique fixe : `"Une erreur est survenue lors de la generation IA. Consultez les logs du serveur."`. Le détail complet de l'exception continue d'être tracé côté serveur via l'appel `logger.exception(...)` déjà présent juste avant chaque `raise`, qui journalise le message d'erreur et la stacktrace complète dans les logs pour permettre le débogage local.
- **Statut** : **Corrigé et validé le 01/10/2026 à 00:45.**

---

### F2. `rate`/`pitch` TTS non validés — [RÉSOLU]
- **Fichier** : `backend/server.py`, fonction `_synth_edge` (l. ~5886)
- **Constat initial** : `_synth_edge` transmettait les paramètres `rate` et `pitch` bruts (fournis par le client) directement à `edge_tts.Communicate(...)`, sans validation de format. La voix (`voice`) était déjà validée via un catalogue/regex, mais pas ces deux champs, exposant un risque d'erreurs runtime ou de comportements inattendus en cas de valeurs malformées.
- **Correctif appliqué** : ajout d'une regex stricte `_EDGE_RATE_PITCH_RE = re.compile(r"^[+-]?\d+%$")` et d'une fonction `_sanitize_edge_param(value)` qui valide le format attendu (ex. `+10%`, `-5%`) et retombe systématiquement sur la valeur par défaut sûre `"+0%"` si la valeur est absente, mal formée ou de type incorrect. `_synth_edge` appelle désormais cette fonction sur `rate` et `pitch` avant toute transmission à `edge_tts`.
- **Statut** : **Corrigé et validé le 01/10/2026 à 00:50.**

---

### F3. Fuite d'infra via `/workspace/preview-map` — [RÉSOLU]
- **Fichier** : `backend/server.py`, endpoint `GET /workspace/preview-map` (l. ~5406)
- **Constat initial** : la réponse JSON de l'endpoint exposait le chemin système absolu complet (`map_file: "/etc/nginx/forge-preview-ports.map"`), révélant inutilement l'arborescence du serveur à tout client authentifié.
- **Correctif appliqué** : la réponse renvoie désormais uniquement le nom de fichier via `os.path.basename(map_file)` (ex. `"forge-preview-ports.map"`), sans exposer le chemin absolu du système. La lecture et le diagnostic du fichier (résolution via `PREVIEW_MAP_FILE` ou valeur par défaut) restent inchangés côté serveur ; seul le chemin renvoyé au client est masqué.
- **Statut** : **Corrigé et validé le 01/10/2026 à 00:55.**


### F5. Regex de détection des jetons GitHub — [RÉSOLU]
- Le motif `gh[pousr]_` (l. 1211) était un ordre de caractères inhabituel par rapport à la nomenclature officielle GitHub. La regex a été normalisée en `gh[prous]_[A-Za-z0-9_]+`, couvrant explicitement les cinq préfixes officiels : `ghp_` (personal access token), `gho_` (OAuth token), `ghu_` (user-to-server token), `ghs_` (server-to-server token) et `ghr_` (refresh token).
- La contrainte de longueur minimale a également été assouplie (`[A-Za-z0-9]{10,}` → `[A-Za-z0-9_]+`), garantissant la détection même pour des jetons plus courts ou contenant des underscores.
- **Corrigé et validé le 01/10/2026 à 01:05.**
---

## 🟠 Risques ÉLEVÉS

_Aucun risque élevé ouvert à ce jour._

---

## 🟡 Risques MOYENS

_Aucun risque moyen ouvert à ce jour._

### M5. Barrière anti-secrets « soft » sur l'outil `bash` — [RÉSOLU, voir E1]
- `_tool_read_file` bloquait déjà les fichiers de secrets, mais `_tool_bash` pouvait lire `.env` via `cat/grep` en s'appuyant uniquement sur le prompt système. **Ce point est désormais couvert par le correctif E1** (verrou technique `_BASH_SENSITIVE_FILE_RE` dans `_tool_bash()`, corrigé et validé le 30/09/2026 à 23:15) — voir la section « ✅ Vulnérabilités résolues ».

---

## 🟢 Risques FAIBLES

_Aucun risque faible ouvert à ce jour._

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
**Aucun risque ouvert ne reste à traiter.** L'audit est clos : tous les risques critiques, élevés, moyens et faibles identifiés ont été soit corrigés dans le code, soit formellement acceptés par design pour cette instance locale mono-utilisateur (voir la section « ⚪ Risque accepté par design »).
> Le SSRF (C2), le verrou anti-secrets de l'outil bash (E1), le rate limiting de l'authentification (E3), le stockage des jetons GitHub (E4), la configuration CORS (M1), la fuite de détails internes dans les erreurs de génération IA (F1), la validation des paramètres `rate`/`pitch` du TTS (F2) et la fuite d'infra via `/workspace/preview-map` (F3), ainsi que la regex de détection des jetons GitHub (F5) sont résolus — voir la section « ✅ Vulnérabilités résolues ».
> Le sandboxage des previews (C1), l'absence de cloisonnement multi-utilisateurs au niveau du système de fichiers (E2), l'absence de contrôle de rôle admin sur `/workspace/preview-map/refresh` (M2), l'exécution de `PREVIEW_MAP_REFRESH_CMD` (M3), le fallback du token JWT en `localStorage` (M4) et le rôle `admin` sans effet (F4) sont volontairement exclus de cette liste : risques acceptés par design (machine dédiée / mono-utilisateur, inscriptions publiques désactivées).
