# Amélioration — Rendu en blocs façon Emergent (fin de tour)

> Statut : **Phase A exécutée côté code et tests automatisés** ; validation visuelle finale après déploiement prod.
> Créé le : 2026-11-07

## Contexte

Pendant la génération, le frontend affiche déjà le contenu **bloc par bloc** de manière incrémentale (via `streamStore`, flux SSE). Le ressenti « temps réel » est correct en cours de tour.

## Problème identifié

À la **fin du tour**, il y a une bascule :
- Pendant le tour : rendu incrémental depuis `streamStore` (granularité fine, bloc par bloc).
- À la clôture : rechargement depuis la BDD, le message devient un **markdown monolithique** rendu d'un coup.

Cette bascule fait perdre la granularité visuelle « blocs » obtenue pendant le streaming. Le rendu final n'a pas la même structure progressive que le style observé chez Emergent (enchaînement de blocs distincts : réflexion, action outil, résultat, texte…).

## Objectif

Conserver, après la fin du tour, une présentation **structurée en blocs** cohérente avec ce qui a été streamé, au lieu d'un bloc markdown unique rechargé depuis la BDD.

## Pistes (à valider, non tranchées)

1. Persister la **structure en blocs** du tour (et pas seulement le markdown final concaténé) afin que le rechargement BDD restitue les mêmes blocs que le streaming.
2. Au moment de la clôture, **fusionner** l'état `streamStore` dans l'historique sans écraser la granularité (éviter le « reload markdown monolithique »).
3. Composant d'affichage capable de rendre une **liste de blocs typés** (texte / appel outil / sortie outil / réflexion) de façon identique en live et en relecture.

## Dépendances / précautions

- **Ne pas** mélanger ce chantier avec le correctif du bug `timeout of 60000ms exceeded` (sujet distinct).
- Impact probable sur le schéma de persistance des messages (BDD) → à cadrer avant toute écriture.
- Vérifier la compatibilité avec la mémoïsation du rendu markdown prévue par ailleurs.

## Décision

En attente. « On verra ensuite » (utilisateur, 2026-11-07).

---

# Instruction du plan (cartographie réelle du code)

> Ajouté le : 2026-11-07 — suite à inspection du code (aucune modification de code effectuée).
> Cette section complète l'idée ci-dessus sans la réécrire : elle confronte l'hypothèse à l'état réel du code et établit les tâches.

## Cartographie (état réel vs hypothèse)

| Élément | Rôle | Constat |
|---|---|---|
| `lib/streamStore.js` | État live du tour (`text`, `tools`, `steps`) | Granularité fine en mémoire, **jamais persistée telle quelle** |
| `components/StreamingBubble.jsx` | Seul abonné au store, construit un objet `message` | Réutilise **`ChatMessage`** pour le rendu |
| `components/ChatMessage.jsx` | Rendu d'un message | **Rend déjà les blocs** : accordéons `steps`, `tool_steps`, puis markdown. Identique en live et en relecture |
| `pages/Chat.jsx` (l. 979-986) | Clôture SSE `done`/`finished` → push message BDD + `resetStream()` | **Point de bascule** live → objet BDD |
| `server.py` `_persist_step` / `_ensure_assistant_doc` | Persistance | `steps` et `tool_steps` **structurés sont déjà stockés en BDD** |
| `server.py` listing `/messages` (l. 2806-2826) | Allègement payload | Renvoie `steps_summary`/`tool_steps_summary` ; détail via `/messages/{id}/details` |

### Écart réel vs hypothèse initiale
L'idée ci-dessus suppose un « markdown monolithique rechargé ». En réalité la structure en blocs **existe déjà** des deux côtés (`ChatMessage` est le composant commun). Les vrais écarts sont :
1. **Ordre figé** : les blocs d'étapes/outils sont toujours rendus **avant** le texte markdown, alors que chez Emergent texte et actions **s'intercalent** chronologiquement.
2. **Texte non segmenté** : le `content` persisté est un seul markdown concaténé ; la granularité « bloc par bloc » du texte streamé est perdue.
3. **Micro-bascule visuelle** au `done` (remplacement de `StreamingBubble` par le message BDD).

## Deux niveaux de périmètre (à trancher avant exécution)

- **Niveau A (léger, sans migration BDD)** : supprimer la micro-bascule visuelle et garantir que le rendu post-clôture est pixel-identique au live. Pas de changement de schéma.
- **Niveau B (structurel, avec migration BDD)** : persister une **liste de blocs typés ordonnés** (texte / réflexion / appel outil / sortie) pour intercaler texte et actions comme Emergent. Impact schéma messages + fallback rétrocompatible obligatoire.

## Tâches

### 1. `[A+B]` Reproduire la micro-bascule et poser le point de référence visuel
- **Objectif** : documenter ce qui saute à l'œil entre `StreamingBubble` et le message BDD au `done`.
- **Fichiers** : `Chat.jsx` (l. 979-986), `StreamingBubble.jsx`, `ChatMessage.jsx`.
- **Vérification** : capture avant/après `done` sur un tour avec étapes + texte.
- **Critère de fin** : liste écrite des différences réelles (ordre, espacement, caret).
- **Non vérifiable en lecture seule** : le ressenti visuel exact ; nécessite la preview lancée.
- **Détail / livrable** : sous-fichier module [`etape-1-micro-bascule.md`](./etape-1-micro-bascule.md) (analyse de code faite ✅ ; grille de capture à remplir une fois la preview lancée).

### 2. `[A]` Garantir la continuité de clôture sans reload markdown
- **Détail / livrable** : sous-fichier module [`etape-2-continuite-cloture.md`](./etape-2-continuite-cloture.md) (correctif C1 appliqué ✅, build OK ; capture visuelle preview à confirmer).
- **Objectif** : à `done`, remplacer `StreamingBubble` par le message BDD **sans reflow visible**.
- **Fichiers** : `Chat.jsx` (handler `done`).
- **Dépendance** : étape 1.
- **Vérification** : un tour complet ne doit produire aucun saut de mise en page à la clôture.
- **Critère de fin** : transition imperceptible sur un tour avec étapes.

### 3. `[B]` Définir le schéma « blocs ordonnés » (cadrage, pas d'écriture)
- **Objectif** : spécifier une structure `blocks[]` typée (`text` / `thinking` / `tool_call` / `tool_output`) avec ordre chronologique, persistée à côté de `content`.
- **Fichiers (lecture)** : `server.py` `_ensure_assistant_doc`, `_persist_step`, listing l. 2806-2826, `/messages/{id}/details`.
- **Vérification** : revue du schéma + compatibilité ascendante (anciens messages sans `blocks[]`).
- **Critère de fin** : schéma validé + stratégie de rétrocompatibilité écrite.
- **Risque** : migration de données — fallback obligatoire (étape 7).

### 4. `[B]` Alimenter `blocks[]` côté backend pendant le tour
- **Objectif** : enregistrer l'ordre d'arrivée (texte entre deux étapes, étapes, sorties) dans `blocks[]`.
- **Fichiers** : `server.py` (`_persist_step`, point de clôture du tour, émission SSE si besoin d'un champ d'ordre).
- **Dépendance** : étape 3.
- **Vérification** : `python3 -m pytest backend/tests/test_tool_lifecycle.py backend/tests/test_action_chain.py` (depuis la racine).
- **Critère de fin** : un message terminé contient un `blocks[]` ordonné cohérent avec `content`+`steps`.

### 5. `[B]` Exposer `blocks[]` au listing et au détail sans alourdir le payload
- **Objectif** : résumé ordonné dans le listing, détail complet via `/messages/{id}/details`.
- **Fichiers** : `server.py` (listing l. 2806-2826, endpoint details l. 5133).
- **Dépendance** : étape 4.
- **Vérification** : taille du payload listing inchangée à l'ordre de grandeur ; détail complet au clic.
- **Critère de fin** : listing léger, détail complet au chargement paresseux.

### 6. `[B]` Composant de rendu « liste de blocs typés » unifié
- **Objectif** : rendu parcourant `blocks[]` dans l'ordre, intercalant texte et actions — utilisé à l'identique en live et en relecture.
- **Fichiers** : `ChatMessage.jsx` (ou nouveau `MessageBlocks.jsx`), `StreamingBubble.jsx` pour alimenter la même structure depuis `streamStore`.
- **Dépendance** : étapes 2 et 5.
- **Vérification** : un même tour en live et après reload doit être structurellement identique.
- **Précaution** : conserver la mémoïsation (`React.memo` l. 548 de `ChatMessage`) — ne pas régresser la perf de streaming (règle connue : fenêtre de chat lente).
- **Critère de fin** : parité visuelle live/relecture sur un tour multi-étapes.

#### Découpage de l'étape 6 en sous-étapes (Phase B)

> Daté : 2026-11-12
> **Motif** : l'étape 6 en un seul passage est trop lourde (câblage relecture **et** live + build + parité) et épuise le budget d'outils avant d'aboutir. Elle est découpée en 5 sous-étapes atomiques, chacune vérifiable et commitable seule. Ordre **linéaire strict** : `6.1 → 6.2 → 6.3 → 6.4 → 6.5`.

**Cartographie du code concerné**
| Fichier | Rôle | État au découpage |
|---|---|---|
| `frontend/src/components/MarkdownBody.jsx` | Rendu markdown partagé (GFM, code copiable, images, tables) | Créé, non vérifié (build), non commité |
| `frontend/src/components/MessageBlocks.jsx` | Rendu « liste de blocs typés » (`text`/`thinking`/`tool_call`/`tool_output`, tri par `seq`) | Créé, non vérifié, pas de caret de streaming |
| `frontend/src/components/ChatMessage.jsx` | Branche conditionnelle `blocks[]` vs legacy | Modifié, build non vérifié, mélange étape 6+7 à nettoyer |
| `frontend/src/store/streamStore.js` | État live (`text`/`tools`/`steps`) | **Ne construit pas `blocks[]`** — cœur du travail restant |
| `frontend/src/hooks/useStreamingMessage.js` | Expose le message live à `ChatMessage` | N'expose pas `blocks` → live tombe dans le legacy |
| `frontend/src/components/LiveMessage.jsx` | Rend le `<ChatMessage/>` live | Transparent, pas de changement requis |

**Diagnostic** : après reload, un tour s'affiche via `MessageBlocks` (grâce aux étapes 4-5) ; **en live**, il s'affiche encore via l'ancien chemin. La parité live/relecture — objectif n°1 de l'étape 6 — **n'est pas atteinte** tant que le store ne reconstruit pas `blocks[]` côté client.

---

##### 6.1 — Verrouiller la base visuelle partagée (`MarkdownBody`)
- **Objectif** : valider que l'extraction du rendu markdown vers `MarkdownBody.jsx` ne régresse pas l'affichage existant, et stabiliser le comparateur `React.memo` de `ChatMessage` pour qu'il prenne `message.blocks` en compte.
- **Fichiers** : `frontend/src/components/MarkdownBody.jsx` (déjà créé), `frontend/src/components/ChatMessage.jsx` (import + comparateur `React.memo`).
- **Vérification** : `cd frontend && npx vite build` passe ; `MdCodeBlock`, `img`, `table` présents dans `MarkdownBody`.
- **Critère de fin** : build vert, aucun import cassé, comparateur mémo couvre `blocks`.
- **Risque** : nul (extraction + build).

##### 6.2 — Figer `MessageBlocks` comme rendu de relecture
- **Objectif** : rendre `blocks[]` dans l'ordre `seq` pour un message **persisté** (relecture uniquement), et préparer le caret de streaming optionnel (prop `streaming`) sans encore toucher au live.
- **Fichiers** : `frontend/src/components/MessageBlocks.jsx`.
- **Dépendance** : 6.1.
- **Vérification** : build vert ; relecture d'une conversation récente (déjà dotée de `blocks[]`) → ordre texte/outil correct.
- **Critère de fin** : relecture structurellement correcte d'un tour multi-étapes.

##### 6.3 — Construire `blocks[]` live dans `streamStore` (cœur)
- **Objectif** : ajouter au store un `blocks[]` ordonné (`seq` monotone), coalescence des deltas texte en bloc `text`, création d'une paire `tool_call`/`tool_output` à chaque outil — **miroir exact** de la logique backend de l'étape 4 (`_run_generation`).
- **Fichiers** : `frontend/src/store/streamStore.js` (état `blocks`, mutations `setStreamText`/`appendStreamText`/`addStreamTool`/`reset`).
- **Dépendance** : 6.2.
- **Vérification** : test front (Vitest) simulant `delta → tool → delta` → `blocks` conformes au schéma §2 ; invariant `Σ blocks(text).text == content`.
- **Critère de fin** : `blocks[]` live cohérent avec le schéma, sans casser le double stockage `steps`/`tools` existant.
- **Risque R1** : couplage avec `addStreamTool` (rattache déjà l'outil à `steps`) — ne pas le régresser.

##### 6.4 — Alimenter le message live avec `blocks[]`
- **Objectif** : `useStreamingMessage` expose `message.blocks = state.blocks` → la bulle live emprunte **le même** `MessageBlocks` que la relecture ; caret de streaming sur le dernier bloc texte.
- **Fichiers** : `frontend/src/hooks/useStreamingMessage.js`, `frontend/src/components/MessageBlocks.jsx` (prop `streaming`).
- **Dépendance** : 6.3.
- **Vérification** : build vert ; relecture du chemin ; test du hook si faisable.
- **Critère de fin** : live et relecture passent par le même composant.
- **Risque R2** : perf — l'ajout de `blocks` ne doit pas multiplier les `emit()` ni casser `React.memo` (règle connue : fenêtre de chat lente).

##### 6.5 — Isoler l'étape 6 de l'étape 7 et valider la parité
- **Objectif** : nettoyer la branche conditionnelle de `ChatMessage` pour qu'elle relève strictement de l'étape 6 (choix blocs vs legacy), en laissant le **fallback complet** à l'étape 7 ; confirmer la parité visuelle.
- **Fichiers** : `frontend/src/components/ChatMessage.jsx`.
- **Dépendance** : 6.4.
- **Vérification** : `npx vite build` vert ; mémoïsation intacte ; parité live/relecture sur un tour multi-étapes.
- **Critère de fin** : parité visuelle live/relecture.
- **Risque R3 (non vérifiable en lecture seule)** : la parité visuelle réelle (ordre, caret, absence de flash au `done`) ne se valide qu'au navigateur en preview, pas au build. À confirmer manuellement après déploiement.

### 7. `[A+B]` Fallback rétrocompatible pour les anciens messages
- **Objectif** : les messages **sans** `blocks[]` continuent de s'afficher via l'ancien chemin (`content` + `steps` + `tool_steps`).
- **Fichiers** : `ChatMessage.jsx` (branche conditionnelle).
- **Dépendance** : étape 6.
- **Vérification** : ouverture d'une ancienne conversation → aucun blanc, aucun crash.
- **Critère de fin** : anciennes et nouvelles conversations s'affichent correctement.

### 8. `[A+B]` Non-régression globale
- **Objectif** : vérifier qu'aucune régression n'est introduite (streaming, perf, TTS, copie de blocs, rollback d'étape).
- **Vérification** :
  ```bash
  python3 -m pytest backend/tests/test_tool_lifecycle.py backend/tests/test_action_chain.py backend/tests/test_sse_finish.py backend/tests/test_sse_replay.py
  ```
  (depuis la **racine** du projet, jamais `cd backend` — sinon imports `backend.*` cassés).
- **Critère de fin** : suite verte + vérification manuelle d'un tour complet en preview.

## Ordre conseillé

**Faire d'abord le Niveau A (gain visuel immédiat, risque nul, sans BDD), valider en preview, puis décider si le Niveau B vaut l'effort.**

1. **Phase A (prioritaire, autonome)** : `1 → 2 → 8`
   - Rapide, aucun changement de schéma, réversible. Supprime la micro-bascule de clôture.
   - Point de validation utilisateur à la fin : « est-ce suffisant ? »
2. **Phase B (structurelle, seulement si A ne suffit pas)** : `3 → 4 → 5 → 6 → 7 → 8`
   - `3` est un cadrage à valider **avant** toute écriture BDD.
   - `7` (fallback) est **non négociable** si `3`–`6` sont faits : sans lui, les anciennes conversations cassent.
   - `8` reclôt la non-régression après la phase B.

**Chemin critique complet (si A+B)** : `1 → 2 → 3 → 4 → 5 → 6 → 7 → 8`.
**Jalons de décision** : après l'étape 2 (A suffit-il ?) et après l'étape 3 (valider le schéma avant d'écrire).

## Décision (mise à jour)

Phase A (`1 → 2 → 8`) réalisée. Phase B reste en attente d'une décision après validation visuelle en production.

---

## Étape 8 — Non-régression globale (Phase A) : exécutée ✅

> Daté : 2026-11-11

**Vérifications lancées depuis la racine du projet (jamais `cd backend`).**

- Backend — suite de non-régression ciblée :
  ```
  backend/.venv/bin/python -m pytest \
    backend/tests/test_tool_lifecycle.py \
    backend/tests/test_action_chain.py \
    backend/tests/test_sse_finish.py \
    backend/tests/test_sse_replay.py
  ```
  **Résultat : 35 passed, 0 failed** (streaming, cycle d'outils, chaîne d'actions, SSE finish/replay).

- Frontend — build de production :
  ```
  (cd frontend && npx vite build)
  ```
  **Résultat : build réussi en 31,84s**, 2037 modules transformés, chunk `Chat-xAFVKVqV.js` généré, aucune erreur.

**Critère de fin Phase A** : suite verte + build OK ✅. Reste la **vérification manuelle d'un tour complet en preview** — à faire **après déploiement prod** (la preview ne contient aucun workspace), par l'utilisateur.

### Statut Phase A : `1 → 2 → 8` terminée côté code et tests automatisés ✅
- Validation visuelle finale déléguée à l'utilisateur après déploiement (GitHub → `sudo ./upgrade.sh` en prod).
- Décision Phase B (structurelle, migration BDD) : **en attente** du retour utilisateur « A suffit-il ? ».

---

## Phase B — Étape 3 (cadrage schéma `blocs[]`) : exécutée ✅

> Daté : 2026-11-11

Cadrage livré dans **`etape-3-schema-blocs-ordonnes.md`** (ce dossier). Aucune
écriture de code (consigne « pas d'écriture » respectée).

- Schéma `blocks[]` typé/ordonné spécifié : `text` / `thinking` / `tool_call` / `tool_output`, clé `seq` chronologique, `step_index` de liaison.
- Persisté **à côté** de `content` (jamais en remplacement) ; `content` reste source markdown.
- Rétrocompatibilité écrite : fallback **binaire** (`blocks` vide/absent → ancien chemin), **aucun backfill**, aucune migration imposée.
- Invariant posé : `content == concat(blocks.type=="text")` (à vérifier étape 4).

**Jalon de décision** : valider ce schéma avant l'étape 4 (première écriture BDD).
Prochaine étape : **4** (alimenter `blocks[]` côté backend) — en attente de feu vert.

---

## Phase B — Étape 4 (alimenter `blocks[]` côté backend) : exécutée ✅

> Daté : 2026-11-12

Implémentation backend conforme au contrat [`etape-3-schema-blocs-ordonnes.md`](./etape-3-schema-blocs-ordonnes.md) :

- **`_ensure_assistant_doc`** (`backend/server.py`) : ajoute `"blocks": []` au document initial (à côté de `content`, `steps`, `tool_steps`).
- **Helpers de blocs** (`backend/server.py`) :
  - `_block_exit_code(output)` : extrait l'`exit_code` d'une sortie `bash` via `_BASH_EXIT_RE`.
  - `_make_tool_blocks(seq_call, seq_out, tool_step, step_index)` : construit la paire ordonnée `tool_call` + `tool_output` (sortie tronquée à `STEP_LOG_TRUNC` = 4000 car., champ `truncated`, `exit_code` optionnel, `step_index` de liaison).
  - `_push_blocks(database, message_id, blocks)` : persistance incrémentale via `$push: {"blocks": {"$each": blocks}}`.
- **Boucle `_run_generation`** :
  - Compteur `_block_seq` strictement croissant dans la closure du tour.
  - Coalescence des deltas de texte consécutifs dans un tampon `_text_block` (`type: "text"`), flushé en BDD dès qu'un outil arrive, en fin de flux (`done`), ou en sortie anticipée (`CancelledError` / `Exception`).
  - Garantit la création de `assist_doc` dès le premier `delta` (même si aucun événement `start` n'a précédé) pour qu'aucun `_flush_text_block` ne soit perdu.
- **Tests autonomes** (`backend/tests/test_ordered_blocks.py`, 5 tests) :
  - Schéma initial `blocks: []`.
  - Paire `tool_call` + `tool_output` (`exit_code`, `step_index`, troncature `STEP_LOG_TRUNC`).
  - Tour complet intercalé `text → tool_call → tool_output → text → tool_call → tool_output → text`.
  - **Invariant §2.4 vérifié** : `content == "".join(b["text"] for b in blocks if b["type"] == "text")` et `seq == [0..N-1]`.
  - Cas limite flux texte seul sans événement `start`.
- **Vérification** (depuis la racine) :
  ```
  backend/.venv/bin/python -m pytest \
    backend/tests/test_ordered_blocks.py \
    backend/tests/test_tool_lifecycle.py \
    backend/tests/test_action_chain.py \
    backend/tests/test_sse_finish.py \
    backend/tests/test_sse_replay.py
  ```
  **Résultat : 40 passed in 2.75s**.

**Prochaine étape** : **5** (exposer `blocks[]` au détail `/messages/{id}/details` et alléger le listing `/messages` avec `blocks_count` sans alourdir le payload).

---

## Phase B — Étape 5 (exposer `blocks[]` au détail, alléger le listing) : exécutée ✅

> Daté : 2026-11-12

- **`_build_messages_pipeline(flt, limit)`** (`backend/server.py`) : nouveau pipeline d'agrégation pour le listing `/conversations/{conv_id}/messages`.
  - `$match` / `$sort created_at:-1` / `$limit` (même ordre qu'avant).
  - `$set: blocks_count = $size($ifNull["$blocks", []])`.
  - `$unset` de `blocks`, `image_b64`, `file_text`, `images_b64`, `prompt_override`, `_id`.
- **`get_messages`** : remplace le `find` par `database.messages.aggregate(...)`. L'allègement des sorties d'outils (`tool_steps.output` / `steps.tools.output`) reste fait en Python après coup, identique à avant.
- **`get_message_details`** : renvoyait déjà le document entier, donc `blocks[]` est bien exposé au détail. Docstring précisée.
- **Payload listing inchangé en poids** : `blocks[]` n'est plus embarqué ; seul `blocks_count` (entier) est ajouté.
- **Tests** (`backend/tests/test_ordered_blocks.py`, +2 tests) :
  - pipeline expose `blocks_count` et n'expose jamais `blocks[]` ;
  - sémantique réelle : `blocks_count == len(blocks)` (2), ancien message sans blocs → `blocks_count == 0`, champs lourds retirés.
- **Vérification** :
  ```
  backend/.venv/bin/python -m pytest backend/tests/test_ordered_blocks.py \
    backend/tests/test_tool_lifecycle.py backend/tests/test_action_chain.py \
    backend/tests/test_sse_finish.py backend/tests/test_sse_replay.py
  ```
  **Résultat : 42 passed in 2.68s**.

**Prochaine étape** : **6** (rendu front des blocs ordonnés, avec mémoïsation — en attente de feu vert).


---

## Phase B — Étape 7 (fallback rétrocompatible pour anciens messages) : exécutée ✅

> Daté : 2026-11-14

Clôture de l'étape 7 en trois phases successives, sur le modèle de l'étape 6.

### 7.1 — Étendre l'auto-fetch des détails aux anciens messages porteurs d'outils
- **Fichier** : `frontend/src/components/ChatMessage.jsx` (`useEffect` auto-fetch).
- **Problème adressé** : l'auto-fetch des détails ne se déclenchait que si `blocks_count > 0`. Les anciens messages persistés **avant** l'étape 4 n'ont pas de `blocks[]` mais peuvent porter des outils (`tool_steps_count` / `steps_count`). Résultat : `buildSyntheticBlocks` était appelé avec les `tool_steps` du listing — dont les `output` sont volontairement nullifiés par l'allègement payload — et le fallback affichait les outils **sans leur sortie** jusqu'à ouverture manuelle d'un accordéon.
- **Correctif** : condition élargie
  ```
  const isLegacyWithTools = !hasRealBlocks && (
    (message.tool_steps_count ?? 0) > 0 ||
    (message.steps_count ?? 0) > 0 ||
    (Array.isArray(message.tool_steps) && message.tool_steps.length > 0) ||
    (Array.isArray(message.steps) && message.steps.length > 0)
  );
  const needsDetails = hasRealBlocks || isLegacyWithTools;
  ```
  + dépendances `useEffect` enrichies (`tool_steps`, `tool_steps_count`, `steps`, `steps_count`).
- **Critère de fin** : un vieux message avec outils déclenche `GET /messages/{id}/details` au premier rendu → `buildSyntheticBlocks` reçoit des `tool_steps` avec `output` complet → fallback visuellement complet dès l'ouverture de la conversation.

### 7.2 — Durcir les cas limites de l'arbre conditionnel
- **Fichier** : `frontend/src/components/ChatMessage.jsx` (bloc de rendu, ex-commentaire « étape 6.5 »).
- **Changements** :
  - Commentaire ré-attribué à l'étape 7 et clarifié : 3 branches ordonnées documentées (vrais blocs / blocs synthétiques / markdown direct).
  - Branche 3 (markdown direct) gardée par `(message.content || message.streaming)` au lieu d'être inconditionnelle : un message **totalement vide** (ni content, ni outils, ni blocs — ex. rollback ou état transitoire) ne rend plus un cadre markdown fantôme.
  - Ajout d'une branche `: null` finale pour fermer proprement le ternaire JSX.
- **Critère de fin** : aucun cadre vide rendu pour un message sans contenu utile, aucune régression sur les 3 chemins classiques.

### 7.3 — Tests de non-régression ciblés étape 7
- **Fichier** : `frontend/src/components/buildSyntheticBlocks.test.mjs`.
- **Scénarios ajoutés (préfixe `E7.*`)** :
  - `E7.1` — ancien message, `content` vide + outil `output: null` (listing non détaillé) → ordre `tool_call` + `tool_output`, `output == ""`, pas d'`exit_code` parasite.
  - `E7.2` — mix listing/détail partiel : 2 outils dont l'un avec `output: undefined` → 5 blocs stables, `exit_code` du premier préservé.
  - `E7.3` — message totalement vide (`content: null`, `toolSteps: null`) → `[]`. Idem pour `{}` / `undefined`, idem pour `message: null`.
  - `E7.4` — défensif : `toolSteps` non-array → outils ignorés, texte conservé.
  - `E7.5` — invariant `seq` strictement monotone sur mix complexe de 7 blocs, `step_index` correctement liés (0,1,2 pour `tool_call` comme pour `tool_output`), `exit_code` au milieu préservé.
- **Résultat** : **32 assertions OK, 0 KO**.

### Vérifications passées (depuis la racine du projet)
- ESLint `src` : 0 erreur, 0 warning.
- `node frontend/src/components/buildSyntheticBlocks.test.mjs` : **32 OK / 0 KO**.
- `node frontend/src/lib/streamStore.test.mjs` : OK (tous les cas passent).
- `(cd frontend && npx vite build)` : **2040 modules transformés, build en 32,12s**, aucun warning bloquant.
- Backend non-régression :
  ```
  backend/.venv/bin/python -m pytest     backend/tests/test_ordered_blocks.py     backend/tests/test_tool_lifecycle.py     backend/tests/test_action_chain.py     backend/tests/test_sse_finish.py     backend/tests/test_sse_replay.py
  ```
  **Résultat : 42 passed in 4.23s**.

### Statut
- Étape 7 close ✅. Les anciennes conversations (Test 5 déjà validé par l'utilisateur) **affichent désormais leurs outils AVEC leurs sorties** dès l'ouverture, sans action manuelle sur un accordéon.
- **Chemin critique restant** : étape 8 (non-régression globale finale + validation manuelle en prod).
