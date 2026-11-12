- **Intention** : Fusionner et résumer l'historique complet de la conversation sur l meurtre du test 4.
- **Friction** : Aucune.
- **Suite** : Livrer le compte rendu sous forme de puces concises sans préambule ni conclusion.

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

## 2026-11-11 — 6.2 MessageBlocks (relecture + caret live préparé)
- **Intention** : figer `MessageBlocks` comme rendu de relecture ; ajouter prop `streaming` + caret sur le dernier bloc texte (préparation 6.4).
- **Friction** : `read_file` échouait sur un cache de chemin → relecture via `cat`. Parité visuelle réelle non vérifiable au build (preview requise).
- **Suite** : 6.3 — construire `blocks[]` live dans `streamStore` (miroir backend étape 4).

## 2026-11-11 23:02 — 6.3 construire blocks[] live (streamStore)
- **Intention** : reconstruire cote client la meme sequence ordonnee que le backend (deltas coalesces en bloc text, outil -> paire tool_call/tool_output, seq monotone, step_index).
- **Friction** : pas d'infra de test front (ni script test, ni vitest) -> test autonome Node pur (streamStore.test.mjs), 19 assertions. Parite visuelle reelle non verifiable au build (reportee 6.5/preview).
- **Suite** : 6.4 exposer blocks + streaming via useStreamingMessage.

## 6.4 — $(date +%F)
- **Intention** : exposer `blocks`+`streaming` via `useStreamingMessage`, câbler la bulle live sur `MessageBlocks` (parité composant live/relecture).
- **Friction** : hook `useStreamingMessage` déjà complet (blocks dérivés + content dérivé + streaming:true). Seule lacune réelle : `ChatMessage` ne passait pas `streaming` à `MessageBlocks` → caret live (6.2) jamais affiché en flux par blocs. Corrigé. Parité visuelle non vérifiée (preview, reportée 6.5).
- **Suite** : 6.5 — isoler étape 6/7 dans ChatMessage + parité, puis commit final + protocole test preview/prod.

## 2025-11-12 — Correctif rules-of-hooks + finalisation lint
- **Intention** : corriger les 8 `react-hooks/rules-of-hooks` de `src/pages/Chat.jsx` (early returns `user` avant les hooks) + finir `Login.jsx` (5 problèmes).
- **Friction** : Chat.jsx fait 2123 lignes, 2 early returns (l.429-440) suivis de ~40 hooks → vrai bug latent (crash "Rendered fewer hooks" si user repasse null/false). Déplacement du bloc garde via script Python après le dernier hook, juste avant le `return (` principal. Login.jsx : 3 labels associés par htmlFor/id, 1 comment-textnode en string, setter `_setMode` préfixé.
- **Suite** : lint global 0 problème + build OK vérifiés. Reste non testé en preview réelle (rendu Chat). Pour prod : Sauvegarder sur GitHub → upgrade.sh.

## 2025-11-12 12h14 — Audit console prod (dogfooding) : a11y form fields + perf submit
- **Intention** : auditer les signalements console de la PROD (dogfooding, pas preview) au Send/Ctrl+Enter/`+` ; trancher erreur réelle vs bruit.
- **Friction** : aucune erreur runtime (0 rouge, 0 warning orange) ; panneau Issues = 4 "Improvements" bleus « form field should have id/name ». Preuve code : `src/pages/Chat.jsx` l.1766 (input file), 1785 (textarea), 1902 (select provider), 1968 (select model), + 2051 (select agent, conditionnel → 5e si menu ouvert) — tous sans `id`/`name`, seulement `data-testid`. Non couvert par ESLint (règle navigateur). `[Violation] 'submit' handler ~2000ms` = perf de `sendMessage` (deep clone JSON.parse/stringify historique l.737-745 + re-render global l.757 + concat historyText l.727-733), pré-existant, non lié au correctif hooks.
- **Suite** : MINEUR a11y → ajouter `id`+`name` sur les 5 champs de Chat.jsx (coder). Chantier perf distinct : optimiser `sendMessage` (supprimer deep clone, sortir historyText du chemin synchrone) sans régresser `React.memo` ChatMessage ni la zone streaming.

## 2026-05-24 — M1 a11y Chat.jsx (vibe-coder)
- **Intention** : ajouter id+name sur les 5 champs de formulaire sans identifiant dans frontend/src/pages/Chat.jsx (input file, textarea message, select provider/model/agent).
- **Friction** : `yarn lint` casse sur EACCES ~/.config/yarn (config globale hors projet) → contourné via ./node_modules/.bin/eslint (exit 0). Build OK en 31.68s.
- **Suite** : utilisateur valide le diff en PROD (Issues doit passer de 4→0), puis push GitHub + upgrade.sh. M2 (perf sendMessage) reste un chantier distinct non traité.
