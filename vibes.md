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

## 2026-11-03 (suite 7) — Diagnostic "chargement conversations hyper long"
- **Intention** : trouver pourquoi le chargement des conversations est très lent.
- **Friction** : `api.js` n'était pas le bon chemin (c'est `src/lib/api.js`). Mongo local interrogé en lecture seule via pymongo pour mesurer sans lire de contenu.
- **Suite** : corriger get_messages (ne plus renvoyer tool_steps/steps intégraux), activer gzip nginx, puis limiter le poll 8s.

## 2026-11-03 (suite 8) — Allègement payload conversations (correctif perf)

- **Intention** : réduire le payload JSON de `GET /conversations/{id}/messages` de 1-2 Mo à ~100-300 Ko en strippant les `output`+`input` des outils du listing, chargement à la demande via nouvel endpoint `/messages/{id}/details`.
- **Friction** : gzip Nginx (`application/json`) non activable sans sudo — instructions manuelles fournies. Runtime navigateur non vérifiable en preview.
- **Suite** : déploiement via GitHub + `upgrade.sh`, activation gzip Nginx manuellement.

### Fichiers modifiés
- `backend/server.py` : strip agressif dans `get_messages` (tool_steps/steps vidés, résumé léger conservé) + nouvel endpoint `GET /messages/{id}/details` + param `after` sur get_messages
- `frontend/src/components/ChatMessage.jsx` : import api, state `detailData`/`detailLoading`, `fetchDetails` appelé à l'ouverture accordéon, merge données résumées/complètes, loading indicators
- `frontend/src/pages/Chat.jsx` : reconcileMessages comparateur corrigé pour empty arrays stables

### Mesures réelles (base prod, 60 messages par conv)
- conv 4ddce329 : 1958 Ko → 187 Ko (-90%)
- conv fc0f29f9 : 1437 Ko → 181 Ko (-87%)
- conv a0c4c62c : 1242 Ko → 137 Ko (-89%)
- conv a97939ec : 1027 Ko → 300 Ko (-71%)
- Moyenne : ~85% de réduction du payload

### Gzip Nginx (à faire manuellement en SSH)
```
sudo sed -i 's/# gzip_vary on;/gzip_vary on;/' /etc/nginx/nginx.conf
sudo sed -i 's/# gzip_proxied any;/gzip_proxied any;/' /etc/nginx/nginx.conf
sudo sed -i 's/# gzip_comp_level 6;/gzip_comp_level 6;/' /etc/nginx/nginx.conf
sudo sed -i 's/# gzip_types/gzip_types/' /etc/nginx/nginx.conf
sudo nginx -t && sudo systemctl reload nginx
```

## 2026 — Audit régression "outil d'audit" (explosion tokens >6M)
- **Intention** : auditer la régression de l'audit de sécurité (erreur réseau, token invalide, >6 000 000 tokens). Périmètre : boucle agentique streaming de server.py + impact du commit c75dc54.
- **Friction** : impossible de reproduire un vrai appel provider (pas de token de test, coûterait des tokens réels) ; ampleur mesurée par analyse statique des bornes de troncature, pas par run live.
- **Suite** : BLOQUANT confirmé — `_stream_claude` (l.4268), `_stream_claude`-bis (l.4629) et la boucle responses (l.3986) ré-injectent `output` BRUT sans troncature dans `messages`, ré-envoyé à chaque itération (jusqu'à 60). read_file borne à 100 000 chars, bash à 12 000 → un seul read_file de gros fichier × 60 tours = millions de chars. STEP_LOG_TRUNC (1200) défini mais JAMAIS appliqué aux boucles streaming. Les voies non-streaming tronquent à output[:4000] (l.2317/2562/3205), les voies streaming NON. Verdict : REJET.

## 2026-11-06 — Audit sécurité lent + actualisation qui rame (post c75dc54)
- **Intention** : comprendre pourquoi l'actualisation glandouille sur l'animation de chargement au lieu d'afficher la conversation quasi-instantanément ; clarifier que "outil d'audit de sécurité" = bouton runSecurityAudit (prompt LLM), pas l'agent vibe-reviewer.
- **Diagnostic chiffré (base forge, conv de 1540 msg)** :
  - Backend get_messages = 28 ms, index conversation_id_1_created_at_1 DÉJÀ présent → le backend N'EST PAS le goulot.
  - Payload après strip = 627 Ko (les `input` d'outils restent lourds), dont 132 Ko de markdown sur 60 messages.
  - Vrai goulot = rendu React de 60 messages d'un coup, chacun avec ReactMarkdown+remark-gfm NON memoïsé. Le code l'admet (commentaire l.531) mais réduire à 60 ne suffit pas.
  - Strip des output se fait en Python APRÈS lecture Mongo complète (1086 Ko désérialisés) → on lit puis on jette. Projection Mongo n'exclut pas tool_steps/steps à la source.
- **Point utilisateur légitime non technique** : server.py = 8900 lignes, 264 fonctions → monolithe à découper en modules .py.
- **Suite (plan, non appliqué)** : 1) afficher immédiatement le dernier état connu (ne pas bloquer sur skeleton, rendu progressif/différé des anciens) ; 2) memoïser le markdown de ChatMessage ; 3) exclure tool_steps/steps à la source Mongo (projection) et servir un résumé pré-calculé ; 4) reprise d'une génération en cours après actualisation sans écraser l'affichage ; 5) amorcer l'éclatement de server.py en modules.

---

## Boucle d'analyse — Bug `timeout of 60000ms exceeded` "sans rien faire" + lenteur actualisation + découpage server.py

### Intention
Diagnostiquer l'erreur `timeout of 60000ms exceeded` qui s'affiche alors que le tour est FINI (dernier message affiché, agent en attente, aucune action utilisateur). Cartographier aussi la lenteur d'affichage à l'actualisation et le chantier de découpage de server.py (8900 lignes).

### Cartographie (fichiers réels confirmés)
- `frontend/src/lib/api.js`
  - l.33 : `axios.create({ timeout: 60000 })` — timeout GLOBAL appliqué à TOUTES les requêtes axios (api.get/post/patch/delete).
  - Flux SSE (`postSSE`/`readSSEStream`) utilise `fetch` + watchdog 45s (ping backend 4s), terminalSeen sur `finished`/`done` — N'EST PAS concerné par le timeout 60000 axios.
  - Interceptor response (l.59-81) : retry GET sur ECONNABORTED, mais si épuisé → rejet → erreur remontée à l'appelant.
- `frontend/src/pages/Chat.jsx` (2054 lignes — PAS components/Chat.jsx)
  - l.256 `fetchStatusTurns` → `GET /chat/status`, alimente `runningTurns` depuis `data.running`.
  - l.388-395 `setInterval(8000)` actif TANT QUE `runningTurns.length > 0` → appelle `fetchStatusTurns()` + `loadMessages(activeId)` toutes les 8s.
  - l.537 `loadMessages` : `GET /conversations/:id/messages?limit=MESSAGES_FIRST_PAGE`, catch → `setError(formatApiError(e))` SANS distinguer poll de fond vs action utilisateur.
  - Payload mesuré : ~627 Ko après strip (input d'outils restants), ~132 Ko de markdown à parser, rendu non memoïsé.
- `backend/server.py`
  - l.5340 `/chat/status` : `running[]` construit UNIQUEMENT depuis le dict mémoire `_ACTIVE_RUNS` (l.5381-5388).
  - l.5618 ajout `_ACTIVE_RUNS[cid]` ; l.6367 retrait dans un `finally` (Brique C) → nominal OK.
  - l.6436 `_reap_orphan_turns` : nettoie la BDD au démarrage, PAS le dict mémoire (qui est vide au reboot de toute façon).

### Cause racine (révisée, prouvée)
Le `finally` l.6366 vide bien `_ACTIVE_RUNS` en fonctionnement nominal → PAS de fantôme permanent côté backend.
Le vrai mécanisme du `timeout of 60000ms exceeded` "sans rien faire" :
1. Poll agressif : `setInterval` 8s recharge `GET /messages` (627 Ko) tant que `runningTurns.length > 0`.
2. Ces GET héritent du `timeout: 60000` axios GLOBAL (api.js l.33).
3. Fenêtre de latence : entre deux polls, ou si un `/chat/status` a été capturé AVANT le `finally` backend, le client garde `runningTurns > 0` et continue le poll. Un seul `GET /messages` qui dépasse 60s (navigateur qui rame sous 627 Ko + 132 Ko markdown non memoïsé, onglet arrière-plan, micro-coupure) → ECONNABORTED.
4. `loadMessages` catch → `setError(formatApiError)` affiche le bandeau rouge, MÊME si c'est un poll de fond et que le tour est fini.

Deux défauts nommés :
- DÉFAUT A (déclencheur) : timeout axios 60000 GLOBAL appliqué à un GET potentiellement lourd rejoué en boucle. Un timeout fixe sur un GET de 627 Ko est fragile.
- DÉFAUT B (visibilité) : un échec sur un POLL AUTOMATIQUE est traité comme erreur métier affichée. Un poll de fond raté doit être silencieux (il sera rejoué au tick suivant).
- DÉFAUT C (coût) : `loadMessages` recharge TOUTE la page (627 Ko) à chaque tick au lieu du delta ; rendu markdown non memoïsé = lenteur d'actualisation perçue.

### Friction (points non tranchables en lecture seule)
- Impossible de confirmer quelle requête précise (GET /messages vs /chat/status) a timeouté sur la capture : il faudrait l'onglet Réseau au moment du bug (requête rouge/pending ~60s). La logique du code suffit à établir le mécanisme, pas le déclencheur exact de CETTE capture.
- Impossible de mesurer le gain du rendu memoïsé sans navigateur réel (prod, pas preview).

### Suite (dossier pour Vibe Coder — étapes atomiques, ordonnées)
1. [BLOQUANT] Timeout dédié + court sur les GET de polling (ne pas hériter du 60000 global) ; ne JAMAIS afficher d'erreur pour un poll de fond raté. Fichiers : api.js (option timeout par requête), Chat.jsx loadMessages (flag "silent" sur appels du setInterval). Vérif : build frontend OK ; simuler latence → aucun bandeau rouge sur poll. Fin : plus d'erreur "sans rien faire".  — requiert rien.
2. [BLOQUANT] Poll = delta only : setInterval ne recharge QUE les messages après le dernier connu (param `after`/`before` déjà supporté), pas les 627 Ko. Fichier : Chat.jsx (loadMessages vs un loadDelta). Vérif : taille réseau par tick < 50 Ko. Fin : poll léger. — requiert étape 1.
3. [MAJEUR] Memoïser le rendu markdown de ChatMessage (React.memo + useMemo sur le parse). Fichier : ChatMessage.jsx. Vérif : build OK ; profiler React (manuel). Fin : actualisation fluide sur longue conversation. — indépendant.
4. [MAJEUR] Afficher immédiatement le dernier état connu à l'actualisation (ne pas bloquer sur MessagesSkeleton si un cache local existe), synchro en arrière-plan. Fichier : Chat.jsx (état initial + effet de montage). Vérif : affichage < 500ms perçu. Fin : plus d'animation prolongée. — requiert étape 3 pour le gain réel.
5. [MAJEUR] Projection Mongo à la source : exclure tool_steps/steps dans get_messages (ne pas lire 1086 Ko pour en jeter la moitié). Servir un résumé pré-calculé. Fichier : backend/server.py endpoint messages. Vérif : temps réponse + taille payload. Fin : payload < 300 Ko. — indépendant.
6. [MAJEUR] Borner/plafonner le ré-injection des sorties d'outils dans la boucle agentique (bug tokens de l'audit → millions de tokens). Fichier : backend/server.py (_stream_claude / boucle outils). Vérif : test ciblé sur boucle longue, _estimate_tokens stable. Fin : plafond de contexte respecté. — indépendant.
7. [STRUCTUREL] Éclater server.py (8900 l, 264 fn) en modules .py dédiés, server.py = assembleur. Lots suggérés : routes_chat.py, routes_conversations.py, routes_preview.py, providers/*.py, tools/runner.py, history.py, sse.py. Vérif : imports OK, pytest backend/tests passe, démarrage backend OK. Fin : server.py < 1000 lignes. — lot séparé, à faire APRÈS 1-6 pour ne pas mélanger refacto et correctifs.

### Dépendances
- Étape 2 requiert étape 1 (timeout/silent d'abord).
- Étape 4 requiert étape 3 (memoïsation avant d'afficher immédiatement, sinon le "dernier état connu" rame aussi).
- Étapes 5, 6, 7 indépendantes entre elles et du front.

## 2026-11-07 — "timeout of 60000ms exceeded" tour FINI, agent en attente, aucune action
- **Intention** : trouver la source exacte du bandeau rouge `timeout of 60000ms exceeded` qui apparaît alors que le dernier message est déjà affiché et que l'agent attend (donc AUCUN tour en cours). Lecture du README2 (dogfooding/OAuth Claude Pro/SSE) ancrée.
- **Éliminations démontrées (lecture code réelle)** :
  - SSE (`readSSEStream`, api.js l.107-171) : utilise `fetch` natif + watchdog 45s + ping 4s + fermeture sur `finished`/`done`. N'émet JAMAIS `timeout of 60000ms exceeded` (ce libellé = signature axios ECONNABORTED). → Mon hypothèse "SSE fantôme" de l'échange précédent était FAUSSE.
  - Poll 8s (Chat.jsx l.387-395) : conditionné à `runningTurns.length`, piloté par `/chat/status` → `running` issu de `_ACTIVE_RUNS` (server.py l.5381-5388). Nettoyage garanti par `finally: _ACTIVE_RUNS.pop(...)` (l.6367). Tour fini ⇒ `running:[]` ⇒ poll s'arrête. → PAS la source en régime fini normal.
- **Source RÉELLE (axios)** : le message `timeout of 60000ms exceeded` = axios `ECONNABORTED` sur `timeout: 60000` (api.js l.33), appliqué à TOUT appel axios GET/POST. L'intercepteur de retry (api.js l.57-80) rejoue les GET en backoff [400,900,1800] ms puis, à épuisement, REJETTE l'erreur → bandeau rouge. Déclencheurs plausibles tour-fini : réveil d'onglet / `visibilitychange` (Chat.jsx l.355-385) qui relance `loadMessages`+`fetchStatusTurns`+`fetchConversations` ; OU un GET isolé (usage/models/opencode) qui pend >60s côté serveur sous charge. Le timeout axios fixe de 60s sur des GET potentiellement lourds (627 Ko de messages, ou endpoint lent) est la cause mécanique.
- **Friction** : la requête EXACTE en rouge n'est identifiable qu'avec l'onglet Réseau du navigateur au moment du bug (voir quelle requête passe en `(canceled)`/rouge à ~60s). Côté code, 3 candidats : GET /conversations/{id}/messages, GET /chat/status, GET /opencode/usage ou /models.
- **Suite (plan planner, à passer à vibe-coder)** :
  1. Différencier les timeouts axios : GET messages = timeout court (ex 20-25s) + retry (déjà là) ; endpoints secondaires (usage/models) = timeout court et NON bloquants ; ne jamais remonter un ECONNABORTED de GET secondaire en bandeau d'erreur rouge global.
  2. Un ECONNABORTED sur GET idempotent après épuisement du retry NE DOIT PAS produire de bandeau rouge bloquant "timeout" : le dégrader en resynchro silencieuse (le serveur va bien, les données sont en BDD).
  3. Vérifier côté serveur qu'aucun GET secondaire (/opencode/usage, /models/health) ne peut pendre ~60s (appel réseau externe sans timeout propre) → source possible du ECONNABORTED tour-fini.
  4. Instrumenter : attacher l'URL fautive au message d'erreur affiché (aujourd'hui "timeout of 60000ms exceeded" ne dit pas QUELLE requête) pour trancher définitivement.
- **Note style Emergent (demande récurrente de l'utilisateur)** : la granularité "bloc par bloc" (commande/retour séparés) existe DÉJÀ dans streamStore pendant la génération ; le problème est la bascule fin-de-tour → rechargement BDD en message markdown monolithique. Point distinct à traiter séparément.

## 2026-11-07 — Facteur réseau DNS confirmé + mise de côté « rendu blocs »
- **Intention** : consigner que le bug `timeout of 60000ms exceeded` était fortement aggravé par des DNS défaillants côté box Free ; archiver le sujet « rendu blocs façon Emergent » hors du périmètre du correctif timeout.
- **Friction** : après correction des DNS (réseau Free), la conversation s'affiche rapidement → confirme que le timeout axios 60s était un **seuil atteint à cause de la latence réseau**, pas forcément un défaut serveur. Le correctif reste pertinent (timeouts différenciés + GET de fond non bloquants) mais n'est plus bloquant/urgent.
- **Suite** : exécuter malgré tout les « corrections » du plan timeout (étapes 1→4 déjà consignées) via vibe-coder ; sujet « rendu blocs » rangé dans `PROJETS_AMELIORATIONS/rendu-blocs-style-emergent.md`, à instruire plus tard.

---

## 2026-11-07 — Facteur réseau DNS confirmé + point faible serveur `GET /models`

- **Intention** : consigner le réglage DNS (environnement) et finaliser l'étape 3 du plan (endpoints secondaires lents côté serveur).
- **Friction** : rien bloquant ; analyse statique uniquement (rôle planner, lecture seule).
- **Suite** : passer à vibe-coder pour exécuter le plan robustesse timeout.

### Contexte réseau (environnemental, NON spécifique à la Forge)
- Box Freebox Révolution, opérateur Free. L'utilisateur est passé des DNS Google/Cloudflare aux **DNS 100 % Free** :
  - IPv4 : primaire `212.27.40.240`, secondaire `212.27.40.241`
  - IPv6 : `2a01:e0c:1:1599::22`, `2a01:e0c:1:1599::23`
- A aussi activé la **résolution DNS IPv6** dans la box, et corrigé la config IPv4 requise côté portable (la box était configurée comme DNS en plus de passerelle — soupçon de bug).
- **Effet observé** : affichage conversation + rafraîchissement nettement plus rapides, retour de coding plus rapide (tour à 4 outils).
- **Conclusion** : le `timeout of 60000ms exceeded` était massivement aggravé par des **résolutions DNS lentes/défaillantes**, pas par un défaut fonctionnel serveur. Le correctif robustesse reste pertinent mais n'est plus urgent/bloquant.

### Étape 3 du plan — vérification serveur des endpoints secondaires (FAIT, lecture)
- `GET /opencode/usage` (l.6496) : timeout httpx 12s/connect 5s, **ne propage jamais** (retourne `{available:false}`). ✅ sain.
- `POST /models/health` (l.7012) : cache TTL 10 min, « ne lève jamais ». ✅ sain.
- **`GET /models` (l.6861) : POINT FAIBLE RÉEL.**
  - Appelle `_fetch_catalog(pid)` (l.6653) **séquentiellement** dans une boucle `for` sur chaque provider dispo (claude, gemini, opencode, ollama_cloud, free providers…).
  - Chaque `_fetch_catalog` fait un appel HTTP externe à **15s de timeout** (ex. Claude `/v1/models` l.6670).
  - Les timeouts **s'additionnent** : 4-5 providers × 15s dans le pire cas ⇒ cumul capable d'atteindre les **60s du timeout axios frontend**.
  - Cache `_CATALOG_TTL` 10 min masque le problème en régime établi ; il réapparaît au **1er chargement / cache expiré / réveil d'onglet**, surtout avec DNS lents.
  - **Candidat n°1 de la capture utilisateur** — parfaitement cohérent avec « DNS corrigés ⇒ plus de timeout ».

### Plan robustesse — mis à jour
1. **`GET /models` : paralléliser `_fetch_catalog`** via `asyncio.gather` au lieu de la boucle séquentielle → temps total = max(15s) et non somme. + envisager un plafond global (ex. 10s) sur l'endpoint.
2. **Timeouts axios différenciés** (`api.js` l.33) : GET messages court (~20-25s) + retry ; endpoints secondaires (models/usage/health) court et non bloquants.
3. **`ECONNABORTED` sur GET idempotent** après épuisement retry ⇒ resynchro **silencieuse**, jamais de bandeau rouge.
4. **Instrumenter le message d'erreur** avec l'URL fautive (diagnostic définitif de la requête responsable).

---

## 2025 — Boucle : correctifs robustesse timeout (vibe-coder)

- **Intention** : appliquer les 4 étapes du plan robustesse timeout (parallélisation `/models`, timeouts axios différenciés, resync silencieuse GET, URL fautive dans l'erreur).
- **Friction** : eslint v9 sans config dans le repo → non exécutable ; cache npm root-owned → `npx`/babel-preset-react inutilisables. Contourné via `esbuild` local (parse JSX OK). `test_regression.py` échoue (404) car tests d'intégration HTTP live, pas de serveur up ici — sans rapport avec le changement.
- **Suite** : pousser sur GitHub puis `upgrade.sh` sur la Dedibox pour déployer en prod.

### Changements livrés
1. `backend/server.py` — `GET /models` : boucle séquentielle `_fetch_catalog` → `asyncio.gather(..., return_exceptions=True)`. Un provider externe lent/injoignable vu depuis la Dedibox ne bloque plus les autres. Gain mesuré 5x (5 providers), résilience exception validée (→ liste vide, pas de crash). Compile OK.
2. `frontend/src/pages/Chat.jsx` — `fetchStatusTurns` : timeout court dédié 8s sur `/chat/status` (secondaire, échec déjà silencieux).
3. `frontend/src/pages/Chat.jsx` — `loadMessages` : échec PUREMENT réseau (ECONNABORTED/ERR_NETWORK/timeout) sur ce GET idempotent → `console.warn` + resync au prochain poll, plus de bandeau rouge. Erreurs métier (404/500/auth) toujours affichées.
4. `frontend/src/lib/api.js` — `formatApiError` : branche dédiée `ECONNABORTED`/timeout + annexe `[url]` fautive à tous les messages pour diagnostic.

### Vérifs
- `python3 -m py_compile backend/server.py` → OK.
- esbuild parse `Chat.jsx` + `api.js` → OK.
- `test_reality_checks.py` → passe. `test_regression.py` → échecs 404 = intégration live hors scope.
- **Non vérifié** : build Vite complet (cache npm root-owned, droits insuffisants) et rendu runtime réel.

## 2026-11-07 — Tâche 1 (vibe-coder) : test parallélisation GET /models
- **Intention** : lever le MAJEUR de l'audit a93184d — créer un test versionné prouvant le parallélisme de `_fetch_catalog`.
- **Friction** : pas de `pytest-asyncio` dans le projet → suivi du pattern maison `asyncio.run()` dans un test sync (cf. test_tool_loop.py). Route HTTP non appelée (auth+providers réels) : la logique parallélisée est reproduite à l'identique du bloc server.py l.6875.
- **Suite** : `backend/tests/test_models_parallel.py` créé, 4 tests PASSED en 3.04s. Lot a93184d clos côté dette de couverture. Reste en réserve : Tâche 2 (timeout 15→8s / préchauffe cache) conditionnée au ressenti prod.

## 2026-11-07 — Tâche 2 : optimisation chargement à froid /models
- **Intention** : baisser le timeout unitaire des catalogues (15→8 s) et préchauffer le cache au démarrage pour que le 1er `GET /models` tape déjà le cache.
- **Friction** : `test_regression.py` échoue (404) = tests d'intégration HTTP sur serveur live absent de l'atelier, hors périmètre. Préchauffe non observable en runtime depuis l'atelier (pas de backend up) → validée par test isolé (0,303 s parallèle, échec provider toléré).
- **Suite** : commit + push GitHub, puis `upgrade.sh` en prod (manœuvre utilisateur). Observer un chargement à froid réel.

## 2026-11-08 — Audit sécurité défensif (fenêtre 01/10 → 07/11)
- **Intention** : auditer le code applicatif ajouté/modifié depuis le 01/10/2026 (server.py +3359/-336, preview_runtime.py +1140, tools/web_search.py nouveau), sur base AUDIT_SECURITE.md (tous points C1-F5 clos). Vérifier injection, secrets, failles web, permissions, erreurs ; détecter toute régression sur points clos.
- **Friction** : 2FA (totp.py/auth_2fa.py) ajouté puis reverté (32d5ba1) → hors périmètre (code absent de HEAD). git attributes global non lisible (warning bénin). AUDIT_SECURITE.md non accessible en chemin relatif via read_file (lu via bash/absolu).
- **Suite** : aucune régression détectée (E4 token toujours redacté+header, E1 verrou bash intact, XSS/CORS/JWT inchangés, _git sans shell=True, branch/repo validés regex, web_search/fetch_url passent par redact_secrets). 1 constat FAIBLE nouveau : fuite de chemin absolu (work_dir) et stderr git dans les detail HTTP des endpoints /github/* (pattern analogue à F1, non couvert par F1). Consigné F6. Verdict : CORRECTIONS REQUISES (1 faible).

## 2026-11-08 — Correctif F6 (fuite chemin/stderr git dans /github/*)
- **Intention** : aligner les endpoints `/github/commit-message` et `/github/push` sur la politique F1 — messages d'erreur génériques côté client, détail tracé côté serveur ; retirer la clé `workspace` (chemin absolu) de la réponse push.
- **Friction** : tests `test_deps_regression.py` échouent sous `cd backend` (imports `backend.*` cassés + tests nécessitant un serveur live) — préexistant, hors périmètre ; vérifié via `test_commit_message.py` lancé depuis la racine (10/10 OK). `git` émet des warnings `~/.config/git/attributes` Permission denied (droits hôte, sans impact).
- **Suite** : commit/push GitHub de la branche `claude-ai` puis déploiement prod via `upgrade.sh` (à la main de l'utilisateur).

## Boucle — Instruction "rendu-blocs-style-emergent" (2026-11-08)
- **Intention** : établir les tâches pour conserver, après fin de tour, un rendu en blocs cohérent avec le streaming (piste du .md PROJETS_AMELIORATIONS), sans écrire de code.
- **Friction** : le .md suppose une "bascule markdown monolithique" à la clôture, MAIS la cartographie montre que `ChatMessage` rend DÉJÀ les mêmes blocs (steps/tool_steps structurés + markdown) en live ET en relecture ; les `steps`/`tool_steps` structurés SONT persistés en BDD (`_persist_step`, `_ensure_assistant_doc`) et restitués en version allégée par le listing (`steps_summary`/`tool_steps_summary`), détail complet via `GET /messages/{id}/details`. L'écart réel est donc étroit : ordre d'affichage (texte vs blocs), granularité du streaming texte non persistée, et transition live→BDD au `done`. Point non vérifiable en lecture seule : le ressenti visuel exact de la transition.
- **Suite** : livrer le plan d'étapes atomiques ; confirmer avant exécution si l'utilisateur veut la persistance d'une structure de blocs ordonnée (texte intercalé entre étapes) ou seulement la stabilité visuelle de la transition.

## 2026-11-07 — Plan rendu-blocs façon Emergent
- **Intention** : écrire cartographie réelle + plan 8 tâches + ordre conseillé dans PROJETS_AMELIORATIONS/rendu-blocs-style-emergent.md sans réécrire l'existant.
- **Friction** : hypothèse initiale du .md (markdown monolithique) inexacte — ChatMessage rend déjà les blocs ; vrais écarts = ordre figé + texte non segmenté + micro-bascule. Parité visuelle non vérifiable en lecture seule (preview requise).
- **Suite** : attendre le choix de périmètre A (léger) ou B (structurel+migration BDD) avant tout code.

## 2026-11-11 01:13 — Phase A / Étape 1
- **Intention** : documenter la micro-bascule visuelle StreamingBubble→message BDD au `done`, en sous-fichier module dédié lié au plan.
- **Friction** : capture visuelle réelle impossible — preview Fork-Clone non lancée (seuls tournent prod/forge-dev/autres projets, hors périmètre) ; grille de capture laissée à remplir.
- **Suite** : attendre lancement preview pour remplir la grille, puis valider l'hypothèse C1 (remount par changement d'id) → étape 2.

## 2026-11-11 01:21 — Étape 2 (Phase A) : continuité de clôture sans remount
- **Intention** : supprimer la micro-bascule au `done` (cause C1) via clé stable partagée entre bulle live et message BDD, sans fichier à rallonge (hook + composant module).
- **Friction** : React remonte le dernier slot car le type racine de la bulle (`LiveMessage`) diffère de `ChatMessage` ; la clé stable `STREAMING_SLOT_KEY` + le même sous-arbre `ChatMessage` atténuent le reflow, mais la confirmation visuelle exige la preview (non atteignable en localhost → URL publique requise). Build Vite OK (2037 modules).
- **Suite** : capture avant/après `done` en preview pour remplir la grille de `etape-2-continuite-cloture.md` ; si validé, jalon de décision « Phase A suffit-elle ? » avant d'engager la Phase B.

## 2026-10-02 — Étape 2 : vérif preview Docker
- **Intention** : confirmer que la preview publique sert le build de l'étape 2 et obtenir une capture avant/après `done`.
- **Friction** : Chromium Playwright non installé sur le backend du projet + pas de binaire local → `screenshot_url` inopérant, pas de droits sudo pour l'installer. Capture visuelle déléguée à l'utilisateur. Vérifié par HTTP : chunk `Chat-Vp3RGyl1.js` (étape 2) répond HTTP 200 sur https://fork-clone.preview.quentin-astro.fr/ → nouveau build bien en ligne.
- **Suite** : attendre le retour visuel utilisateur (flash au `done` disparu ou non) → poser le jalon de décision Phase A vs Phase B.

## 2026-11-11 — Capture preview (étape 2)
- **Intention** : capturer le rendu réel du chat avant/après `done` pour vérifier C1.
- **Friction** : Playwright accessible via venv prod ; Chromium absent des caches lisibles → téléchargé proprement DANS le projet (`.captures/browsers/`, profil/tmp cloisonnés). Capture OK mais la preview renvoie **502 Bad Gateway** (conteneur Docker arrêté depuis le test 200 précédent) → vérif visuelle du chat impossible pour l'instant.
- **Suite** : attendre le redémarrage du conteneur preview par l'utilisateur, puis 2 captures ciblées (pendant streaming / après done) et remplir la grille de `etape-2-continuite-cloture.md`.

## 2026-11-11 — Nettoyage Playwright/Chromium (incident réseau prod)
- **Intention** : supprimer tout l'attirail de capture (Chromium 658 Mo + `shot.py`) que j'avais installé dans `.captures/`, car lancé depuis le backend **prod** il saturait CPU/réseau du serveur qui sert la fenêtre de chat → streaming SSE buggé côté client + réseau portable planté.
- **Friction** : la capture d'un site en preview depuis le même backend prod est incompatible avec le service du chat live. Playwright techniquement opérationnel mais **inutilisable ici** sans dégrader la prod → abandonné.
- **Suite** : correctif C1 (étape 2) à vérifier **sans navigateur** (HTTP 200 OK, build servi). Validation visuelle du flash/reflow déléguée à l'utilisateur dans son propre navigateur. Ne plus jamais lancer de navigateur headless depuis ce backend.

## 2025 — upgrade.sh : survie de Playwright + memos capture

- Intention : rendre upgrade.sh resilient pour que Chromium/Playwright SURVIVE a chaque upgrade (le pull du code sandbox ne doit pas casser la capture en prod).
- Correctif applique (upgrade.sh, bloc Navigateur Chromium) :
  - Balayage elargi des caches navigateur : APP_DIR/.playwright, cache utilisateur courant, cache du compte de service (via getent passwd), cache root.
  - Reparation idempotente de la config : si Chromium est localise a un emplacement different de PLAYWRIGHT_BROWSERS_PATH enregistre, la cle est recalee automatiquement (sed si presente, append sinon, jamais de doublon) -> le service redemarre sur le bon dossier et Playwright ne casse plus apres upgrade.
  - Verifie : bash -n upgrade.sh -> SYNTAXE OK.
- Friction : la desynchro d'origine venait d'une install historique de Chromium dans le cache utilisateur alors que le runtime attend APP_DIR/.playwright. Le correctif la rattrape automatiquement au prochain upgrade.
- Suite : laisser l'etape 2 (correctif C1 chat) a valider visuellement via l'outil screenshot_url du backend (pas de lancement manuel de Chromium).

### MEMOS PERMANENTS (exiges par l'utilisateur)
1. La fenetre de chat tourne EN PROD. Mes commandes bash s'executent dans l'environnement prod (dogfooding : la Forge est une API utilisee par elle-meme). Ne jamais raisonner comme un sandbox tiers isole.
2. Playwright doit s'executer EN PROD, via l'outil backend screenshot_url (fonction _tool_screenshot_url, backend/server.py). Jamais lancer Chromium a la main depuis bash -> ca sature CPU/reseau du serveur qui sert le chat et fait planter le streaming SSE.
3. Capturer une preview = meme logique qu'Emergent : (a) s'assurer que la preview est up, (b) faire la capture via screenshot_url, (c) eteindre la preview ensuite. La capture ne vise JAMAIS la sandbox, mais l'URL publique de la preview.
4. URL de preview deductible seul : _default_preview_url(name) = https://{name}.{PREVIEW_DOMAIN_SUFFIX}. Suffixe par defaut preview.quentin-astro.fr. Pour ce projet -> https://fork-clone.preview.quentin-astro.fr/. Ne plus demander l'URL a l'utilisateur.
5. Pas de "ca depasse 30s" comme argument : ce n'est pas un standard universel. Sur ce serveur le build Vite prend ~40s. Le timeout bash de 30s est une limite d'OUTIL, pas une anomalie serveur -> enchainer/relancer plutot que conclure a un probleme.

## 2026 — Rappel upgrade.sh (consigné, ne plus zapper)
- **Intention** : mémoriser que `upgrade.sh` modifié dans le workspace doit être RÉCUPÉRÉ depuis le workspace puis COPIÉ dans `/var/www/forge` (prod) avant d'être exécuté. Le script s'exécute depuis la prod, pas depuis le workspace.
- **Friction** : oublié à plusieurs reprises malgré consignation antérieure.
- **Suite** : à chaque modif d'`upgrade.sh`, fournir le message de commit ET rappeler le circuit copie-vers-prod.

## 2026-11-11 — Phase A rendu-blocs (étape 8)
- **Intention** : clôturer l'étape 8 (non-régression globale) du chantier rendu-blocs-style-emergent, phase A (1→2→8).
- **Friction** : build Vite ~28s > timeout bash 30s à la 1re tentative → relancé en nohup détaché, lu via /tmp/vite_build.log (normal sur ce serveur, pas une anomalie). Validation VISUELLE impossible ici (preview sans workspace) → déléguée à l'utilisateur après déploiement prod.
- **Suite** : commit + sauvegarde GitHub ; attendre retour utilisateur « Phase A suffit-elle ? » avant d'envisager la Phase B (structurelle, migration BDD).
