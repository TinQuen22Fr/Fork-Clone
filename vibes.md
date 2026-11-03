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

## 2026-11-03 — Fausses coupures reseau / reponses qui sautent
- **Intention** : supprimer les fausses deconnexions du client (bandeau "Reseau indisponible" + reponses SSE qui sautent) alors que la liaison est bonne (SSH OK en arriere-plan).
- **Friction** : cause racine probable = buffering Nginx sur la route SSE (hors perimetre projet, infra OS). Corrige cote applicatif seulement : watchdog SSE 10s->45s, timeout axios 20s->60s, Cache-Control global retire. Non verifie en preview live (pas d'env fonctionnel ici) — reprise reelle non testable.
- **Suite** : si le symptome persiste apres deploiement, verifier `proxy_buffering off` + `proxy_read_timeout` sur la route /api/chat/stream cote Nginx (action utilisateur, hors projet).

## 2026-11-03 (suite) — Deploiement JAMAIS fait + anti-regression affichage
- **Intention** : comprendre pourquoi "timeout of 20000ms" s'affiche encore alors que api.js est a 60000.
- **Friction** : `git status` montre api.js en `M` (NON commite). Dernier commit = c4f6ec0 (commit-msg), PAS le fix reseau. => la prod tourne avec l'ancien code 20s. Le fix n'a jamais ete enregistre sur GitHub ni deploye par upgrade.sh.
- **Ajout** : reconcileMessages (Chat.jsx) protege contre l'ecrasement de l'affichage par une liste serveur partielle lors d'une resync (le "debut de message qui saute"). Build OK.
- **Suite** : enregistrer sur GitHub (api.js + Chat.jsx) PUIS upgrade.sh. Sans ca, rien ne change cote prod.

## 2026-11-03 (suite 2) — Vraie cause du "message qui saute" trouvee
- **Intention** : eliminer la resync qui ecrase l'affichage pendant la generation.
- **Diagnostic** : Nginx (prod+preview) a DEJA proxy_buffering off + X-Accel-Buffering:no cote backend → piste "buffering reseau" FAUSSE, retiree. Vrai defaut : pendant le polling de reprise, GET /messages renvoie une liste SANS le message assistant encore en cours (non persiste) → reconcileMessages ecrasait l'affichage → message qui saute.
- **Fix** : polling ne recharge /messages et ne reconcilie QU'UNE FOIS stillRunning==false. Ordre des branches post-boucle corrige (!finished avant !lastList.length) pour ne plus afficher "connexion coupee" a tort quand la generation continue.
- **Friction** : aucune redirection HUB dans le code → le "basculement HUB" vient du Ctrl+Shift+R manuel qui recharge sur la route par defaut, pas d'un bug de navigation.
- **Verifie** : build vite OK (built in 31.11s). NON verifie en prod, NON deploye.
- **Suite** : utilisateur enregistre sur GitHub (api.js + Chat.jsx) puis upgrade.sh.
