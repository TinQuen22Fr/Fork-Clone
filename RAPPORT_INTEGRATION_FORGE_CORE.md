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
