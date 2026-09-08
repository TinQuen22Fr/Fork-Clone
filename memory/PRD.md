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

## Backlog
- FAIT (2026-09-07): UI renommage de conversation (crayon + input, PATCH câblé) — vérifié navigateur.
- FAIT (2026-09-07): lien "Register" masqué (instance admin-only).
- P2: "Share Conversation" (lien public read-only /share/{id}).
- P2: sélecteur de modèle Claude dans l'UI + streaming (SSE).
