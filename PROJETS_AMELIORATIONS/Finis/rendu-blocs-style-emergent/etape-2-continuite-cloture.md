# Étape 2 — Continuité de clôture sans remount (Phase A)

> Sous-fichier module rattaché à `rendu-blocs-style-emergent.md` (plan principal)
> et faisant suite à `etape-1-micro-bascule.md` (diagnostic C1–C4).
>
> Périmètre : **Phase A**, visuel uniquement. Aucune touche au flux SSE ni à la
> persistance BDD.

## Objectif
Supprimer la micro-bascule visuelle au `done` : le passage de la bulle live au
message persisté ne doit plus produire de flash ni de reflow.

## Cause traitée
**C1 (dominante)** — remount dû à un changement de type/clé au dernier slot :
pendant la génération, un composant distinct rendait le message en cours ; à la
clôture, un `ChatMessage` d'id BDD le remplaçait → React démontait/remontait.

## Changements appliqués

### 1. Hook module `frontend/src/hooks/useStreamingMessage.js` (nouveau)
- Déporte hors de `Chat.jsx` la construction du **message live** depuis le store
  (`streamStore`) — évite la duplication et les fichiers à rallonge.
- Exporte la **clé stable** `STREAMING_SLOT_KEY = "streaming-slot"`, identité de
  l'emplacement du message en cours, réutilisée à la clôture.

### 2. Composant `frontend/src/components/LiveMessage.jsx` (nouveau)
- Unique abonné au store (préserve l'optimisation perf : la page `Chat()` ne se
  re-rend pas à chaque token).
- Rend **le même composant `ChatMessage`** que les messages persistés, avec la
  même forme de props.
- Remplace l'ancien `StreamingBubble.jsx` (supprimé).

### 3. `frontend/src/pages/Chat.jsx`
- Import : `StreamingBubble` → `LiveMessage` + `STREAMING_SLOT_KEY`.
- Bulle live rendue avec `key={STREAMING_SLOT_KEY}`.
- Dernier message assistant BDD rendu avec la **même clé stable** :
  `key={m.id === lastAssistantId ? STREAMING_SLOT_KEY : m.id}`.
- Conséquence : même clé au même emplacement avant/après `done` → React
  réconcilie le slot au lieu de le remonter.

### 4. Nettoyage
- `StreamingBubble.jsx` supprimé (plus aucune référence).
- Commentaires de `streamStore.js` et du hook mis à jour (`<LiveMessage/>`).

## Vérifications

### Faites
- **Build Vite** : `node_modules/.bin/vite build` → **OK**, 2037 modules
  transformés, aucune erreur (lancé avec un HOME local au projet pour contourner
  l'absence de droits sur la config yarn globale, puis HOME temporaire nettoyé).
- Relecture du diff : logique SSE (`done`/`finished`), persistance et codes
  inchangés ; mémoïsation `ChatMessage` préservée.

### Restant (non vérifiable en lecture seule)
- **Capture visuelle avant/après `done`** : exige la preview lancée et accessible.
  Grille à remplir :

  | Aspect | Avant correctif | Après correctif (attendu) |
  |--------|-----------------|---------------------------|
  | Flash / reflow du bloc au `done` | présent | absent |
  | Position verticale du message | peut sauter | stable |
  | Accordéons étapes/outils | peuvent se replier/rouvrir | état conservé |
  | Caret / typing-dots | disparition visible | transition douce |

## Critère de fin
Transition imperceptible à la clôture d'un tour comportant étapes + texte,
confirmée visuellement en preview.
