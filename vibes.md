## [2026-03-30] Summarize Conversation History
- **Intention** : Fusionner le résumé d'historique existant avec les détails des Étapes 9 (Brique D) et 10 (Brique F).
- **Friction** : Aucune friction technique, récapitulatif factuel strict exigé sans préambule ni conclusion.
- **Suite** : Restituer le résumé consolidé sous forme de puces concises en français.

## [2026-11-01] Bouton « générer le message de commit » dans Enregistrer sur GitHub
- **Intention** : Cartographier la boîte « Enregistrer sur GitHub » et le backend git pour planifier un bouton qui pré-remplit le champ « Message de commit » à partir du `git diff` réel.
- **Friction** : Le diff n'est accessible que côté serveur (le frontend ne voit que `changes` = un entier via `/github/status`) ; la génération nécessite donc un nouvel endpoint backend. Point non vérifiable en lecture seule : comportement exact de `resolve_project_dir` et conventions de test.
- **Suite** : Rédiger le plan (endpoint `/api/github/commit-message` → bouton dans `GithubSaveDialog.jsx` → tests → doc), sans toucher au code.

## [2026-11-02] Bouton « Générer le message de commit » — réalisé
- **Intention** : implémenter l'endpoint POST /api/github/commit-message + bouton « Générer » dans GithubSaveDialog, génération déterministe du message conventionnel depuis le git diff réel.
- **Friction** : yarn/eslint inutilisables (EACCES sur /home/quentin/.config, hors périmètre) → build via node_modules/vite/bin/vite.js ; bouton non testé en navigateur (preview Docker HS) et needs_project multi-projets non exécuté.
- **Suite** : enregistrer le lot sur GitHub (server.py, GithubSaveDialog.jsx, test_commit_message.py, RAPPORT + vibes.md) puis valider le bouton au premier usage réel.

## 2026-11-03 — Bandeau « Réseau indisponible » récurrent
- **Intention** : fiabiliser le client HTTP pour que le bandeau ne s'affiche que sur vraie panne, pas sur micro-coupure/réveil d'onglet.
- **Friction** : causes réelles = aucun `timeout` axios (requêtes pendantes → ERR_NETWORK) + retry limité à 1 essai/600 ms (insuffisant au réveil). Non testable en live faute d'env preview fonctionnel.
- **Suite** : `timeout: 20000` ajouté ; retry GET idempotent avec backoff [400,900,1800] ms + prise en compte `ECONNABORTED`/timeout ; POST/PATCH/DELETE jamais rejoués. Build vite OK (33.77s). → Enregistrer sur GitHub.

## 2026-11-03 — Message de commit : anti-bruit + corps plafonne
- **Intention** : eviter que `.forge-context`/`vibes.md` tirent le commit vers `chore` nu, et plafonner le corps a 3 lignes.
- **Friction** : le resume multi-fichiers reste `met a jour N fichiers` (deterministe assume, pas de devinette fix/feat). Non teste en preview live.
- **Suite** : attente arbitrage utilisateur pour resume descriptif cas "1 fichier code + bruit", sinon enregistrer sur GitHub.
