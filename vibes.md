- **Intention** : Résumer l'historique de conversation (anciens et nouveaux échanges) conformément au format strict demandé.
- **Friction** : Aucune.
- **Suite** : Restituer la synthèse sous forme de puces courtes en français sans intro ni outro.

## 2026-11-12 — Phase B · Étape 5 (rendu-blocs-style-emergent)
- **Intention** : Exposer `blocks[]` au détail `/messages/{id}/details` et alléger le listing `/messages` (exposer `blocks_count`, retirer `blocks[]`).
- **Friction** : Le détail renvoyait déjà le doc entier (donc `blocks[]` inclus) ; pas d'écriture nécessaire. Le mini-exécuteur de test ne résolvait pas les références de champ `$` (corrigé).
- **Suite** : Étape 6 (mémoïsation/rendu front des blocs) — à exécuter sur demande.

## 2026-11-12 — rendu-blocs-style-emergent étape 6
- **Intention** : découper l'étape 6 (Phase B) en 5 sous-étapes atomiques dans la doc chantier, puis lancer 6.1 (verrouiller MarkdownBody + build + comparateur React.memo).
- **Friction** : passage précédent laissé non vérifié (MarkdownBody/MessageBlocks créés non commités, ChatMessage mélange étape 6+7, live non câblé).
- **Suite** : exécuter 6.1 — vérifier MarkdownBody, ajuster le comparateur React.memo de ChatMessage, valider `vite build`.
- **6.1 FAITE** : comparateur `React.memo` de ChatMessage étendu à `message.blocks` ; `MarkdownBody.jsx` vérifié complet ; `vite build` vert (2040 modules, 32.7 s). Build valide.
