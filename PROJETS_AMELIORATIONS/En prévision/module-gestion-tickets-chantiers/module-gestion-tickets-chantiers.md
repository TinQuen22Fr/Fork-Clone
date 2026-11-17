# Chantier — module-gestion-tickets-chantiers

> Idée d'origine : `idee-origine.md`
> État : **En prévision**
> Risque global : moyen (nouveau trust boundary d'écriture FS en Phase B)

---

## 1. Objectif

Vue kanban des chantiers (`PROJETS_AMELIORATIONS/`) accessible depuis le Header,
en lecture puis avec déplacement par drag & drop entre états.
**Le FS est la seule source de vérité — aucune base de données ajoutée.**

---

## 2. Modèle de données (dérivé du FS, zéro duplication)

```
PROJETS_AMELIORATIONS/
├── En prévision/<slug>.md ............... ticket "prévision" (fichier seul)
│   └── <slug>/ .......................... (ou dossier-chantier)
├── En cours/<slug>/<slug>.md ............ ticket "en cours" (dossier + plan)
│   └── etape-*.md ....................... étapes
└── Finis/<slug>/<slug>.md ............... ticket "fini"
    └── ameliorations-possibles/ ......... sous-tâches rouvrables
```

- **Ticket** = `{ slug, titre, etat, chemin, etapes[], ameliorations }`
- **État porté par le dossier parent** (prévision / en cours / finis). Pas de champ stocké.
- **Déplacer un ticket = `mv`** entre les 3 dossiers.
- **Titre** = 1ʳᵉ ligne `# ` du `.md`.

---

## 3. Cartographie de l'existant (réutilisé, non réécrit)

| Brique | Emplacement | Réutilisation |
|---|---|---|
| Routes workspace | `backend/server.py` l.7579+ | Pattern FS + auth `Depends(get_current_user)` à copier |
| Helpers FS | `_valid_project_name`, `_ensure_project_dir` (l.7332-7420) | Validation nom / dossier sécurisé |
| Header UI | `frontend/src/components/chat/ChatHeader.jsx` (zone boutons, `PreviewButton` l.65) | Point d'insertion bouton |
| Primitives UI | `frontend/src/components/ui/{sheet,dialog,card}.jsx` | shadcn déjà présent, aucune dépendance à ajouter |
| Page hôte | `frontend/src/pages/Chat.jsx` | Montage du panneau |

**Aucune dépendance npm/pip à installer.** DnD = HTML5 natif (`draggable` + `onDrop`).

---

## 4. Analyse d'impact

**Backend** : `backend/server.py` uniquement (bloc routes `/api/tickets/*`, ~80-120 l.).

**Frontend** :
- `frontend/src/components/chat/ChatHeader.jsx` — +1 bouton (~5 l.)
- `frontend/src/pages/Chat.jsx` — montage + état ouvert/fermé
- **NOUVEAU** `frontend/src/components/TicketsBoard.jsx` — vue kanban
- **NOUVEAU** `frontend/src/hooks/useTickets.js` — fetch + mutations

**Tests** : **NOUVEAU** `backend/tests/test_tickets.py`.

---

## 5. Risques

| # | Risque | Parade |
|---|---|---|
| R1 | Noms de dossiers à accents/espaces (`En prévision`, `Améliorations...`) → encodage URL | API route par état **logique** (`prevision`/`en-cours`/`finis`), mapping interne vers noms FS réels |
| R2 | Écriture FS depuis l'UI = nouveau trust boundary | Valider chaque slug via `_valid_project_name`, interdire `..`, confiner sous `PROJETS_AMELIORATIONS/`, jamais de chemin brut client |
| R3 | Tickets globaux vs routes workspace per-projet | Nouveau préfixe `/api/tickets`, pas de greffe sur `/api/workspace/projects` |
| R4 | Déplacement irréversible | Move only (pas de delete v1), `mv` atomique, confirmation UI avant bascule |
| R5 | Collision bouton Header mobile avec `PreviewButton` | Contrôle visuel prod (étape 5/7) |
| R6 | Course multi-onglets | Relecture FS après chaque mutation, UI reflète l'état réel renvoyé |

---

## 6. Étapes (atomiques, commitables)

### Étape 1 — API lecture `GET /api/tickets`
- **Fichier** : `backend/server.py`
- **Fait** : renvoie `{prevision:[], en_cours:[], finis:[]}`, chaque ticket avec `slug`, `titre`, `etat`, présence `ameliorations`.
- **Vérif** : `curl .../api/tickets` cohérent avec `ls` ; slug invalide ignoré.

### Étape 2 — API mutation `POST /api/tickets/{slug}/move` *(requiert 1)*
- **Fichier** : `backend/server.py`
- **Fait** : `mv` fichier/dossier entre états, crée le dossier cible si besoin, gère fichier-seul ↔ dossier.
- **Vérif** : move + `ls` confirme ; slug invalide → `400` ; hors workspace → refusé.

### Étape 3 — Hook `useTickets.js` *(requiert 1)*
- **Fichier** : `frontend/src/hooks/useTickets.js` (nouveau)
- **Fait** : fetch initial + `moveTicket()`, relecture auto après mutation.
- **Vérif** : données chargées sans erreur console.

### Étape 4 — Composant `TicketsBoard.jsx` *(requiert 3)*
- **Fichier** : `frontend/src/components/TicketsBoard.jsx` (nouveau)
- **Fait** : 3 colonnes dans un `Sheet`, carte = titre + état, clic → détail `.md`.
- **Vérif** : `npm run build` OK ; colonnes peuplées.

### Étape 5 — Bouton Header + montage *(requiert 4)*
- **Fichiers** : `ChatHeader.jsx`, `Chat.jsx`
- **Fait** : ouverture/fermeture panneau, bouton visible desktop + mobile (contrôle R5).
- **Vérif** : build OK ; contrôle visuel prod.

### Étape 6 — Drag & drop natif *(requiert 2 + 5)*
- **Fichier** : `TicketsBoard.jsx`
- **Fait** : glisser une carte entre colonnes → `move`, confirmation avant bascule (R4).
- **Vérif** : DnD d'un ticket test, `ls` confirme le `mv`.

### Étape 7 — Non-régression *(requiert toutes)*
- **Fichier** : `backend/tests/test_tickets.py` (nouveau) + lint/build front
- **Fait** : suite backend verte, `npm run build` OK, contrôle visuel prod (Header mobile, DnD).
- **Vérif** : tout vert → chantier déplaçable en `Finis/`.

---

## 7. Ordre d'exécution recommandé (phases livrables)

### Phase A — Lecture seule (déployable sans risque, zéro écriture FS)

```
1 → 3 → 4 → 5 → 7
```

| Ordre | Étape | Rôle |
|---|---|---|
| 1 | Étape 1 — `GET /api/tickets` | Fondation lecture |
| 2 | Étape 3 — hook `useTickets` | Branche front sur API |
| 3 | Étape 4 — `TicketsBoard` | Vue kanban lecture |
| 4 | Étape 5 — bouton Header | Accès + contrôle R5 mobile |
| 5 | Étape 7 — non-régression | Verrou de phase |

**Livrable A** : board consultable depuis le Header, lecture pure, déployable seul.
**Décision après A** : *la consultation suffit-elle ?* Si oui, Phase B optionnelle (on garde le `mv` manuel).

### Phase B — Écriture (déplacement drag & drop)

```
2 → 6 → 7
```

| Ordre | Étape | Rôle |
|---|---|---|
| 6 | Étape 2 — `POST /move` | Trust boundary d'écriture (R2/R4), isolé après lecture stable |
| 7 | Étape 6 — DnD natif → `move` | Branche UI sur écriture |
| 8 | Étape 7 — non-régression (re-run) | Revalide avec mutations actives |

**Livrable B** : déplacement souris, `mv` FS confirmé, confirmation avant bascule. Clôture du chantier.

### Chemin complet

```
Phase A : 1 → 3 → 4 → 5 → 7
Phase B : 2 → 6 → 7
```

**Justification** :
- **A ne touche jamais le FS en écriture** → déployable sans risque de déplacement accidentel.
- **B isole tout le risque R2/R4** dans une phase dédiée, rollback-able sans perdre le board.
- **Étape 7 jouée 2 fois** (fin A, fin B), comme l'étape 8 de `rendu-blocs`.
- **Ordre recommandé, pas imposé.**

---

## 8. Simplifications assumées

- **DnD HTML5 natif** (pas de lib) → *skipped : réordonnancement intra-colonne, add when priorités fines.*
- **Pas de delete** v1 (move only) → *skipped : suppression ticket, add when archivage nécessaire.*
- **Titre = 1ʳᵉ ligne `# `** (pas de front-matter) → *skipped : métadonnées structurées, add when filtrage/tri.*
- **État porté par le dossier** (pas de DB) → cohérent avec l'usage manuel, rien à migrer.

---

## 9. Points non vérifiables en lecture seule

- Rendu du bouton Header sous mobile (collision `PreviewButton`) → contrôle visuel prod (étape 5/7).
- Comportement `mv` sur dossiers à accents selon FS prod → test réel étape 2.

---

## 10. Journal de boucle

- **[2025-11-17] Intention** : matérialiser le plan de chantier dans `En prévision/`. **Friction** : aucune. **Suite** : attendre GO pour démarrer Phase A, étape 1.
