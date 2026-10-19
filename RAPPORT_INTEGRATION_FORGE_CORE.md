# Rapport d'intégration — `forge-core` (vibe-kanban) + `build-your-own-x-vibe-coding` pour stabiliser et améliorer la Forge

## 0. Objet et périmètre

Trois dépôts analysés, plus le dépôt amont réel identifié.

| Dépôt | Nature réelle | Usage pour notre backend |
|---|---|---|
| `namastexlabs/forge-core` | **Fork archivé de `BloopAI/vibe-kanban`** (archivé le 18/09/2026, non fonctionnel). Workspace Rust : `db`, `executors`, `server`, `services`, `deployment`, `local-deployment`, `utils`. SQLite + `sqlx`, serveur `axum`, types partagés TS. | **Fort** : c'est le cœur d'exécution / boucle d'outils / streaming / persistance qui manquait aux analyses précédentes. |
| `automagik-dev/forge` | Surcouche Rust/React au-dessus des crates `forge-core-*` (dépôt amont séparé). | Moyen : couche de composition + hook de notification. |
| `inFaaa/build-your-own-x-vibe-coding` | Tutoriels + méthode « vibe loop ». | Faible en code, utile en méthode. |

**Avertissement de fiabilité** : `forge-core` est archivé et non fonctionnel. On l'utilise comme **source de patterns**, jamais comme dépendance à importer. Aucune brique ne sera copiée telle quelle : tout est réimplémenté dans notre stack Python/JS.

---

## 1. Cartographie de `forge-core` (lu dans le code)

### 1.1 Boucle d'outils — `crates/executors`

- **Modèle d'action en chaîne, pas de compteur d'itérations.** `ExecutorAction { typ: ExecutorActionType, next_action: Option<Box<ExecutorAction>> }` avec `append_action()` (`actions/mod.rs`). La « boucle » est une **liste chaînée déclarative** d'actions, pas un `for` avec compteur.
- **Trois types d'action** : `CodingAgentInitialRequest`, `CodingAgentFollowUpRequest`, `ScriptRequest`.
- **La continuation est un nouveau processus, pas une réitération.** `CodingAgentFollowUpRequest { prompt, session_id, executor_profile_id }` relance l'agent **avec le même `session_id`**. La reprise de contexte passe par la **session**, pas par le rejeu du transcript. C'est une différence structurelle avec notre approche « continue » qui réinjecte un message.
- **Exécuteurs multiples** : `claude`, `codex`, `gemini`, `amp`, `copilot`, `cursor`, `opencode`, `qwen`, plus un harnais générique **ACP** (`executors/acp/harness.rs`) qui branche stdin/stdout de l'agent via flux duplex et signaux d'arrêt.
- **Normalisation des entrées** : `NormalizedEntryType = UserMessage | AssistantMessage | ToolUse { status: ToolStatus } | ErrorMessage | Thinking` (`logs/mod.rs`). Le **statut d'un outil est suivi par entrée** (`with_tool_status`), approbation comprise (`from_approval_status`). Un outil a donc un cycle de vie explicite (pending → running → success/error), pas un simple couple appel/résultat.

### 1.2 Flux — `msg_store.rs`, `log_msg.rs`, `services/events`

- **`MsgStore` = historique borné + diffusion live.** `history: VecDeque` plafonné à **100 Mo** + `broadcast::channel(10000)`. La méthode clé `history_plus_stream()` renvoie **l'historique rejoué, puis le flux live** (`hist.chain(live)`). C'est le pattern anti-perte à la reconnexion.
- **`LogMsg` = enveloppe unique de transport** : `Stdout`, `Stderr`, `JsonPatch`, `SessionId`, `Finished`. Elle sait se sérialiser **en SSE** (`to_sse_event`) **ou en WebSocket** (`to_ws_message`) : même modèle, deux transports.
- **Fin explicite de flux** : `LogMsg::Finished` → événement SSE `finished` vide ; les consommateurs font `take_while(!Finished)`.
- **JSON Patch (RFC 6902) incrémental.** `streams.rs::stream_tasks_raw` émet d'abord un **snapshot initial** sous forme de patch `replace /tasks`, puis des patchs incrémentaux filtrés depuis le broadcast.
- **La base est la source de vérité des événements.** `EventService` pose un **hook de mise à jour SQLite** (`set_update_hook`) : chaque changement de table relit la ligne et émet le patch correspondant ; les suppressions sont capturées dans le *preupdate hook*.

### 1.3 Persistance — `crates/db`

- SQLite + `sqlx`, migrations horodatées. Modèles : `Task`, `TaskAttempt`, `ExecutionProcess`, `ExecutionRun`, `ExecutorSession`, `ExecutionProcessLogs`.
- **Statuts terminaux** : `ExecutionProcessStatus = Running | Completed | Failed | Killed` ; `completed_at` positionné dès qu'on quitte `Running`.
- **Raisons d'exécution** : `ExecutionProcessRunReason = SetupScript | CleanupScript | CodingAgent | DevServer`.
- **Logs en JSONL** : `execution_process_logs(execution_id, logs TEXT, byte_size, inserted_at)` — un `LogMsg` par ligne, donc **rejouables**.
- **Sessions reprenables** : `executor_sessions(session_id, prompt, summary, commit_message)`.
- **Runs légers** : `execution_runs` = invocation d'exécuteur **sans** `Task`/`TaskAttempt` (micro-tâches serveur : messages de commit, descriptions de PR, refactors rapides).
- **Suivi de diff** : colonnes `before_head_commit` / `after_head_commit` sur `execution_processes`.
- **Suppression douce** : `dropped`, `masked_by_restore` → UI de restauration de runs.

### 1.4 Méthode — `build-your-own-x-vibe-coding`

- **Micro-boucles 15–25 min**, un seul objectif : build → run → note.
- **Reality checks** : chaque étape finit sur un **script exécutable autonome** (`checks/NN_*_test.py`) qui imprime pass/fail + un *hint*.
- **Journal `vibes.md`** : 3 puces par boucle, `Intention → Friction → Suite`.

---

## 2. État de notre backend (lu dans `backend/server.py`)

- **Boucle d'outils** : `MAX_TOOL_ITERS = 60`, outils exécutés via `asyncio.to_thread`, relance sur tour vide (`EMPTY_TURN_RETRIES`) sur les 5 providers, `_tools_digest()` anti-réexécution.
- **Flux** : `_SSEQueue` partage la file entre runner et connexion, `_ACTIVE_RUNS` suit les tours en cours, ping toutes les 4 s, watchdog frontend à 10 s.
- **Persistance** : `_persist_step` écrit chaque étape terminée dans le document du tour, avec `_truncate_step_logs`.

---

## 3. Écarts et briques récupérables (priorisées)

### Brique A — Rejeu d'historique à la (re)connexion — **priorité 1**
Notre flux SSE ne rejoue rien : un client qui se reconnecte après le watchdog rate des événements. `forge-core` garantit le **rejeu historique + live** (`history_plus_stream`).
**Application** : `_SSEQueue` conserve un **tampon borné** des événements du tour et le rejoue en tête de toute nouvelle connexion. Le watchdog redevient un simple déclencheur de reconnexion, plus une erreur affichée.

### Brique B — Fin de flux explicite — **priorité 1**
`LogMsg::Finished` + `take_while` : terminaison propre et unique. Chez nous, la fin de tour est implicite.
**Application** : émettre un événement terminal unique (`finished`) et fermer proprement côté client, ce qui met fin aux toasts de resynchronisation.

### Brique C — Transition de statut terminal idempotente — **priorité 1**
`Running → Completed | Failed | Killed` avec `completed_at` posé une seule fois.
**Application** : un `update_one` gardé par statut garantit qu'un tour finit toujours `done`/`error`, même si le client a disparu → fin des tours « bloqués en cours » fantômes.

### Brique D — Chaîne d'actions déclarative — **priorité 2**
Remplacer le compteur `MAX_TOOL_ITERS` (garde-fou binaire) par une **chaîne d'actions explicite** : chaque étape déclare son action et sa suivante (`next_action`), y compris les actions non-LLM (`ScriptRequest`). Deux bénéfices : la boucle devient inspectable/persistable, et on distingue enfin **le travail de l'agent** du **travail de plomberie** (setup, cleanup, dev-server), aujourd'hui mélangés dans le même compteur.
**Application** : modéliser l'étape courante comme une action typée avec statut et raison (`CodingAgent`, `ScriptRequest`, `FollowUp`). On garde `MAX_TOOL_ITERS` comme garde-fou de sécurité, mais il ne pilote plus la logique.

### Brique E — Cycle de vie d'outil explicite — **priorité 2**
`ToolUse { status }` suivi par entrée (pending/running/success/error).
**Application** : donner à chaque appel d'outil dans l'UI un statut propre, au lieu d'un appel/résultat binaire. Améliore la lisibilité des longs tours et prépare la reprise après coupure.

### Brique F — Session reprenable — **priorité 3**
`executor_sessions(session_id, prompt, summary)` : la continuation réutilise une session plutôt qu'un message « Continue… ».
**Application** : à terme, remplacer notre relance textuelle (`"Continue la tâche en cours…"`) par une vraie reprise de session côté provider, là où l'API le permet. Plus propre, mais dépend de chaque provider.

### Brique G — Reality checks automatiques — **priorité 2**
Après une étape qui modifie du code, lancer automatiquement la commande de test du projet et rattacher le résultat à l'étape (méthode `build-your-own-x`).
**Application** : complète la relance sur tour vide. Un tour ne se déclare « fini » que si le check passe, ou signale explicitement l'échec.

### Brique H — Journal de boucle — **priorité 3**
Ajouter `Intention → Friction → Suite` aux agents de la Forge (`agents/*.md`). Zéro code, gain de traçabilité et d'amélioration continue.

---

## 4. Nettoyage de notre backend (constaté en lecture seule)

- **Fichiers `.bak` suivis par git** : `backend/server.py.bak_item10`, `frontend/src/pages/Chat.jsx.bak-20261004-*` (2), `upgrade.sh.bak-*`.
- **Dizaines de `backend/server.py.bak-*` non suivis** dans l'arborescence (≈17 + les `upgrade.sh.bak-*` à la racine). Ils polluent `grep -rn` et faussent les recherches.
- **`.forge-context`** constamment modifié : à ignorer explicitement ou à sortir du suivi.
- **Commits en double** (`8240ca3` et `7bd2fff`, même message) : à surveiller côté sauvegarde automatique.
- Référence amont : `forge-core` et `automagik` maintiennent des commits dédiés « Cleanup dead code ». Même logique à appliquer chez nous.

---

## 5. Plan d'intégration (étapes atomiques)

1. **Nettoyer les `.bak`** — supprimer les fichiers listés en 4, ajouter `*.bak-*` à `.gitignore`. Vérif : `git ls-files | grep '\.bak'` ne renvoie plus rien. *(indépendant)*
2. **Écrire ce rapport** en `.md` dans le dépôt. *(indépendant)*
3. **Tests de la boucle d'outils** — porter dans `backend/tests/` les tests à faux modèle (5 providers + limite de relances + relance sur tour vide). Vérif : `pytest backend/tests`. *(indépendant, sert de filet pour 4-7)*
4. **Brique B — fin de flux explicite** — événement terminal unique + fermeture client. Requiert 3.
5. **Brique A — rejeu d'historique** — tampon borné dans `_SSEQueue` rejoué en tête de connexion ; watchdog = reconnexion. Requiert 3. Touche `backend/server.py` (SSE) et `frontend/src/lib/api.js`.
6. **Brique C — état terminal idempotent** — `update_one` gardé par statut + `completed_at`. Requiert 3.
7. **Brique G — reality checks** — exécution auto du test projet rattachée à l'étape. Requiert 3.
8. **Brique E — cycle de vie d'outil** — statut par entrée d'outil. Requiert 3.
9. **Brique D — chaîne d'actions déclarative** — remplace le pilotage par `MAX_TOOL_ITERS`. Requiert 3 et 8.
10. **Brique F — session reprenable** — par provider, après validation manuelle. Requiert 3.
11. **Brique H — journal de boucle** dans `agents/*.md`. Requiert 2 (indépendant du code).

**Ordre recommandé** : 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11.
Les briques 4, 5, 6 sont le socle « stabilisation ». Les briques 7 à 11 sont le socle « amélioration ».

---

## 6. Points non vérifiables depuis la lecture seule

- Le comportement réel d'exécution de `forge-core` : dépôt archivé et non fonctionnel, donc patterns lus mais non testés en exécution.
- La liste exacte des `*.bak-*` suivis vs non suivis (seuls quatre confirmés suivis).
- La cause racine des coupures SSE en production sur Claude, non reproduite en conditions réelles.
- L'API de reprise de session de chaque provider (Brique F) : dépend de fournisseurs externes.

---

## 7. Suivi d'exécution

### Étape 1 — Nettoyage des `.bak` — FAIT le 2026-10-18 22:28:52

- **Fichiers suivis retirés de l'index git** (`git rm --cached`) : `backend/server.py.bak_item10`, `frontend/src/pages/Chat.jsx.bak-20261004-010941`, `frontend/src/pages/Chat.jsx.bak-20261004-012215`, `upgrade.sh.bak-20261004-005003`.
- **Fichiers `.bak*` supprimés du disque** : 62 au total (archive de sécurité préalable dans `/tmp/forge_bak_archive_20261018-2228/backups.tar.gz`).
- **`.gitignore` complété** : ajout de `*.bak` et `*.bak.*` aux règles existantes `*.bak-*` et `*.bak_*`.
- **Vérification** : `git ls-files | grep '\.bak'` ne renvoie plus rien ; les 4 variantes (`.bak`, `.bak-1`, `.bak_1`, `.bak.item10`) sont toutes ignorées par git.
- **Fichiers réels intacts** : `backend/server.py` (302114 o), `frontend/src/pages/Chat.jsx` (72486 o), `frontend/src/lib/api.js` (7687 o), `upgrade.sh` (15812 o).
- **Statut** : étape terminée, non commitée (en attente de la sauvegarde GitHub).

### Étape 2 — Écriture du rapport en `.md` — FAIT le 2026-10-18 22:35:25

- **Fichier** : `RAPPORT_INTEGRATION_FORGE_CORE.md` (racine du dépôt, branche `claude-ai`).
- **Contenu** : sections 0 à 6 (objet, cartographie `forge-core`, état du backend, briques A→H priorisées, nettoyage, plan en 11 étapes, limites).
- **Intégrité vérifiée** : 144 lignes, 8 briques présentes, plan complet, fin de fichier propre.
- **Commit** : `7096ae6 docs: rapport d'intégration forge-core (vibe-kanban) — patterns flux SSE, statut terminal idempotent et boucle d'outils pour stabiliser la Forge`.
- **Statut** : étape terminée et enregistrée.

---
### Étape 3 — Tests de la boucle d’outils — FAIT le 2026-10-18 23:23:24 CEST

- **Fichier ajouté** : `backend/tests/test_tool_loop.py` — 7 tests, transports et exécution d’outils simulés, sans appels réseau.
- **Providers couverts** : OpenAI-compatible, Claude, Gemini, Ollama local et Ollama Cloud ; vérification de la relance après un tour vide suivant l’exécution d’un outil.
- **Limites couvertes** : nombre maximal de relances `EMPTY_TURN_RETRIES` et garde-fou `MAX_TOOL_ITERS`.
- **Vérification ciblée** : `backend/.venv/bin/python -m pytest backend/tests/test_tool_loop.py -q` — 7 passed.
- **Suite complète** : `pytest backend/tests` ne passe pas la collecte : `test_kokoro_optional.py` appelle `.rstrip()` sur `REACT_APP_BACKEND_URL` absent. Les tests préexistants n’ont donc pas été exécutés dans cette tentative.
- **Statut** : étape terminée ; aucun changement du code applicatif. Étape 4 et suivantes en attente du feu vert.

### Étape 3 — Correction de la collecte des tests — FAIT le 2026-10-18 23:35:18 CEST

- **Défaut corrigé** : `backend/tests/test_kokoro_optional.py:17` appelait `.rstrip("/")` sur `os.environ.get("REACT_APP_BACKEND_URL")` sans valeur par défaut, ce qui faisait échouer la collecte de `pytest backend/tests` (`AttributeError: 'NoneType' object has no attribute 'rstrip'`) et bloquait l'exécution de tous les tests.
- **Correction** : valeur par défaut `http://127.0.0.1:8001` ajoutée, alignant ce fichier sur `test_deps_regression.py`, `test_regression.py` et `test_tts_bugs.py`. Diff d'une seule ligne, hors périmètre applicatif.
- **Vérification** : `backend/.venv/bin/python -m pytest backend/tests --collect-only -q` — 47 tests collectés, 0 erreur (contre « 34 collected, 1 error » avant correction).
- **Tests de l'étape 3** : `backend/.venv/bin/python -m pytest backend/tests/test_tool_loop.py -q` — 7 passed.
- **Statut** : étape 3 conforme au plan ; le test de vérification `pytest backend/tests` n'est plus bloqué par la collecte. Les échecs restants de la suite préexistante relèvent de tests d'intégration HTTP nécessitant un backend en écoute, sans rapport avec l'étape 3. Étape 4 et suivantes en attente du feu vert.

### Étape 4 — Brique B : fin de flux explicite — FAIT le 2026-10-19 00:39:06 CEST

- **Objectif** : émettre un **événement terminal unique** (`finished`) et fermer proprement le flux côté client, pour supprimer la fin de tour implicite (source des toasts de resynchronisation et des coupures perçues).
- **Backend — `backend/server.py`** :
  - `_SSEQueue.finish(status, **extra)` : émet `{"type": "finished", "status": ...}` puis ferme la file. **Idempotent** (un second appel n'émet rien).
  - `_SSEQueue.close()` : rendu idempotent (une seule sentinelle `__end__`).
  - `_SSEQueue.publish()` : ignore tout événement publié **après** fermeture (plus rien après le terminal).
  - Pont SSE : mappe `finished` → `event: finished` côté HTTP.
  - `_run_generation` : chemin nominal → `finish("done"|"error")` après `done` ; chemin erreur sans contenu → `finish("error")` ; `finally` → `finish(...)` comme filet de sécurité (couverture annulation/exception).
- **Frontend — `frontend/src/lib/api.js`** :
  - Suivi de `terminalSeen` dans `flushBlock` (`finished` ou `done`).
  - Sortie de boucle dès le terminal reçu + `reader.cancel()` (fermeture propre, sans attendre le watchdog).
  - `catch` : plus d'erreur `STREAM_INTERRUPTED` si le terminal a déjà été vu.
- **Frontend — `frontend/src/pages/Chat.jsx`** :
  - `terminalRef` remis à `false` à chaque envoi, posé à `true` sur `done`/`finished`.
  - Branche `catch` : si `terminalRef.current`, aucune resynchronisation ni toast d'erreur.
- **Tests ajoutés** : `backend/tests/test_sse_finish.py` (4 tests) — terminal unique, idempotence de `finish`, `publish` ignoré après fermeture, statut + données additionnelles.
- **Vérifications** :
  - `pytest backend/tests/test_sse_finish.py` → **4 passed**.
  - `pytest backend/tests/test_tool_loop.py backend/tests/test_sse_finish.py` → **11 passed**.
  - `pytest backend/tests --collect-only` → **51 tests collectés, 0 erreur**.
  - `py_compile backend/server.py` → OK ; `esbuild` sur `api.js` et `Chat.jsx` → OK.
  - `git diff --check` → propre.
- **Non vérifié** : le rendu réel dans un navigateur (flux live non rejoué en sandbox) ; `eslint` non exécutable (configuration `eslint.config.js` absente du dépôt, préexistant).

### Étape 5 — Brique A : rejeu d'historique à la reconnexion — FAIT le 2026-10-19 11:16:01 CEST

- **Objectif** : ne plus perdre d'événements quand un client se (re)connecte en plein tour. Un **tampon borné séquencé** dans `_SSEQueue` est rejoué en tête de connexion ; le watchdog frontend devient une **reconnexion transparente** au lieu d'une erreur.
- **Backend — `backend/server.py`** :
  - `_SSEQueue` réécrite : **tampon borné** (`SSE_BUFFER_MAX`, défaut 500) + **numérotation monotone** (`seq`) de chaque événement + **diffusion multi-abonnés**.
  - `subscribe(after_seq)` : rejoue le tampon depuis `after_seq` puis suit le live, **atomiquement** (aucun `await` entre rejeu et enregistrement → pas de trou). `unsubscribe()` nettoie l'abonné en sortie.
  - `stream(ping_interval, after_seq)` : génère le rejeu puis le direct ; heartbeat `__ping__` conservé.
  - Garantie terminale : à la reprise d'un tour **déjà clos**, l'événement `finished` est **toujours** livré, même si `after_seq` dépasse le dernier `seq` du tampon (sinon le client resterait bloqué sans savoir que le tour est fini).
  - `_sse_from_event(ev)` : mapping **unique** événement interne → trame SSE (avec `seq`), partagé par les deux ponts.
  - `chat_stream` : l'écho `user_message` est désormais **tamponné** (rejoué à la reconnexion) ; pont SSE factorisé via `_sse_from_event`.
  - **Nouvel endpoint** `GET /chat/stream/{conversation_id}?after_seq=N` : rejoue depuis `N` si un run est actif, sinon émet `finished` immédiatement.
- **Frontend — `frontend/src/lib/api.js`** :
  - `readSSEStream()` : boucle de lecture partagée, mémorise `lastSeq` (via `data.seq`) et `terminalSeen`.
  - `resumeSSE(conversationId, afterSeq)` : reprise `GET` du flux depuis le dernier `seq` vu.
  - `postSSE` : sur coupure (watchdog/réseau) **sans** terminal vu, déclenche la **reprise transparente** ; ne lève `STREAM_INTERRUPTED` que si la reprise échoue aussi. `conversation_id` récupéré du formulaire.
- **Tests ajoutés** : `backend/tests/test_sse_replay.py` (5 tests) — rejeu complet, rejeu partiel après `seq`, terminal seul si rien de neuf, tampon borné, ajout du `seq` dans la trame.
- **Test adapté** : `test_sse_finish.py::test_finish_carries_status_and_extra` — vérifie désormais que chaque événement porte un `seq` (nouveau contrat de la Brique A).
- **Vérifications** :
  - `pytest backend/tests/test_sse_finish.py test_sse_replay.py test_tool_loop.py` → **16 passed**.
  - `pytest backend/tests --collect-only` → **56 tests collectés, 0 erreur**.
  - `py_compile backend/server.py` → OK ; `esbuild` sur `api.js` et `Chat.jsx` → OK.
  - `git diff --check` → propre.
- **Défauts corrigés pendant l'étape** : (1) terminal non livré quand `after_seq` dépassait le tampon ; (2) assertion de test erronée sur la borne du tampon. Les deux sont couverts par les tests.
- **Non vérifié** : la reconnexion réelle dans un navigateur (rafraîchir en plein tour) — non exécutable en sandbox sans serveur + client lancés.

---

# 8. Annexe — Bascule des previews en conteneurs Docker isolés

**Rédigé le 2026-10-19 21:54:25 CEST** — analyse de faisabilité et plan de mise en place.
Statut : **étude uniquement — aucun paquet installé, aucun code modifié.**

## 8.1 Intention retenue

Les previews cessent d'être des processus lancés **sur l'OS hôte** (`PreviewManager` +
`subprocess.Popen`) et deviennent des **conteneurs Docker jetables** :

1. **Docker = sandbox des previews uniquement**, sous limites de ressources strictes.
2. **Endormissement après inactivité** : conteneur `stop` (workspace conservé sur l'hôte pour
   GitHub), **aucun réveil automatique** — reprise sur action explicite de l'utilisateur.
3. **Interdiction stricte aux agents** de lancer compilation ou service sur l'OS principal :
   tout s'exécute dans le conteneur du projet, sur ordre explicite.
4. **Secrets maîtrisés** : `.env` jamais affiché en clair, masquage/tronquage systématique des
   sorties (`sed`, `grep`) pour éviter l'exposition de clés et les faux blocages de sécurité.

## 8.2 Constat serveur (mesuré le 2026-10-19)

| Élément | Valeur réelle | Conséquence |
|---|---|---|
| vCPU | 2 | Compilations **sérialisées** obligatoires |
| RAM | 3,8 GiB (≈ 2,2 dispo) | **1 conteneur en build à la fois** ; 768 Mo/conteneur |
| Swap | **absent** | OOM = mort d'un service ; swapfile à créer avant Docker |
| Disque | 915 G, 40 G utilisés | Suffisant (image de base ≈ 1,5 Go) |
| Noyau / cgroups | 7.0.0-38, cgroup v2 unifié | Compatible Docker |
| Docker | **non installé** (`docker.io` 29.1.3 dispo en apt) | Installation requise |
| `forge-backend` | `User=quentin`, `ProtectSystem=strict`, `/run` inscriptible | Accès `/run/docker.sock` possible |
| `forge-backend-preview` | `User=www-data`, racine `/var/www/forge-dev` | Groupe `docker` à prévoir aussi |
| Previews actuelles | `backend/preview_runtime.py` (618 l.), ports 8090→8189 | Lancement **hôte**, à remplacer |
| Reverse proxy | nginx wildcard `*.preview.quentin-astro.fr` → `127.0.0.1:$port` | Modèle conservable tel quel |
| Inactivité | `PREVIEW_IDLE_TIMEOUT=900`, `PREVIEW_AUTOSTART=0` | Base saine, à durcir |
| **Faille** | `PreviewManager._env()` fait `dict(os.environ)` | **Toutes les clés API du backend sont héritées par les projets** |
| Masquage | `redact_secrets()` (server.py:2042) | Réutilisable et à étendre |

### Cause de la boucle infinie constatée

`gemini-perso.service` : `NRestarts=1451`, `Result=exit-code`, code 3.
Boucle = `Restart=always` + `RestartSec=3` **sans `StartLimitBurst`**, sur un échec de démarrage
**systématique** : `pymongo.errors.ServerSelectionTimeoutError` (hôte MongoDB non résolvable,
`[Errno -2] Name or service not known`). Chaque cycle coûte ~37 s de temps réel : d'où la charge,
la saturation SSH et les déconnexions. **Ce n'est pas un problème Docker** : correctif immédiat
en 8.3 Étape 0.

## 8.3 Faisabilité — verdict

**Faisable**, avec trois contraintes structurantes :

* **2 vCPU / 3,8 GiB** : impossible de builder plusieurs projets simultanément → **file d'attente
  sérialisée** (un seul build à la fois) et `--memory=768m --cpus=1.0 --pids-limit=256` par conteneur.
* **Pas de swap** : à créer (2 Go) avant toute charge Docker, sinon l'OOM killer tue au hasard.
* **Docker est un privilège root déguisé** : `forge-backend` doit joindre le socket sans être root
  → passer par le groupe `docker` (accès quasi-root assumé) **ou** par un proxy `docker-socket-proxy`
  limité aux verbes nécessaires. Recommandé : proxy.

## 8.4 Étapes indispensables (ordre d'exécution)

### Étape 0 — Stopper l'hémorragie (immédiat, hors Docker)
- `systemctl stop gemini-perso` puis `systemctl disable gemini-perso` (échec de démarrage permanent).
- Ajouter dans toute unité Forge : `StartLimitIntervalSec=300` + `StartLimitBurst=5` (section `[Unit]`)
  et `Restart=on-failure` au lieu de `always` sur les services à démarrage fragile.
- Créer un swapfile 2 Go (`fallocate -l 2G /swapfile`, `mkswap`, `swapon`) et l'inscrire en `/etc/fstab`.
- **Vérif** : `swapon --show` renvoie 2 G ; `systemctl --failed` vide ; `NRestarts` stable après 10 min.

### Étape 1 — Installation Docker + daemon durci
- `apt-get install docker.io docker-compose-v2` (dépôt Ubuntu, pas de `curl | sh`).
- `/etc/docker/daemon.json` : `live-restore: true`, rotation des logs (`max-size 10m`, `max-file 3`),
  `no-new-privileges: true`, `default-ulimits.nofile` 2048/4096.
- **Ne pas** exposer l'API TCP : socket Unix uniquement.
- Accès du service : soit `quentin` + `www-data` ajoutés au groupe `docker` (privilège quasi-root),
  soit `docker-socket-proxy` restreint (`CONTAINERS=1 IMAGES=1`, `EXEC=0`). Recommandé : proxy.
- `forge-backend.service` conserve `ProtectSystem=strict` ; `/run` est déjà en écriture (socket).
- **Vérif** : `docker info` (cgroup v2, overlay2) ; `docker run --rm hello-world` **sans sudo**.

### Étape 2 — Image de base unique des previews
- `deploy/docker/preview-base.Dockerfile` : `node:22-bookworm-slim` + python3/pip/git, utilisateur
  non-root `preview` (UID 10001), `WORKDIR /workspace`, `CMD ["sleep","infinity"]`.
- Un **seul** tag versionné (`forge-preview-base:1`). Aucun `.env`, aucun secret dans l'image.
- **Vérif** : build < 2 min ; `docker run --rm forge-preview-base:1 id` renvoie UID 10001.

### Étape 3 — Modèle d'exécution (bind-mount, pas de copie)
- Le workspace **reste sur l'hôte** (`/var/www/forge/workspace/<projet>`), monté en
  `-v <projet>:/workspace:rw`. Aucune duplication : GitHub et l'utilisateur gardent la main.
- `--read-only` + `--tmpfs /tmp:rw,noexec,nosuid,size=256m` ; pas de `--privileged`,
  `--cap-drop=ALL`, `--security-opt=no-new-privileges`.
- **Vérif** : dans le conteneur `touch /etc/x` échoue, `touch /workspace/ok` réussit.

### Étape 4 — Limites de ressources
- `--memory=768m --memory-swap=768m --cpus=1.0 --pids-limit=256`.
- **File sérialisée** pour `npm install` / `pip install` : sémaphore asyncio global (1 slot) dans
  `PreviewManager`, pour ne jamais lancer deux builds sur 2 vCPU.
- **Vérif** : `docker stats --no-stream` pendant un build → mémoire plafonnée ; 2e build en attente.

### Étape 5 — Cycle de vie et états
- Remplacer `PreviewProcess` par `PreviewContainer` : `create → start → wait_ready → stop → remove`.
- Attente d'ouverture de port identique à l'existant (polling 180 s) mais via le socket Docker.
- États exposés **inchangés** (`stopped|starting|running|error`) : l'API frontend ne bouge pas.
- Arrêt = `docker stop -t 10` ; suppression = `docker rm -f` + nettoyage des volumes anonymes.
- **Vérif** : `preview/start|stop|status|logs` renvoient le même schéma JSON qu'aujourd'hui.

### Étape 6 — Sommeil sur inactivité, sans réveil automatique
- Conserver `PREVIEW_IDLE_TIMEOUT` (900 s) ; le reaper appelle `docker stop` au lieu de `killpg`.
  Le conteneur **arrêté est conservé**, le workspace hôte est intact.
- `PREVIEW_AUTOSTART` reste **0** : après redémarrage du backend, **rien** n'est relancé.
  Reprise uniquement via `POST /preview/start` (action explicite) → `docker start`.
- `--restart=no` (jamais `always`) : Docker ne ressuscite pas les previews au boot.
- **Vérif** : preview inactive > 15 min → `docker ps` vide, `docker ps -a` montre `Exited (0)` ;
  `systemctl restart forge-backend` → **aucun** conteneur relancé.

### Étape 7 — Interdiction de compiler sur l'OS hôte
- `PreviewManager` : supprimer tout `subprocess.Popen` de `npm`/`pip`/`node`/`uvicorn` hôte ; ces
  commandes ne sont plus produites que sous forme d'`docker exec` dans le conteneur du projet.
- Côté agent : confinement au répertoire du projet **et** contrôle que les commandes de build/serve
  passent par l'API preview (allowlist) ; refus explicite et journalisé sinon.
- **Vérif** : `grep -n "subprocess.Popen" backend/preview_runtime.py` → plus aucun lancement de service ;
  tentative `npm run dev` hors conteneur → refus journalisé.

### Étape 8 — Secrets : injection et masquage
- Variables du projet injectées **au runtime** (`--env-file` ou `docker exec --env`), **jamais**
  copiées dans l'image ni dans un `Dockerfile`.
- **Fin de l'héritage global** : `_env()` ne fait plus `dict(os.environ)` (fuite actuelle de toutes
  les clés du backend). Liste blanche stricte (`PATH`, `HOME`, `LANG`, `NODE_ENV`, `PORT`).
- Masquage : étendre `redact_secrets()` (`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `sk-*`,
  `ghp_*`, `Bearer …`) et l'appliquer à **toute** sortie de log preview.
- Tronquage : logs UI filtrés via `sed -E "s/(=.{4}).*/\1***/"` ; jamais de dump intégral de `.env`.
- **Vérif** : `docker inspect` sans secret en clair hors variables injectées ; `preview/logs` sur un
  projet à `.env` ne révèle aucune valeur ; test unitaire de `redact_secrets()` sur 6 motifs.

### Étape 9 — Reverse proxy et domaine (inchangé)
- nginx wildcard existant conservé : chaque conteneur publie `-p 127.0.0.1:<port>:<port>` dans la
  plage 8090→8189, donc `proxy_pass http://127.0.0.1:$port` fonctionne sans modification.
- Aucun port publié sur `0.0.0.0` : **jamais** d'exposition directe.
- **Vérif** : `ss -ltn` ne montre que des `127.0.0.1:<port>` ; le domaine preview répond en HTTPS.

### Étape 10 — Hygiène, quotas, nettoyage
- Étiqueter chaque conteneur : `--label forge.project=<nom> --label forge.managed=1` (base du GC).
- `docker system df` en tâche quotidienne ; `docker image prune -f --filter until=168h`.
- Plafond de conteneurs simultanés (ex. 3) ; au-delà, refus explicite à l'utilisateur.
- État reconstruit depuis `docker ps -a --filter label=forge.managed=1` (pas d'état en mémoire).
- **Vérif** : après GC, aucune image intermédiaire ; disque stable sur 7 jours.

### Étape 11 — Recette et retour arrière
- Recette : 3 projets (node, python, statique) → start/stop/sleep/reprise, log sans secret,
  domaine HTTPS, isolation (écriture hors workspace refusée).
- Retour arrière : `PREVIEW_RUNTIME=host` (variable d'env) conserve l'ancien `PreviewManager`
  fonctionnel pendant toute la transition ; bascule atomique, sans migration de données.
- **Vérif** : avec `PREVIEW_RUNTIME=host`, les previews repartent exactement comme avant.

## 8.5 Ordre recommandé et effort

| # | Étape | Effort | Bloquant pour la suite |
|---|---|---|---|
| 0 | Stop boucle gemini + swap + StartLimit | 20 min | **Oui** (stabilité serveur) |
| 1 | Docker + daemon durci | 30 min | Oui |
| 2 | Image de base | 30 min | Oui |
| 3-4 | Montages + limites + file sérialisée | 2 h | Oui |
| 5-6 | Cycle de vie + sommeil sans réveil | 3 h | Oui |
| 7 | Interdiction build hôte | 1 h 30 | Non (peut suivre) |
| 8 | Secrets + masquage | 1 h 30 | Non (à faire avant prod) |
| 9-10 | Proxy + hygiène | 1 h | Non |
| 11 | Recette + rollback | 1 h | — |

## 8.6 Points de vigilance

- **Ne pas** installer Docker avant l'étape 0 : sans swap ni `StartLimitBurst`, un build qui part en
  boucle reproduirait exactement la panne actuelle, en plus grave.
- **Un seul build à la fois** : c'est la contrainte dimensionnante des 2 vCPU, non négociable.
- **Le groupe `docker` vaut root** : si le proxy socket n'est pas retenu, l'isolation des agents
  reste protéiforme — un agent qui atteint le socket peut tout faire sur l'hôte.
- **Workspace monté en `rw`** : un projet peut corrompre ses propres fichiers ; c'est voulu (Git
  reste le filet), mais aucun montage ne doit dépasser son dossier.
- **`--memory-swap` égal à `--memory`** : sans swap hôte, un conteneur qui s'échappe déclenche l'OOM
  killer ; c'est préférable à un gel du serveur, mais doit être surveillé (`dmesg | grep -i oom`).

## 8.7 Non vérifiable sans exécution

- Le débit réel de `npm install` dans un conteneur sur 2 vCPU (estimation, non mesuré).
- L'impact du proxy socket sur la latence des appels `docker` depuis `server.py`.
- Le comportement de nginx wildcard avec des conteneurs démarrés/arrêtés (test de charge à faire).
Ces trois points ne peuvent être tranchés qu'après l'étape 1.

