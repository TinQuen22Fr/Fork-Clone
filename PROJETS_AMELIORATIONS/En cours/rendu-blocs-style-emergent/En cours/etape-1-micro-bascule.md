# Étape 1 — Micro-bascule visuelle `StreamingBubble` → message BDD

> Sous-fichier module du plan [`rendu-blocs-style-emergent.md`](./rendu-blocs-style-emergent.md).
> Phase A · Étape 1. Objectif : **documenter** précisément ce qui saute à l'œil
> au moment du `done`. Aucune modification de code à cette étape.

## 1. Mécanique exacte de la bascule (analyse du code — factuel)

Le tour en cours et le tour clos sont rendus par **deux porteurs de données
différents**, mais **le même composant de rendu** (`ChatMessage`).

### Pendant le tour (`sending === true`)
- `frontend/src/pages/Chat.jsx` l. 1544-1550 : monte `<StreamingBubble>`.
- `frontend/src/components/StreamingBubble.jsx` : seul abonné à `streamStore`,
  construit un objet message **volatil** :
  ```js
  { id: "streaming", role: "assistant",
    content: state.text, tool_steps: state.tools, steps: state.steps,
    streaming: true }
  ```
  puis le passe à `<ChatMessage>`.

### À la clôture (`event === "done"`)
- `Chat.jsx` l. 978-981 :
  ```js
  } else if (event === "done") {
    setMessages((prev) => [...prev, data]); // objet message BDD
    resetStream();                           // vide le streamStore
    terminalRef.current = true;
  }
  ```
- Conséquence en un render : `StreamingBubble` **disparaît** (store vidé +
  `sending` repasse à `false`), et un **nouveau** `ChatMessage` apparaît dans la
  liste `messages`, alimenté cette fois par l'objet `data` de la BDD.

### Variante `finished` (Brique B)
- `Chat.jsx` l. 982-986 : `finished` ne pousse PAS de message, il fige + reset.
  Le message BDD arrive alors par un autre canal (listing/poll). Même effet
  visuel de substitution.

## 2. Pourquoi ça « saute » — causes structurelles identifiées

| # | Cause | Localisation | Effet visuel |
|---|-------|--------------|--------------|
| C1 | **Changement de porteur** : `message.id` passe de `"streaming"` à l'id BDD → React **démonte/remonte** le nœud au lieu de le réconcilier | `StreamingBubble.jsx` (id fixe) vs `Chat.jsx` l. 979 | Flash / reflow du bloc entier |
| C2 | **Deux objets message potentiellement non identiques** : `data` (BDD) peut avoir `steps`/`tool_steps` sérialisés différemment de `state.steps`/`state.tools` (live) | `done` l. 979 | Hauteur/ordre des accordéons peut bouger |
| C3 | **Indicateur de frappe** (`typing-dots`) conditionné par `streamingHasContent` | `Chat.jsx` l. 1551 | Apparition/disparition du bloc « points » |
| C4 | **`streaming: true` → absent** : tout rendu conditionné par ce flag (caret, état "en cours") bascule d'un coup | `StreamingBubble.jsx` vs message BDD | Disparition du caret / état actif |

## 3. Grille de capture (à remplir quand la preview tourne)

> **Non vérifiable en lecture seule.** Exige la preview de Fork-Clone lancée.
> Protocole : envoyer un message qui déclenche **au moins 1 étape + du texte**,
> capturer juste **avant** et juste **après** le `done`.

| Observation | Avant `done` | Après `done` | Diff réelle ? |
|-------------|--------------|--------------|---------------|
| Position verticale du bloc | | | |
| Espacement entre blocs étapes/texte | | | |
| Ordre étapes ↔ texte | | | |
| Présence du caret / état "en cours" | | | |
| Flash / clignotement visible | | | |
| Saut de la hauteur du message | | | |

## 4. Conclusion d'étape (préliminaire, à confirmer par capture)

Hypothèse principale : **C1 (changement d'`id` → remount)** est la cause
dominante du saut. Le correctif de l'étape 2 devra viser une **transition sans
remount** (continuité de clé React et parité de structure des blocs live↔BDD),
sans toucher à la logique SSE ni à la persistance.

## 5. Statut
- [x] Analyse du code (factuel) — faite.
- [ ] Capture avant/après — **en attente de preview lancée**.
- [ ] Liste finale des différences réelles — dépend de la capture.
