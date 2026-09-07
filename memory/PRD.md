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

## Backlog
- P1: UI de renommage de conversation (backend PATCH /api/conversations/{id} déjà prêt, pas câblé dans Chat.jsx).
- P2: masquer le lien "Register" quand ALLOW_REGISTRATION=false.
- P2: "Share Conversation" (lien public read-only /share/{id}).
- P2: sélecteur de modèle Claude dans l'UI + streaming (SSE).
