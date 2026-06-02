# Gemini3_Unchained_ZeroDollar_Forge — PRD

## Original Problem Statement
"on va faire simple créer un fork de toi !!!" — User wanted a fork of an AI assistant.

## User Choices
- App type: Chat interface (ChatGPT/Claude style)
- AI model: Gemini 3 Pro (`gemini-3.1-pro-preview`)
- Features: User authentication, file/image upload, saved conversations
- Design: Coloré et moderne (bold neo-brutalist with hot pink/yellow/cyan accents on dark)
- App name: `Gemini3_Unchained_ZeroDollar_Forge`

## Architecture
- **Backend**: FastAPI + Motor (MongoDB async) + JWT auth (bcrypt) + emergentintegrations LlmChat
- **Frontend**: React 19 + react-router-dom + Tailwind + react-markdown + lucide-react
- **AI**: Gemini 3 Pro via Emergent Universal Key
- **Auth**: JWT in httpOnly cookie (samesite=none, secure) + Bearer fallback in localStorage

## What's Implemented (2026-06-02)
- ✅ JWT auth: register / login / logout / me + admin seeding (idempotent)
- ✅ Conversations CRUD (create / list / rename / delete) scoped to authenticated user
- ✅ Chat send endpoint (multipart) with text + optional image upload to Gemini 3 Pro
- ✅ Multi-turn context (replays last 20 turns into prompt)
- ✅ Auto-titling on first exchange
- ✅ Neo-brutalist UI: login hero + chat interface with sidebar
- ✅ Markdown rendering for AI responses (code blocks, lists, tables, blockquotes)
- ✅ Optimistic UI for sent messages, typing indicator while AI responds
- ✅ Image preview before send, 8 MB limit
- ✅ Mobile responsive sidebar (slide-in)
- ✅ Protected routes with auth context

## Core Requirements (Static)
1. User can sign up & log in
2. User can chat with Gemini 3 Pro
3. User can upload images to the AI
4. User's conversations are saved & accessible
5. Distinctive bold UI (no generic ChatGPT clone)

## Prioritized Backlog
### P1 — Quick wins
- Streaming token responses (currently buffered)
- Auto-resize textarea height
- Rename conversation from sidebar (UI for existing PATCH endpoint)
- Copy-to-clipboard on code blocks

### P2 — Nice to have
- Model selector (Gemini 3 Pro / Flash / Claude / GPT — same key)
- Conversation search
- Export conversation as Markdown
- Brute-force lockout on login (playbook recommended)
- Password reset flow
- File types beyond images (PDF, txt)
- Conversation summarization for very long chats (current strategy replays last 20 turns)

### P3 — Backlog
- Public share links for conversations
- Team / workspace mode
- Custom system prompt per conversation
- Object storage for images (currently base64 in Mongo — fine for small images)

## Testing
- Backend: 21/21 pytest tests pass — `/app/backend/tests/backend_test.py`
- Frontend: e2e flows (login, chat, image upload, logout, protected route) all pass
