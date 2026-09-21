# Claude Unchained Forge — PRD

## Problème / intention utilisateur
App de chat auto-hébergée type ChatGPT/Claude, 100% standalone et open-source,
AUCUNE dépendance Emergent (pas d'Emergent LLM key, pas de crédits, pas d'upsell).
L'utilisateur veut utiliser SON abonnement Claude Pro (22€/mois) directement,
et refuse catégoriquement toute API Anthropic payante au token.

## Décision technique clé (juin 2026)
Claude Pro ne donne pas d'API classique. La SEULE voie sans facture au token =
jeton OAuth de Claude Code (`claude setup-token`, jeton `sk-ant-oat...`), appelé
sur `/v1/messages` avec :
- `Authorization: Bearer <token>`
- `anthropic-beta: oauth-2025-04-20,claude-code-20250219`
- 1er bloc `system` = identité obligatoire "You are Claude Code, Anthropic's official CLI for Claude."
La conso est décomptée du forfait Pro/Max. Usage individuel/perso (instance mono-user, inscription fermée).

## Stack
- Backend: FastAPI + MongoDB (motor), JWT (cookie httpOnly + Bearer fallback), lifespan config tolérante.
- Frontend: React 19 + Vite 6 + Tailwind (Neo-Brutalist), react-markdown, framer-motion.
- Moteur: Claude via httpx direct sur l'API Messages, jeton d'abonnement.
- Source de vérité: repo GitHub TinQuen22Fr/Fork-Clone, branche `claude-ai`. Déploiement perso Dedibox (Nginx + systemd).

## Implémenté (2026-09-07)
- Portage complet du repo `claude-ai` dans /app (backend + frontend Vite).
- `generate_ai_response()` branché sur Claude via jeton OAuth d'abonnement (fichier `backend/server.py`).
- Config: `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_MODEL`, `CLAUDE_MAX_TOKENS`, `CLAUDE_SYSTEM_PROMPT`.
- Vérifié: boot backend, login admin, création conversation, chat renvoie 503 propre tant que le jeton est absent.
- Vérifié: frontend Vite rend la page login (port 3000) dans la preview.

## En attente
- (rien de bloquant) Objectif principal atteint.

## Vérifié E2E (2026-09-07)
- Jeton `CLAUDE_CODE_OAUTH_TOKEN` configuré, `CLAUDE_MODEL=claude-sonnet-5`.
- Backend curl: chat texte + vision → vraies réponses Claude (abonnement, 0 API payante).
- Frontend E2E (testing_agent iteration_2): 6/7 — login, chat, new chat, sidebar, vision, persistance, suppression OK.

## Ollama sur CPU faible (2026-09-18)
- Cible serveur user: Atom C2338, 2 cœurs @1.74GHz, SANS AVX/AVX2, 4 Go RAM, swap 1 Go (à augmenter).
- Réglages Ollama rendus tunables via .env: OLLAMA_NUM_CTX(4096), OLLAMA_NUM_PREDICT(768), OLLAMA_NUM_THREAD(2), OLLAMA_KEEP_ALIVE(10m), OLLAMA_TIMEOUT(600).
- Reco modèle code pour ce matériel: qwen2.5-coder:1.5b (ou 0.5b). 3B+ = lent, RAM limite.
- Réalité: sans AVX2, inférence lente (dizaines de sec à plusieurs min/réponse). Ollama = fallback de secours, pas rapide.
- Gemini: clé renseignée côté serveur user → fonctionnel après deploy (google-genai dans requirements).

## Implémenté (2026-09-18) — Multi-providers + corrections
- Providers: Claude (OAuth, tools), Gemini (google-genai, GEMINI_API_KEY), Ollama (local, OLLAMA_URL/MODEL). Route dans generate_ai_response.
- Correction "réponses coupées": MAX_TOOL_ITERS 10→25 + appel final SANS outils pour forcer une réponse/résumé (plus de perte du travail). _call_anthropic(use_tools).
- Nom de modèle DYNAMIQUE: GET /api/models + header active-model-label + tag par message (message.provider) au lieu de "// CLAUDE" figé.
- Ollama robuste: num_ctx 4096/num_predict 1024, timeout 300s, erreurs claires (injoignable / modèle absent 404). Testé sandbox: llama3.2:1b OK (~8s 1er appel).
- Bug corrigé: chat_regenerate utilisait `provider` non défini → `payload.provider`.
- requirements.txt: +google-genai. .env/env.example: GEMINI_API_KEY, GEMINI_MODEL, OLLAMA_URL, OLLAMA_MODEL.
- Vérifié E2E (iteration_7, 100%): sélection provider met à jour le nom, Claude+tools, Ollama réel, Gemini erreur propre sans clé, régressions OK.
- EN ATTENTE: GEMINI_API_KEY fournie par l'utilisateur pour activer/tester Gemini. Ollama nécessite le service actif + modèle pull côté serveur.

## Implémenté (2026-09-08) — Tool Calling agentique
- Backend: outils `bash` (subprocess, timeout 30s, capture stdout/stderr) et `read_file`. Boucle agent dans generate_ai_response (détecte stop_reason=='tool_use', exécute, réinjecte tool_result, relance; garde-fou MAX_TOOL_ITERS=10). Helper _call_anthropic. Flag ENABLE_TOOLS (défaut true).
- generate_ai_response retourne (text, tool_steps); chat_send + regenerate stockent tool_steps sur le message.
- Frontend: ChatMessage.jsx affiche un panneau repliable <details data-testid=tool-steps-{id}> avec chaque appel d'outil + sortie.
- Vérifié E2E (iteration_6): bash + read_file OK, panneau UI OK, aucune régression.
- ⚠️ SÉCURITÉ: bash exécute des commandes arbitraires avec les droits du backend. Réservé à l'instance perso. Désactivable via ENABLE_TOOLS=false.

## Implémenté (2026-09-08) — Barre d'actions type claude.ai
- Sous chaque réponse Claude: Copier (presse-papier), Lecture audio (Web Speech API navigateur, gratuit), Pouce haut/bas (persistés), Régénérer (dernière réponse uniquement), heure relative FR (dayjs).
- Backend: POST /api/chat/regenerate, PATCH /api/messages/{id}/feedback. user_msg stocke image_mime.
- Frontend: ChatMessage.jsx (barre d'actions), Chat.jsx (handlers regenerate/submitFeedback, isLast).
- Vérifié E2E (iteration_5): 100% front + backend curl. Aucune régression (rename, chat OK).

## Déploiement Dedibox — pièges résolus (2026-09-08)
- Repo complet requis à la racine : install.sh, deploy/, README (sinon `./install.sh: No such file`).
- requirements.txt : NE JAMAIS faire `pip freeze` du venv du pod (pollue avec litellm/google/openai...). Garder la liste propre + httpx.
- npm 11 strict → conflit peer-deps (date-fns@4 vs react-day-picker@8). Fix : `frontend/.npmrc` avec `legacy-peer-deps=true`.
- Vite build.outDir = "build". Nginx DOIT avoir `root /var/www/forge/frontend/build;` (la config live avait divergé vers /var/www/forge/build → ancien bundle servi, crayon absent). Corrigé.

## Implémenté (2026-09-21) — Routeur multi-providers + cascade de bascule
Fichiers modifiés: `backend/server.py`, `backend/.env`, `backend/env.example`,
`frontend/src/pages/Chat.jsx`, `frontend/src/components/ChatMessage.jsx`.

- Nouveaux providers: `ollama_cloud` (https://ollama.com/api, Bearer, `_generate_ollama_cloud`)
  et `opencode` (passerelle compatible OpenAI, `_generate_opencode`).
- Détection dynamique des clés: `_provider_available()` — un provider sans clé est ignoré
  sans bloquer les autres. Exposé par `GET /api/models` (+ chaîne du mode auto).
- Routeur `generate_ai_response()`: `_build_chain()` (Ollama local toujours dernier recours),
  `_classify_error()` (credits/region/freetier/quota/auth/timeout/network/model/unavailable),
  bascule silencieuse sur le provider suivant, log de chaque bascule.
  Retourne (texte, tool_steps, meta{provider, model réel, fallback_used, attempts}).
- Secours intra-provider: OLLAMA_CLOUD_FALLBACK_MODEL, OPENCODE_FALLBACK_MODEL, GEMINI_FALLBACK_MODEL.
- Messages assistant persistent `model`, `requested_provider`, `fallback_used`, `routing`.
- Frontend: option "Auto (meilleur dispo)" + liste providers dynamique, en-tête montrant la
  chaîne auto, badge "bascule auto" sur le message (tooltip = providers échoués + motif).
- ENV ajoutés: OLLAMA_CLOUD_URL/API_KEY/MODEL/FALLBACK_MODEL/TIMEOUT,
  OPENCODE_BASE_URL/API_KEY/MODEL/FALLBACK_MODEL/TIMEOUT, PROVIDER_PRIORITY, ENABLE_FALLBACK.

### Constats réels sur les comptes de l'utilisateur (testés en direct)
- Ollama Cloud: clé valide. `deepseek-v4-pro` ET `deepseek-v4-pro:0813` → "this model is not
  included in your free usage, add usage credits". Gratuits constatés: `gpt-oss:120b`,
  `nemotron-3-nano:30b`. Config: modèle par défaut = deepseek-v4-pro:0813, secours gpt-oss:120b.
- OpenCode Zen/Go: clé valide mais AUCUN modèle utilisable → `CreditsError: No payment method`;
  les `deepseek-v4-*` renvoient en plus `RegionError` (opt-in Chine requis sur le workspace).
  Infra implémentée et prête, le routeur le saute automatiquement.
- Google AI Studio: clé valide. `gemini-2.5-flash` et `gemini-2.0-flash` RETIRÉS (404
  "no longer available to new users"). Basculé sur `gemini-3.6-flash` (+ secours `gemini-3.5-flash`) — OK.

### Tests E2E (curl + navigateur, 2026-09-21)
- provider=ollama_cloud → 200, bascule interne gpt-oss:120b, model remonté correctement.
- provider=opencode → échec region → bascule auto sur claude, badge + routing corrects.
- provider=gemini → 200 gemini-3.6-flash. provider=auto → claude. provider=claude → 200.
- /api/chat/regenerate OK avec le nouveau meta. provider inconnu → 400.
- UI: sélecteur = Auto + 5 providers; en-tête affiche la chaîne de bascule.

## Adaptation (2026-09-21) — Abonnement OpenCode Go activé
Fichiers modifiés: `backend/server.py`, `backend/.env`, `backend/env.example`.

- L'abonnement Go lève le `CreditsError`, mais la passerelle impose alors :
  - un **User-Agent identifiable** (`OPENCODE_USER_AGENT=claude-unchained-forge/1.0`) ;
  - un **`x-opencode-session` stable par conversation**, sinon `MissingSessionID`.
  → `generate_ai_response()` / `_dispatch_provider()` / `_generate_opencode()` prennent
  un `session_id` ; `chat_send` et `chat_regenerate` passent le `conversation_id`
  (header envoyé : `ses_forge_<conversation_id>`). Nouveau kind d'erreur `session`.
- `OPENCODE_FALLBACK_MODEL=glm-5.3-flash` (les `deepseek-v4-*` renvoient encore
  `RegionError` : opt-in Chine à cocher dans le workspace Go).
- ⚠️ Notre implémentation parle à `/chat/completions`. Les modèles servis par
  `/messages` (qwen3.*, minimax-*) ou `/responses` (grok-4.6, gpt-5.6-luna,
  muse-spark-*) ne sont PAS compatibles — documenté dans env.example.
- Testé : `provider=opencode` → 200 via `glm-5.3-flash`. Modèles validés en direct :
  glm-5.3-flash, mimo-v2.5, kimi-k2.7-code, hy3.

## Implémenté (2026-09-21) — OpenCode : 3 transports + sélecteur de modèle
Fichiers modifiés: `backend/server.py`, `backend/.env`, `backend/env.example`,
`frontend/src/pages/Chat.jsx`.

- **OpenCode v2** : aucune adaptation API nécessaire. L'API HTTP reste sur `/zen/go/v1`
  (seule la console a déménagé sur console.opencode.ai). v2 = CLI/desktop/web.
- **Trois transports gérés** dans `_generate_opencode`, détectés via `_opencode_transport()`
  d'après l'id du modèle, avec **réessai automatique entre transports** si l'endpoint ne colle pas
  (`_opencode_wrong_transport()` : codes 400/404/405/415/422 + messages « not supported for format »,
  et 401 non-authentification) :
  - `/chat/completions` (OpenAI) : deepseek-*, glm-*, kimi-*, mimo-*, hy*, longcat
  - `/messages` (Anthropic, auth `x-api-key`) : minimax-*, qwen3.*
  - `/responses` (OpenAI Responses, `input`/`instructions`) : gpt-5.6-luna, muse-spark-*
  Parsing par transport (`_opencode_extract`) + conversion d'image par transport
  (`_opencode_convert_image` : image_url / input_image / source base64).
  Override manuel possible : `OPENCODE_TRANSPORT=auto|chat|messages|responses`.
- **Sélecteur de modèle** : `GET /api/models` renvoie le catalogue live de chaque provider distant
  (`_fetch_catalog`, cache 10 min : `/zen/go/v1/models` et `/api/tags`). `POST /api/chat/send`
  et `/api/chat/regenerate` acceptent un champ `model` qui ne s'applique **qu'au provider demandé**
  (les providers de secours gardent leur modèle configuré). UI : second `<select>`
  (`data-testid=model-select`) affiché quand le provider a un catalogue, persisté en localStorage,
  réinitialisé au changement de provider. En-tête affiche le modèle choisi avec la mention « (choisi) ».
- **Opt-in Chine activé côté utilisateur** : `deepseek-v4-flash` / `deepseek-v4-pro` répondent désormais.

### Tests E2E (curl + navigateur, 2026-09-21)
- opencode défaut → deepseek-v4-flash OK. Override testés OK : minimax-m3 et qwen3.8-max (`/messages`),
  gpt-5.6-luna et muse-spark-1.3-contributor (`/responses`), grok-4.6 (retry chat→responses),
  kimi-k2.7-code, mimo-v2.5-pro, glm-5.3-flash.
- ollama_cloud override nemotron-3-nano:30b OK.
- UI : 37 modèles listés dans le sélecteur OpenCode, sélection appliquée à l'envoi.

## Implémenté (2026-09-21) — Bouton Stop + pièces jointes universelles
Fichiers modifiés: `backend/server.py`, `backend/requirements.txt` (+pypdf),
`backend/env.example`, `frontend/src/pages/Chat.jsx`,
`frontend/src/components/ChatMessage.jsx`.

### Stop / annuler
- `AbortController` (`abortRef`) sur `POST /chat/send` + `signal` passé à axios.
- Pendant la génération, le bouton SEND devient **STOP** (`data-testid=stop-message-btn`)
  et un lien « annuler » apparaît dans la bulle de chargement (`stop-generation-btn`).
- À l'annulation : message optimiste retiré, bandeau « Requête annulée. », puis
  `loadMessages()` pour resynchroniser (le message utilisateur est déjà persisté côté serveur).
- ⚠️ La requête HTTP est coupée ; si le serveur avait déjà fini d'écrire la réponse,
  elle réapparaît au resync. Pas de streaming, donc pas d'arrêt à mi-génération côté modèle.

### Pièces jointes (plus seulement des images)
- Backend : `parse_attachment()` classe le fichier —
  `image/*` → chemin vision existant ; PDF → **extraction texte via pypdf** ;
  tout fichier texte/code (≈60 extensions + `text/*`, json, xml, yaml...) → décodé UTF-8/latin-1.
  Type non géré → HTTP 400 avec un message explicite (conseille PDF/CSV/texte pour docx/xlsx).
- `build_attachment_prompt()` injecte le contenu dans le prompt en bloc de code balisé
  (langage déduit de l'extension), tronqué à `MAX_FILE_CHARS` avec mention explicite.
  → fonctionne avec **TOUS les providers**, même sans vision ni support document.
- `POST /chat/send` accepte le champ `file` (l'ancien `image` reste accepté).
  Nouvelles limites : `MAX_UPLOAD_MB=16`, `MAX_FILE_CHARS=40000`.
- Message utilisateur : `file_name`, `file_kind`, `file_size`, `file_text` (exclu des GET).
  `chat_regenerate` réinjecte `file_text` pour régénérer à l'identique.
- Frontend : input sans `accept`, icône trombone, carte d'aperçu (vignette image ou icône fichier
  + nom + taille), puce de pièce jointe sur le message (`attachment-chip-{id}`).

### Tests E2E (curl + navigateur, 2026-09-21)
- `.py` via gemini → le modèle retrouve le marqueur du fichier. `.pdf` via gemini → marqueur extrait.
- `.csv` via opencode/deepseek-v4-flash → bonne valeur lue dans le CSV.
- Binaire `.bin` → 400 avec message clair.
- Navigateur : carte d'aperçu OK, STOP + « annuler » visibles pendant la génération,
  annulation → bandeau « Requête annulée. » et retour du bouton SEND.

## Corrigé (2026-09-21) — Affichage mobile / tablette
Fichiers modifiés: `frontend/src/App.css`, `frontend/src/index.css`,
`frontend/src/pages/Chat.jsx`, `frontend/src/components/ChatMessage.jsx`.

### Cause racine du dock de saisie invisible sur téléphone
`App.jsx` enveloppe tout dans `<div className="App">`, et `App.css` ne lui donnait
qu'un `min-height: 100vh` — donc **aucune hauteur définie**. Le shell du chat en
`h-screen`/`h-full` retombait en hauteur auto : le document faisait 10 700 px de haut
et le dock se retrouvait tout en bas, hors écran, inatteignable à cause de
`overflow-hidden`. En paysage / mode ordinateur la hauteur suffisait, d'où le symptôme.

### Corrections
- `App.css` : `.App { height: 100%; min-height: 100%; display: flex; flex-direction: column }`
  + `.App > * { flex: 1; min-height: 0 }` → hauteur de référence réelle pour les enfants.
- `index.css` : `html, body, #root` passent en **`100dvh`** via `@supports`
  (100vh inclut la barre d'URL mobile et déborde). Classe `.safe-bottom`
  (`env(safe-area-inset-bottom)`) pour la barre de geste iPhone.
- Chat : shell en `h-full` (plus `h-screen`), zone messages `flex-1 min-h-0 overflow-y-auto
  overflow-x-hidden`, dock et bandeau d'erreur en `flex-shrink-0`.
- **Composer sur 2 rangées en mobile** : rangée contrôles (provider + modèle + trombone)
  puis rangée saisie + envoi, via des conteneurs `sm:contents` qui disparaissent dès `sm`
  pour conserver la rangée unique du desktop. Sélecteurs en `flex-1 min-w-0` (plus de
  débordement horizontal). Le sélecteur de modèle n'est plus caché en mobile.
- **Breakpoint tablette md → lg** : le tiroir latéral reste un overlay jusqu'à 1024 px,
  donc l'iPad portrait a toute la largeur pour le chat. Ajout d'un fond cliquable
  (`sidebar-backdrop`, `lg:hidden`) pour fermer le tiroir.
- Bulles `max-w-[88%] sm:max-w-[80%]`, header compacté, `overflow-wrap: anywhere` et
  `pre` réduits en mobile, pied de page masqué sous `sm`, textarea en 16 px (pas de zoom iOS).

### Tests (navigateur, viewports réels)
- 390×700 (téléphone portrait) : dock visible, `scrollHeight == clientHeight` (plus de scroll
  de page), `scrollWidth == 390` (pas de débordement), burger présent, envoi Gemini OK.
- 844×390 (téléphone paysage) et 820×1100 (tablette portrait) : dock visible, aucun débordement.
- Tiroir mobile : ouverture, fond cliquable, fermeture OK.

## Implémenté (2026-09-21) — PWA, streaming SSE, glisser-déposer, quota Go
Fichiers: `backend/server.py`, `frontend/index.html`, `frontend/src/main.jsx`,
`frontend/public/{manifest.webmanifest,sw.js,icon-*.png,maskable-*.png,apple-touch-icon.png,favicon-32.png}`,
`frontend/src/lib/api.js`, `frontend/src/pages/Chat.jsx`,
`frontend/src/components/ChatMessage.jsx`, `frontend/src/index.css`.

### 1. Installable (PWA)
- `manifest.webmanifest` (display standalone, start_url /chat, thème #050505) + icônes
  192/512 + maskable 192/512 + apple-touch-icon 180 (icône flamme générée).
- Métas iOS (`apple-mobile-web-app-*`) et `sw.js` **volontairement sans cache**
  (réseau uniquement) : un SW cachant le bundle avait déjà piégé la prod. Enregistré
  seulement en HTTPS depuis `main.jsx`.

### 2. Streaming SSE (`POST /api/chat/stream`)
- Événements : `user_message`, `start` (provider+modèle réels), `delta`, `tool`, `done`, `error`.
- Headers : `Cache-Control: no-transform` et **`X-Accel-Buffering: no`** (indispensable derrière Nginx).
- Parseur SSE Anthropic partagé (`_stream_anthropic_turn`) : reconstruit les blocs texte+tool_use
  → **la boucle d'outils Claude fonctionne en streaming** (bash/read_file exécutés entre deux tours).
- Streaming natif : Claude, Gemini (`client.aio...generate_content_stream`), Ollama local et Cloud
  (NDJSON), OpenCode transports `chat` (SSE OpenAI) et `messages` (SSE Anthropic).
  Transport `responses` → repli non-stream (une seule salve).
- Cascade conservée : bascule provider possible **tant qu'aucun mot n'a été émis** ; après, l'erreur
  est remontée telle quelle. Repli intra-provider conservé (ex. ollama_cloud deepseek → gpt-oss:120b).
- **Arrêt = instantané** : couper la connexion annule le générateur. Le texte déjà produit est
  sauvegardé avec `stopped: true` via une **tâche détachée** (`_spawn`/`_persist_stopped`) — on ne peut
  pas `await` dans le `finally` d'un générateur en fermeture (GeneratorExit), les écritures étaient
  silencieusement perdues. Badge « ARRÊTÉ » sur le message.
- Front : `postSSE()` dans `lib/api.js` (fetch + ReadableStream, EventSource ne gère pas POST),
  bulle en cours avec curseur clignotant `stream-caret`, barre d'actions masquée pendant le flux.
- `/api/chat/send` (non-stream) conservé pour compatibilité ; `regenerate` l'utilise toujours.

### 3. Glisser-déposer global
- Écouteurs `dragenter/dragover/dragleave/drop` sur `window` avec compteur de profondeur,
  overlay `drop-overlay` plein écran, `acceptFile()` partagé avec le trombone.

### 4. Quota OpenCode Go
- Endpoint découvert : `GET /zen/go/v1/usage` → `{rolling, weekly, monthly: {status, percent, resetsAt}}`.
- Exposé par `GET /api/opencode/usage` (renvoie `available:false` + raison si clé absente/erreur,
  jamais d'erreur bloquante). Rafraîchi au chargement et après chaque envoi.
- `UsageBadge` dans l'en-tête : 3 mini-jauges 5H / SEM / MOIS, couleur cyan → jaune (70%) → rose (90%),
  infobulle avec les dates de reset.

### Tests (curl + navigateur)
- Streaming : Claude (10 deltas), Claude+outil bash (événement `tool` puis réponse finale),
  Gemini, OpenCode chat (deepseek-v4-flash) et messages (qwen3.8-max), Ollama Cloud avec repli gpt-oss:120b.
- Navigateur : texte qui grandit (408 → 825 caractères en 2 s), curseur visible, STOP → texte partiel
  conservé + badge ARRÊTÉ **persistant après rechargement**.
- PWA : manifeste servi, 4 icônes, `display: standalone`, service worker enregistré.
- Glisser-déposer : overlay affiché, fichier attaché, envoi en flux et marqueur retrouvé par Gemini.
- Quota : badge affiché avec 0% sur les 3 fenêtres.

## Implémenté (2026-09-21) — Multi-fichiers, favoris, icône « unchained »
Fichiers: `backend/server.py`, `frontend/src/pages/Chat.jsx`,
`frontend/src/components/ChatMessage.jsx`, `frontend/src/pages/Login.jsx`,
`frontend/public/*` (icônes régénérées), `evolutions-futures-possible.md` (nouveau).

### Multi-fichiers
- Couche de génération refactorée : `image_b64/image_mime` → **`images: list[dict]`**
  ({data, mime}) dans `_build_messages`, `_gemini_contents`, `_generate_*`, `_stream_*`,
  `_dispatch_provider`, `generate_ai_response`, `stream_ai_response`. Multi-images
  transmis nativement (Claude, Gemini, Ollama Cloud `images[]`, OpenCode `image_url[]`).
- `process_uploads()` lit N pièces jointes ; `build_attachments_prompt()` **partage le
  budget `MAX_FILE_CHARS` entre les fichiers** (plancher 2000 car/fichier).
- Endpoints : champ `files` (répété). ⚠️ `Optional[list[UploadFile]] = File(None)` est
  rejeté par Pydantic → utiliser **`files: list[UploadFile] = File(default=[])`**.
  `file`/`image` restent acceptés pour compatibilité. `MAX_ATTACHMENTS=10`, 16 Mo cumulés.
- Document message : `attachments[]` (métadonnées), `images_b64[]` et `prompt_override`
  (exclus des GET). `_images_from_message()` assure la compat des anciens messages.
  `regenerate` réutilise `prompt_override` tel quel.
- Front : état `attachments[]`, input `multiple`, drop multiple, bandeau de vignettes
  horizontal avec retrait unitaire + « tout retirer », puces multiples sur le message.

### Favoris de modèles
- localStorage `forge_favorites` : jusqu'à 6 entrées `{provider, model, label}`.
- Bouton étoile dans les contrôles (épingle la sélection courante), barre de puces
  au-dessus du composer, clic = applique provider + modèle en un coup.

### Identité visuelle
- Nouvelle icône : maillon de chaîne brisé (sigle « unchained ») au cœur de flammes
  géométriques cyan/magenta/or, style cyberpunk. Régénérée en 192/512/maskable/apple-touch/
  favicon/logo-64 et utilisée **partout** : PWA, favicon, logo sidebar, avatar assistant,
  écran vide, page de connexion (remplace l'icône lucide `Flame` et l'ancien avatar distant).

### Tests
- curl : 3 fichiers (`a.txt`, `b.txt`, `c.py`) en streaming Gemini → les 3 marqueurs
  correctement listés avec attribution au bon fichier.
- Navigateur : 2 favoris épinglés (gemini-3.6-flash, glm-5.3-flash), clic → provider appliqué ;
  dépôt de 3 fichiers → 3 vignettes ; envoi OK.

## Implémenté (2026-09-21) — 5 providers gratuits + résumé auto de l'historique
Fichiers: `backend/server.py`, `backend/env.example`, `backend/.env`,
`backend/tests/mock_openai_provider.py` (nouveau), `evolutions-futures-possibles.md`.

### Chantier 1 — cascade multi-providers gratuits
- `Settings.free_providers` : dict construit en boucle pour groq/cerebras/sambanova/
  nvidia/openrouter avec `<UP>_API_KEY`, `<UP>_BASE_URL`, `<UP>_MODEL`, `<UP>_TIMEOUT`.
  **Zéro clé et zéro modèle en dur.** `PROVIDER_IDS` passe à 10 providers.
- Adaptateur unifié compatible OpenAI : `_generate_openai_compat` +
  `_stream_openai_compat` (POST `/chat/completions`, `stream: true`, parseur SSE
  partagé `_sse_events`), branché dans `_dispatch_provider` et `_stream_provider`.
  `_openai_messages()` gère system prompt + images multiples.
- Découverte dynamique : `_fetch_catalog` étendu à `GET {base}/models`
  (cache `MODEL_CATALOG_TTL`, 1 h par défaut). OpenRouter filtré sur `:free`
  (`OPENROUTER_FREE_ONLY`). En-têtes `HTTP-Referer`/`X-Title` pour OpenRouter.
- `_resolve_free_model()` : override > env > **premier modèle découvert**. Si le
  catalogue est vide → 503 explicite et la cascade passe au suivant.
- `PROVIDER_PRIORITY` par défaut : claude, opencode, puis les 5 gratuits, puis
  gemini, ollama_cloud, et **ollama local toujours forcé en dernier**.
- `_classify_error` : indisponibilité (503/502/500/unavailable) testée AVANT le
  bucket "model", sinon « model unavailable » était mal catégorisé.
- `/api/models` renvoie les catalogues des 5 nouveaux providers ; l'UI existante les
  affiche automatiquement (badge « non configuré » si clé absente).

### Chantier 2 — résumé automatique de l'historique long
- `prepare_history()` appelée par `/chat/send`, `/chat/stream` et `/chat/regenerate`
  (remplace la troncature `history[-history_turns:]`).
- Déclenchement au-delà de `HISTORY_SUMMARY_THRESHOLD_TOKENS` (estimation 4 car/token).
  `HISTORY_SUMMARY_KEEP_RECENT` derniers messages intacts, les plus anciens condensés
  via `_summarize_messages()` (appel `provider=auto`, `history=[]` → pas de récursion).
- Résumé **incrémental et persisté** : `conversations.summary` + `summarized_ids`.
  Injecté en tête sous forme d'un couple user/assistant pour respecter l'alternance
  exigée par Claude (`_with_summary`).
- Échec de condensation → log + troncature simple, jamais de blocage.

### Tests (mock local, aucune vraie clé consommée)
- `backend/tests/mock_openai_provider.py` : faux provider OpenAI (GET /v1/models,
  POST /v1/chat/completions stream et non-stream, modèles pilotant des pannes
  `mock-429` / `mock-503`).
- Validé : découverte dynamique (3 modèles), providers sans clé ignorés
  (`available: false`, pas d'erreur), streaming SSE, modèle forcé, cascade sur
  **429 → quota → claude** et **503 → unavailable → claude** avec `routing` correct.
- Résumé : 26 messages / 22 444 caractères condensés en 680 caractères par Claude,
  le fait technique clé (« Atom C2338 sans AVX2 ») préservé, résumé persisté en base.
- UI : 11 providers listés, badge « bascule auto depuis groq » affiché sur la réponse.

## Implémenté (2026-09-21) — Outils web de l'agent + dictée vocale
Fichiers: `backend/server.py`, `backend/env.example`, `backend/requirements.txt`
(+ddgs, +playwright), `frontend/src/pages/Chat.jsx`, `evolutions-futures-possibles.md`.

- 3 nouveaux outils agent (`TOOLS` + `_run_tool`) : `web_search`, `fetch_url`,
  `screenshot_url`. Aucun n'existait avant (seuls `bash` et `read_file`).
- `web_search` en cascade : Google CSE (si `GOOGLE_CSE_KEY`+`GOOGLE_CSE_CX`) →
  ddgs (DuckDuckGo, sans clé) → **API de recherche Wikipedia**. Le repli Wikipedia
  était indispensable : depuis une IP de datacenter, DDG/Google/Brave/Mojeek
  renvoient 202/429/403. ⚠️ Wikipedia exige un User-Agent identifiant l'app avec
  un moyen de contact, sinon 403 (un UA type « Mozilla/5.0 » est refusé).
- `fetch_url` : httpx + nettoyage regex (script/style/balises + unescape), tronqué
  à `FETCH_URL_MAX_CHARS`. Pas de beautifulsoup/lxml pour rester léger.
- `screenshot_url` : Playwright sync dans `asyncio.to_thread` (OK car hors boucle
  d'événements), flags `--no-sandbox --disable-dev-shm-usage --disable-gpu`.
  Image dans `backend/static/screenshots/`, servie par `GET /api/screenshots/{nom}`
  (auth requise, nom validé `[0-9a-f]{32}\.png`, purge au démarrage selon
  `SCREENSHOT_RETENTION_HOURS`). L'outil renvoie le markdown à insérer, ce qui
  affiche l'image dans la réponse via ReactMarkdown. Désactivable
  (`ENABLE_SCREENSHOT=false`) car ~300 Mo de RAM par capture.
  ⚠️ En sandbox, les navigateurs sont dans `/pw-browsers` alors que Playwright les
  cherche dans `~/.cache/ms-playwright` → symlink créé. Sur le serveur de l'user,
  `python3 -m playwright install chromium` doit être lancé par LE MÊME utilisateur
  que le service systemd.
- Dictée : bouton micro (`dictate-btn`). Web Speech API en premier choix, repli
  MediaRecorder + `POST /api/stt` (Whisper via provider gratuit, `STT_MODEL`).

### Tests
- `web_search` → 5 résultats (repli Wikipedia fr, sandbox bloquée côté DDG).
- `screenshot_url` via l'agent Claude → capture réussie, markdown inséré, image
  chargée dans le chat (`naturalWidth: 1280`), `401` sans authentification.
- `fetch_url` → texte d'example.com extrait.
- `/api/stt` sans clé → 503 avec message explicite.
- UI : bouton micro visible, capture affichée dans la conversation.

## Backlog
- FAIT (2026-09-07): UI renommage de conversation (crayon + input, PATCH câblé) — vérifié navigateur.
- FAIT (2026-09-07): lien "Register" masqué (instance admin-only).
- P2: "Share Conversation" (lien public read-only /share/{id}).
- P2: sélecteur de modèle Claude dans l'UI + streaming (SSE).

## Licence (2026-09-21)

- `LICENSE` ajouté à la racine : texte intégral officiel GNU GPL v3 (récupéré depuis gnu.org), précédé de la notice `Copyright (C) 2026 Quentin Dumont`.
- `README.md` : section « 📄 Licence » indiquant GPLv3 + lien vers le fichier LICENSE.
- Dates des entrées de la dernière session corrigées (2026-06 / 21-06 → 2026-09-21).
