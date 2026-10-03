# Journal des fonctions ajoutees — Forge (Claude Unchained)

> Periode couverte : **depuis le 2 octobre apres-midi** jusqu'a maintenant.
> Deux chantiers concernes : **Mode Autonomie** (commit `84a6930`, 3 oct 00:42) et **Fetcher de contexte / memoire persistante** (travail non committe, en cours de deploiement).
> Le fix `upgrade.sh` (`4c4e73d`, 2 oct 08:05) ne contient **aucune fonction nouvelle** (1 ligne de condition Git).

---

## 1. Mode Autonomie — commit `84a6930`

> Objectif : si la connexion de l'utilisateur coupe, l'agent continue d'executer le prompt ; au retour, l'utilisateur voit les taches en cours et le resultat.
> Documentation : `REPRISE_MODE_AUTONOME.md`.

### Backend — `backend/server.py`

| Fonction / classe | Ligne | Role |
|---|---|---|
| `_summarize_turn(content, tools, stopped)` | 4711 | Resume un tour (reponse + outils utilises + statut d'arret) pour le journal de reprise. |
| `chat_status(current_user)` | 4728 | **Endpoint `GET /chat/status`** : renvoie au client l'etat des tours (en cours / termines) de l'utilisateur, ce qui declenche la reprise a la reconnexion. |
| `class _SSEQueue` | 5113 | File de messages Server-Sent Events pour piloter un flux de generation en arriere-plan et le rejouer au client qui se reconnecte. |
| ├─ `_SSEQueue.__init__` | 5124 | Initialise la file et son etat. |
| ├─ `_SSEQueue.put(ev)` | 5125 | Empile un evenement (usage interne). |
| ├─ `_SSEQueue.publish(ev)` | 5128 | Diffuse un evenement aux abonnes. |
| ├─ `_SSEQueue.close()` | 5135 | Ferme le flux proprement. |
| └─ `_SSEQueue.stream()` | 5139 | Genere le flux SSE consomme par le client. |
| `_run_generation(...)` | 5148 | **Cœur du mode autonome** : execute la generation du LLM en tache de fond, persiste le resultat, et continue meme si le client est deconnecte. |

### Frontend — `frontend/src/pages/Chat.jsx`

| Element | Ligne | Role |
|---|---|---|
| `notice` / `setNotice` (etat) | 74 | Message transitoire (ex. avis de reprise apres reconnexion). |
| `statusTurns` / `setStatusTurns` (etat) | 116 | Liste des tours termines pendant l'absence. |
| `runningTurns` / `setRunningTurns` (etat) | 117 | Liste des tours en cours (agent en train de travailler). |
| `fetchStatusTurns()` | 144 | Appelle `GET /chat/status` et met a jour les deux etats. |
| `useEffect` (resync) | — | Resynchronise l'etat a la reconnexion de l'onglet (evenement `online`). |
| `useEffect` (polling) | — | Interroge periodiquement `fetchStatusTurns` quand des tours tournent. |
| **Bandeau statut agent** | 821-868 | Bloc UI : liste des tours en cours (`runningTurns`) et des tours termines (`statusTurns`). |
| **Affichage `notice`** | 1330-1336 | Bandeau d'avis (`data-testid="chat-notice"`) avec bouton de fermeture. |

---

## 2. Fetcher de contexte / memoire persistante — travail non committe

> Objectif : collecter et memoriser en continu les faits cles par projet, puis les reinjecter automatiquement dans le prompt systeme.
> Documentation detaillee : `FETCHER_CONTEXTE.md`.

### Backend — `backend/server.py`

| Fonction | Ligne | Role |
|---|---|---|
| `fetch_project_snapshot(project)` | ~950 | Scan d'ouverture : structure, stack, README, `.forge-rules`, etat Git d'un projet. |
| `_fact_priority(text)` | ~1000 | **Cœur du tri** : attribue une priorite (0-4) a un fait ; 0 = bruit rejete. |
| `_looks_important(text)` | ~1030 | Filtre anti-bruit : decide si un texte merite d'etre memorise. |
| `_extract_fact_labels(text)` | ~1090 | Extrait les labels/categories de faits d'un texte. |
| `remember_context_facts(...)` | ~1110 | Ecrit les faits en base Mongo (`context_facts`), dedupliques et priorises. |
| `capture_user_facts(text, ...)` | ~1150 | Capture les faits importants provenant du message **utilisateur**. |
| `_extract_system_facts(...)` | ~1200 | Capture les faits provenant des **outils / du systeme** (chemins, decisions). |
| `recall_context_facts(...)` | ~1230 | Relit les faits memorises d'un projet. |
| `fetch_and_store_project(...)` | ~1280 | Orchestration a **l'ouverture d'un projet** : snapshot -> memorisation. |
| `_render_context_block(...)` | ~1330 | Fabrique le bloc texte a injecter dans le prompt systeme. |
| `sync_context_file(...)` | ~1350 | (Optionnel) Synchronise la memoire dans un fichier de contexte. |
| `get_project_context(...)` | ~1370 | Lecture du contexte (utilise par l'endpoint). |
| `prepare_turn_context(...)` | ~1390 | **Avant chaque appel LLM** : re-resout le bloc de contexte (async), y compris en tache de fond. |

**Endpoints**
- `GET /context/{project}` — lire la memoire de contexte d'un projet.
- `POST /context/{project}/refresh` — forcer un re-scan du projet.

**Collection Mongo** : `context_facts` (index `(user_id, project, norm)`).

**Injection** : via `forge_system_prompt()` (re-injectee a chaque appel LLM).

### Frontend — `frontend/src/pages/Chat.jsx`

| Element | Role |
|---|---|
| Etat `projectContext` | Contient le contexte charge du projet actif. |
| `useEffect` (chargement) | Appelle `GET /context/{project}` quand `activeConv.project` change. |
| **Bandeau discret** | Sous le header : badge « contexte N fait(s) », stack + branche/commit, detail depliable des faits colores par priorite. |

---

## 3. Grille de priorite des faits (Fetcher)

| Prio | Categorie | Exemple |
|---|---|---|
| **4** | Regle / instruction / decision explicite | « Il faut toujours… », « Retiens que… » |
| **3** | Architecture / choix technique acte | « On a choisi FastAPI… », « Endpoint/deploy/branche » |
| **2** | Chemin systeme cle ou variable d'env. | `/var/www/forge/.venv`, `DATABASE_URL est utilisee` |
| **1** | Fait factuel minimal | version, port, URL, domaine |
| **0** | Bruit -> non memorise | politesses, logs, phrases sans enjeu |

---

## 4. Recapitulatif des fichiers touches (periode)

| Fichier | Chantier | Etat Git |
|---|---|---|
| `backend/server.py` | Mode Autonomie + Fetcher | `M` (modifie, non committe pour le Fetcher) |
| `frontend/src/pages/Chat.jsx` | Mode Autonomie + Fetcher | `M` (modifie, non committe pour le Fetcher) |
| `upgrade.sh` | Fix check Git | committe (`4c4e73d`) |
| `REPRISE_MODE_AUTONOME.md` | Doc Mode Autonomie | committe (`84a6930`) |
| `FETCHER_CONTEXTE.md` | Doc Fetcher | non suivi |
| `FONCTIONS_AJOUTEES.md` | **Ce journal** | non suivi |
