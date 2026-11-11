# Amélioration — Rendu en blocs façon Emergent (fin de tour)

> Statut : **idée à instruire** (non planifiée, non exécutée). Consignée à la demande de l'utilisateur, à traiter plus tard, séparément du bug timeout.
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

### 2. `[A]` Garantir la continuité de clôture sans reload markdown
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

En attente du choix de périmètre (A ou B) par l'utilisateur. Aucun code modifié à ce stade — cartographie et plan seulement.
