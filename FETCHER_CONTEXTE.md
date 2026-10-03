# Fetcher de contexte & mémoire persistante

> Document généré pour recenser **toutes les fonctions ajoutées** par la
> fonctionnalité « Fetcher de contexte ». Dernière mise en place :
> module inséré dans `backend/server.py` et bandeau dans `frontend/src/pages/Chat.jsx`.

## 1. Objectif

Collecter et mémoriser en continu des **faits clés par projet**, puis les
**réinjecter automatiquement** dans le prompt système (`forge_system_prompt`)
pour que la Forge garde le contexte d'un projet d'un tour à l'autre et après
redémarrage.

Trois moments de déclenchement :

1. **Ouverture d'un projet** → scan structurel (snapshot).
2. **Première conversation puis chaque tour** → pré-résolution du bloc contexte.
3. **Info importante détectée** (utilisateur ou système) → mémorisation.

---

## 2. Backend — `backend/server.py`

### 2.1 Constantes de configuration (lignes ~965-1042)

| Nom | Rôle |
|-----|------|
| `CONTEXT_MAX_FACTS` | Nombre max de faits injectés (env `FORGE_CONTEXT_MAX_FACTS`, défaut 40). |
| `CONTEXT_TREE_MAX_ENTRIES` | Max d'entrées d'arborescence (env `FORGE_CONTEXT_TREE_MAX_ENTRIES`, défaut 80). |
| `CONTEXT_TREE_MAX_DEPTH` | Profondeur max du scan (env `FORGE_CONTEXT_TREE_MAX_DEPTH`, défaut 3). |
| `_CONTEXT_SKIP_DIRS` | Dossiers ignorés au scan (`node_modules`, `.git`, `__pycache__`, etc.). |
| `_CONTEXT_MANIFESTS` | Fichiers manifestes inspectés (`package.json`, `requirements.txt`, `pyproject.toml`…). |
| `_RULE_KEYWORDS` | Mots-clés de **règle / instruction** prioritaire (prio 4). |
| `_ARCH_KEYWORDS` | Mots-clés d'**architecture** / décision technique (prio 3). |
| `_SYS_PATH_RE` | Détection des **chemins système** clés (prio 2). |
| `_ENV_VAR_RE` | Détection des **variables d'environnement** (prio 2). |
| `_FACT_PATTERNS` | Motifs factuels (port, version, URL…) avec leur `kind` (prio 1). |
| `_MIN_SENTENCE_LEN` | Longueur minimale d'une phrase pour être considérée. |

### 2.2 Fonctions ajoutées

| Fonction | Ligne | Rôle |
|----------|-------|------|
| `_project_context_file(name)` | 1044 | Chemin du fichier de contexte projet (`.forge-rules` / contexte). |
| `fetch_project_snapshot(name)` | 1149 | Scan structurel : stack, arborescence, git, README, règles. |
| `_norm_fact(text)` | 1174 | Normalise un fait (anti-doublon). |
| `_fact_priority(text)` | 1179 | **Cœur du tri** : classe un fait en `(priorité, kind)`. |
| `_looks_important(text)` | 1212 | Filtre **anti-bruit** : rejette ce qui n'a pas d'enjeu. |
| `_extract_fact_labels(text)` | 1217 | Extrait les libellés factuels (compat). |
| `remember_context_facts(...)` | 1229 | Enregistre/consolide des faits en Mongo (`context_facts`). |
| `capture_user_facts(user_id, project, text)` | 1279 | Capture les faits d'un **message utilisateur**. |
| `_extract_system_facts(tool_steps)` | 1308 | Extrait les faits des **étapes outil système** (décisions/chemins). |
| `recall_context_facts(user_id, project)` | 1347 | Relit les faits mémorisés d'un projet. |
| `fetch_and_store_project(...)` | 1358 | **Déclencheur « ouverture »** : scan + mémorisation. |
| `_render_context_block(snap, facts)` | 1405 | Fabrique le **bloc texte** injecté dans le prompt système. |
| `sync_context_file(name, snap, facts)` | 1440 | Réécrit le fichier de contexte projet sur disque. |
| `get_project_context(user_id, project)` | 1462 | Assemble contexte complet (snapshot + faits). |
| `prepare_turn_context(user_id, project)` | 1489 | **Déclencheur « tour »** : pré-résout le bloc dans un ContextVar. |

### 2.3 Endpoints API

| Méthode | Route | Ligne | Rôle |
|---------|-------|-------|------|
| `GET` | `/context/{project}` | 4780 | Lit la mémoire d'un projet (snapshot + faits triés). |
| `POST` | `/context/{project}/refresh` | 4820 | Force un re-scan complet du projet. |

### 2.4 Points de branchement (appels)

| Ligne | Appel | Contexte |
|-------|-------|----------|
| 4493 | `fetch_and_store_project` | Rattachement conversation ↔ projet. |
| 4642-4647 | `prepare_turn_context` + `capture_user_facts` | Tour `chat_send` (non-stream). |
| 4969 | `capture_user_facts` | Flux `chat_stream`. |
| 5172 | `prepare_turn_context` | `_run_generation` (tâche de fond / autonome). |
| 5234 | `_extract_system_facts` | Faits issus des outils après génération. |
| 5921 | `fetch_and_store_project` | Création de conversation. |
| 6445 | `fetch_and_store_project` | Création de projet. |

> L'**injection** dans le prompt se fait via `forge_system_prompt()`
> (appelée à ~11 endroits : 1950, 2248, 2864, 3173, 3515, 3633, 3800,
> 3862, 3939, 4003, 4116).

### 2.5 Base de données

- Collection Mongo **`context_facts`**, indexée sur `(user_id, project, norm)`.
- Chaque fait : `text`, `kind`, `priority`, `source`, `confirmations`,
  `updated_at`.

---

## 3. Frontend — `frontend/src/pages/Chat.jsx`

| Élément | Rôle |
|---------|------|
| État `projectContext` | Stocke la mémoire chargée du projet actif. |
| `useEffect` | Charge `/context/{project}` quand la conversation active change de projet. |
| **Bandeau discret** (sous le header) | Badge « contexte N fait(s) », stack + branche/commit, détail dépliable des faits **colorés par priorité** (rouge = règles, jaune = architecture…). |

---

## 4. Grille de priorité des faits

| Prio | Catégorie | Exemple |
|------|-----------|---------|
| **4** | Règle / instruction / décision **explicite** | « Il faut toujours… », « Retiens que… », « Désormais… » |
| **3** | **Architecture** / choix technique acté | « On a choisi FastAPI… », « La stack est… », branche/endpoint/deploy |
| **2** | **Chemin système clé** ou **variable d'env.** | `/var/www/forge/.venv`, `DATABASE_URL est utilisée` |
| **1** | Fait factuel minimal | version, port, URL, domaine |
| **0** | **Bruit → non mémorisé** | politesses, logs, phrases sans enjeu |

---

## 5. Variables d'environnement

| Variable | Défaut | Rôle |
|----------|--------|------|
| `FORGE_CONTEXT_MAX_FACTS` | 40 | Nombre max de faits injectés. |
| `FORGE_CONTEXT_TREE_MAX_ENTRIES` | 80 | Max d'entrées du scan d'arborescence. |
| `FORGE_CONTEXT_TREE_MAX_DEPTH` | 3 | Profondeur du scan. |
