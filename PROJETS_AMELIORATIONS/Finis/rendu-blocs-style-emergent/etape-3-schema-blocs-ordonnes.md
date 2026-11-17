# Étape 3 — `[B]` Schéma « blocs ordonnés » (cadrage, sans écriture)

> Daté : 2026-11-11
> Nature : **cadrage uniquement**. Aucune modification de code. Spécifie le schéma
> `blocks[]`, sa place dans le document message, et la stratégie de rétrocompatibilité.
> Les étapes 4–7 implémenteront ce cadrage.

## 1. Modèle de données réel (constaté dans `server.py`)

Document message assistant créé par `_ensure_assistant_doc` (l. 6108) :

```jsonc
{
  "id": "uuid",
  "conversation_id": "uuid",
  "role": "assistant",
  "content": "",            // markdown final concaténé (chunks de texte)
  "has_image": false,
  "tool_steps": [],          // liste PLATE de tous les outils (tronqués)
  "steps": [],               // liste ORDONNÉE des étapes (voir ci-dessous)
  "provider": null,
  "model": null,
  "requested_provider": null,
  "fallback_used": false,
  "routing": [],
  "stopped": false,
  "status": "running",
  "completed_at": null,
  "created_at": "iso"
}
```

Une étape (`_new_step`, l. 6095), poussée par `_persist_step` (l. 6133) :

```jsonc
{
  "index": 0,
  "intention": "texte d'intention de l'étape",
  "tools": [ { "tool": "...", "input": {...}, "output": "..." } ],
  "status": "done",
  "action": "coding",        // Brique D (type d'action figé à la clôture)
  "action_reason": "",
  "created_at": "iso",
  "finished_at": "iso"
}
```

### Limite constatée (pourquoi `steps[]` ne suffit pas)

`content` (markdown global) et `steps[]` (étapes + outils) sont **deux canaux
parallèles** : il n'existe **aucun ordre chronologique commun** entre un paragraphe
de texte et une salve d'outils. À la relecture, on ne peut donc **pas** reconstituer
l'enchaînement réel « texte → outil → texte → outil » façon Emergent. C'est
exactement la granularité perdue au reload (problème du chantier).

## 2. Schéma cible : `blocks[]`

Nouveau champ **ordonné** persisté **à côté** de `content` (jamais en remplacement).
`content` reste la source de vérité markdown (compat, recherche, exports).

```jsonc
"blocks": [
  { "type": "text",        "seq": 0, "text": "paragraphe markdown" },
  { "type": "thinking",    "seq": 1, "text": "réflexion éventuelle" },
  { "type": "tool_call",   "seq": 2, "tool": "bash", "input": { "command": "..." }, "step_index": 0 },
  { "type": "tool_output", "seq": 3, "tool": "bash", "output": "...", "exit_code": 0, "truncated": true, "step_index": 0 },
  { "type": "text",        "seq": 4, "text": "suite du texte après l'outil" }
]
```

### Types de blocs (fermé, versionnable)

| `type`        | Champs propres                                       | Source live (streamStore)        |
|---------------|------------------------------------------------------|----------------------------------|
| `text`        | `text` (markdown)                                    | chunks de texte                  |
| `thinking`    | `text`                                               | intention d'étape / réflexion    |
| `tool_call`   | `tool`, `input`, `step_index`                        | ouverture d'un outil             |
| `tool_output` | `tool`, `output`, `exit_code?`, `truncated`, `step_index` | résultat d'outil            |

### Règles d'invariant

1. `seq` est un entier **strictement croissant**, unique dans le message : c'est
   l'ordre chronologique réel d'arrivée (clé de tri unique côté rendu).
2. `step_index` (sur `tool_call` / `tool_output`) référence `steps[].index` :
   permet de relier un bloc à son étape sans dupliquer la donnée.
3. `tool_output.output` reste **tronqué** à la même limite que `tool_steps`
   (4000 car., cf. `_persist_step`) ; le détail complet reste servi par
   `GET /messages/{id}/details` (étape 5). Pas de doublon de payload lourd.
4. La concaténation des blocs `type:"text"` dans l'ordre `seq` **doit** être
   égale à `content` (invariant de cohérence, vérifié à l'étape 4).

## 3. Où et quand peupler `blocks[]` (cadrage pour l'étape 4)

- `_ensure_assistant_doc` : ajouter `"blocks": []` au document initial.
- Boucle de `_run_generation` : à chaque événement SSE déjà traité
  (`ev["text"]` → bloc `text` ; ouverture d'outil → `tool_call` ;
  fin d'outil → `tool_output`), **émettre aussi** le bloc correspondant avec le
  prochain `seq`. Un compteur `seq` monotone est tenu dans la closure du tour.
- Persistance : un `$push` sur `blocks` (même mécanisme que `steps`), ou un
  `update_one` de clôture réécrivant `blocks` complet. **Décision reportée à
  l'étape 4** (push incrémental privilégié pour la reprise de session).

## 4. Rétrocompatibilité (obligatoire — prérequis de l'étape 7)

- **Lecture** : un message **sans** `blocks` (ancien) ou avec `blocks == []`
  est traité comme « pas de blocs » → le front retombe sur l'ancien chemin
  (`content` + `steps` + `tool_steps`). Aucune migration de données imposée.
- **Pas de backfill** : on ne reconstruit pas `blocks[]` pour l'historique
  existant (impossible de récupérer l'ordre chronologique perdu). Les anciens
  messages restent affichés via le fallback (étape 7).
- **Détection côté front** : `Array.isArray(msg.blocks) && msg.blocks.length > 0`
  → nouveau rendu ; sinon → ancien rendu. Critère binaire, pas de heuristique.
- **Listing allégé** (étape 5) : le listing n'embarque **pas** `blocks` complet ;
  il expose au mieux `blocks_count`. Le détail complet est chargé paresseusement
  via `/messages/{id}/details`. Le payload du listing reste à l'identique.

## 5. Impact & risques

| Risque | Mitigation |
|---|---|
| Gonflement document BDD | `output` tronqué (4000), détail via endpoint ; `blocks` absent du listing |
| Désync `content` vs blocs `text` | invariant §2.4 vérifié par test (étape 4) |
| Anciens messages cassés | fallback binaire §4 (étape 7), aucun backfill |
| Perte perf streaming | `blocks` alimentés depuis les événements **déjà** parcourus (pas de double parsing) ; mémoïsation front conservée (étape 6) |

## 6. Critère de fin de l'étape 3 ✅

- [x] Schéma `blocks[]` typé et ordonné **spécifié** (types fermés, `seq`, `step_index`).
- [x] Place dans le document message définie (**à côté** de `content`, pas en remplacement).
- [x] Stratégie de rétrocompatibilité **écrite** : fallback binaire, aucun backfill, aucune migration imposée.
- [x] Invariant de cohérence `content == Σ blocks.text` posé (à tester à l'étape 4).
- [x] Aucune écriture de code effectuée (cadrage pur, conforme à la consigne « pas d'écriture »).

**Jalon de décision (plan)** : valider ce schéma **avant** toute écriture BDD (étape 4).
