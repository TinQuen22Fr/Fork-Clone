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

## 2026-11-03 (suite 3) — Migration React 19.0.0 -> 19.3.1 (frontend)
- **Intention** : bump react + react-dom de 19.0.0 a 19.3.1 (mise a jour mineure, API additives : ViewTransition, Fragment Refs, Trusted Types ; aucun breaking change).
- **Friction** : le backend n'est PAS en React (FastAPI/Python) -> migration cote frontend uniquement. Cache npm global /home/quentin/.npm inaccessible (droits, hors perimetre) -> contourne via --cache ./.npm-cache (puis supprime). Install scripts esbuild bloques (allowScripts) : sans effet, build OK.
- **Suite** : verifie en preview apres deploiement ; enregistrer sur GitHub + upgrade.sh si validation.

## 2026-11-03 (suite 3) — Migration React 19.0.0 -> 19.3.1 (frontend)
- **Intention** : migrer React du frontend vers 19.3.1 ; clarifier la demande "backend React" (hors-sujet : backend = Python/FastAPI).
- **Friction** : React 19.3.1 existe bien (npm latest) ; package.json/node_modules/lockfile deja alignes sur 19.3.1 (migration paquets deja effective). Backend sans React -> rien a migrer. Pas de reseau npm sortant.
- **Verifs** : aucune API retiree React 19 dans src/ (pas de ReactDOM.render, defaultProps, propTypes) ; createRoot present dans main.jsx. Build vite OK (built in 45.02s, 2025 modules, 0 erreur).
- **Suite** : enregistrer sur GitHub puis upgrade.sh pour deployer.

## 2026-11-03 (suite 4) — MAJ dependances frontend (lot sur, compatibles React 19.3.1 + Node 26)
- **Intention** : mettre a jour les deps frontend vers les dernieres versions COMPATIBLES entre elles, sans breaking change ; laisser les majeures risquees de cote.
- **Fait** : bump de ~45 paquets (tout Radix, @tanstack/react-query 5.106, react-router-dom 7.18.5, react-hook-form 7.91, @hookform/resolvers 5.2.2, recharts 3.10.1, axios 1.21, sonner/swr/date-fns/dayjs/input-otp/tailwind-merge/postcss/autoprefixer/@types/lodash/eslint-plugin-react). Aucun ERESOLVE. Build vite OK (built in 31.60s, 0 erreur, 2035 modules). Backup dans /tmp/*.bak.
- **Friction** : cache npm global /home/quentin/.npm inaccessible (droits) -> contourne avec --cache ./.npm-cache (supprime apres). Majeures NON appliquees (breaking) : tailwindcss 3->4, zod 3->4, framer-motion 11->14, react-day-picker 8->10, react-resizable-panels 3->4, vite 6->8, eslint 9->10, @eslint/js 10, eslint-plugin-react-hooks 7, globals 17, lucide-react 0->1, @vitejs/plugin-react 6, @hookform/resolvers 7. Non verifie en runtime navigateur, non deploye.
- **Suite** : attendre l'arbitrage de l'utilisateur sur les majeures, puis enregistrer sur GitHub + upgrade.sh.

## 2026-11-03 (suite 5) — Modernisation forwardRef -> ref prop (React 19)
- **Intention** : supprimer les 135 `forwardRef` des 38 composants `src/components/ui/*` (deprecie React 19), `ref` devenant une prop normale.
- **Methode** : script Node AST (@babel/parser+traverse+generator deja dans node_modules), applique par 4 lots avec build vite de controle apres chaque lot. Preuve prealable sur switch.jsx validee.
- **Resultat** : 0 forwardRef restant dans src/. Build final `✓ built in 28.61s`, 0 erreur. 38 fichiers ui modifies. Script temporaire supprime (non tracke). Backup /tmp/ui.bak.
- **Friction** : lint eslint inexploitable (config v9 flat absente du projet, pre-existant) -> build vite = reference. Babel reformate le style JSX (reflow + `;`), fonctionnel identique. Non verifie : runtime navigateur reel. Non deploye.
- **Suite** : attendre arbitrage utilisateur -> fournir message de commit, enregistrer sur GitHub, lancer upgrade.sh sur Dedibox.

## 2026-11-03 (suite 6) — Code-splitting / optimisation temps de chargement
- Intention : reduire le bundle unique de 474 K pour accelerer la 1ere charge (plainte wifi/ordi).
- Mesure initiale : index 474 K + markdown 117 K + react 50 K, tout charge d'un bloc meme sur /login.
- Leviers appliques :
  * App.jsx : React.lazy + Suspense sur les pages Login et Chat (fallback = spinner typing-dots).
  * vite.config.js : manualChunks(id) affine -> react / router / markdown / motion / radix / tanstack / vendor.
  * Correction warning "Circular chunk vendor->react->vendor" : react pur + scheduler isoles, react-router separe en chunk "router".
- Levier ECARTE (volontaire) : lazy sur ChatMessage inutile (page Chat affiche toujours des messages -> gain nul, UX degradee).
- Resultat build : react 223K / markdown 128K / vendor 121K / Chat 102K / router 38K / tanstack 29K / index 10K / Login 4K. 0 erreur, 0 warning circulaire.
- Gain : /login ne charge plus Chat (102K) ni markdown (128K) ; ~55% plus leger avant connexion ; meilleur cache par chunk apres upgrade.sh.
- Friction : runtime navigateur non verifie (a confirmer apres deploiement). Backups /tmp/App.jsx.bak, /tmp/vite.config.js.bak.
- Suite : commit + push GitHub + upgrade.sh sur la Dedibox.
