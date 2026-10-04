# Analyse — Integrations externes examinees (freellm / claude-skills / freebuff)

> **Statut global : VEILLE — aucune integration decidee.** Document de memoire,
> ecrit le 2026-10-05 a la demande de Quentin. Sert a ne pas reexaminer trois
> fois les memes ressources. Aucune de ces pistes n'est prioritaire aujourd'hui.
>
> Se lit **en complement** de `evolutions-futures-possibles.md` (qui reste la
> reference des evolutions retenues/ecartees). Ici = ressources externes
> inspectees, verdict et raisons.

---

## Contexte reel de la Forge (a ne pas oublier)

- La Forge est **multi-providers / multi-modeles**, PAS mono-DeepSeek.
  Aujourd'hui : **Claude (OAuth compte Pro claude.ai), Gemini, Ollama Cloud,
  OpenCode Zen/Go, Ollama local**, + adaptateur compatible OpenAI pour
  providers cloud gratuits (Groq, Cerebras, SambaNova, NVIDIA NIM, OpenRouter).
- Le modele de **cette session** (`deepseek-v4.1-flash` via OpenCode Zen) n'est
  **qu'un cas d'usage parmi d'autres** — ne jamais raisonner "pour DeepSeek".
- **Claude** utilise **OAuth** (compte Pro Anthropic) — pas de token API.
- Les **autres providers** fonctionnent avec des **tokens API stockes dans le
  fichier d'environnement local** (le grep bash sur le backend est d'ailleurs
  bloque par la protection du workspace — comportement attendu).
- Point de depart : la selection provider/modele est **codee en dur cote
  frontend** (probable : listes/options dans `Chat.jsx` / composants), cote
  backend via tokens du fichier d'environnement. Voir infra pour l'unification.

---

## 1. `freellm.net/config` — annuaire de configs multi-backends

**Ce que c'est** : generateur de snippets de config pointant des outils
(Claude Code, Cursor, Codex, OpenCode...) vers des backends LLM gratuits via
`ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` + catalogue de modeles (nom, contexte,
quotas).

**Deja acquis cote Forge** : le support de `PROXY_BASE_URL` (item
`ITEM10_BASE_URL.md`) couvre **le concept cle** (pointer vers une base URL
custom). Ce n'est donc PAS une fonctionnalite a importer.

**Piste reutilisable (idee d'UI, pas prioritaire)** :
- Un **registre de providers/modeles** unifie cote Forge : chaque entree =
  `provider + modele + base URL + source de cle (OAuth ou env) + quotas`.
- Benefice : basculer Claude <-> Gemini <-> autres **sans toucher au code**, au
  lieu des listes codees en dur cote frontend.

**Reserve** : les donnees de leur catalogue sont **douteuses**
("GLM-5.3", "DeepSeek V4 Pro", "GPT-6 Luna"... ne correspondent pas au reel).
Ne JAMAIS se fier a leurs noms/limites pour une integration en dur.

**Verdict : `EN REFLEXION` — idee d'UI (registre), concept deja couvert.**

---

## 2. `alirezarezvani/claude-skills` — bibliotheque de skills multi-outils

**Ce que c'est** : ~388 "skills" (dossiers `SKILL.md` + scripts `tools/` +
docs de reference), avec un `scripts/convert.sh` qui convertit un skill au
format de chaque outil cible (Claude Code, Codex, Gemini, Cursor, OpenCode...).
Massif (1499 commits, 706 scripts), MIT.

**Ce qui resonne avec la Forge** :
- Le **concept de "skill" modulaire** = une competence = un dossier autonome.
  Meme philosophie que le refactoring de `Chat.jsx` (decouper / reutiliser) et
  que l'architecture des outils de la Forge (`web_search.py` autonome).
- Le probleme **multi-outil / anti-lock-in** qu'ils resolvent est **le meme**
  que le tien : un contenu ecrit une fois, reutilisable quel que soit le modele.

**Piste reutilisable** :
- S'inspirer **de la STRUCTURE** (`SKILL.md` + outils + docs, un dossier = une
  competence) pour **formaliser les outils/prompts de la Forge**.

**Reserve / a NE PAS faire** :
- **Ne pas cloner ni integrer le repo.** Ecrit pour Claude Code / Codex,
  dependant de leur ecosysteme, poids mort et dette de maintenance enorme.
  Inspiration d'architecture uniquement, aucune dependance.

**Verdict : `EN REFLEXION` — modele d'architecture le plus interessant des trois,
pas une dependance a integrer.**

---

## 3. `CodebuffAI/freebuff` — orchestration multi-agents

**Ce que c'est** : 5 produits gratuits (Desktop/CLI/Web/Cloud/Chat) au-dessus du
framework **Codebuff** (TS/Bun, Apache-2.0). Contenu reutilisable = Codebuff :
orchestration d'**agents specialises** (file-finding -> implementation -> review,
recherche navigateur, workspaces isoles paralleles) via `@codebuff/sdk`.

**Resonance** : la Forge est aujourd'hui **mono-agent** (un chat, un modele).
Passer a des **agents specialises** (un "trouver les fichiers", un
"implementer", un "review") est une **evolution profonde**, pas une idee rapide.

**Reserve** : Freebuff lui-meme est finance par **publicite textuelle** ; rien a
voir avec le cap "outils personnels, sans dependance". La partie Astro/web n'est
pas transposable.

**Verdict : `EN REFLEXION (long terme)` — piste multi-agents seduisante mais
changement d'architecture majeur. Aucune integration.**

---

## Synthese

| Ressource | Ce qu'on peut en tirer | Verdict |
|---|---|---|
| `freellm.net/config` | Registre providers/modeles unifie (UI). Concept base-URL **deja fait**. | `EN REFLEXION` — idee UI, non prioritaire |
| `claude-skills` | Architecture "skills" modulaires pour organiser outils/prompts. | `EN REFLEXION` — inspiration, **pas de clonage** |
| `freebuff` | Orchestration multi-agents (file-finding -> implementation -> review). | `EN REFLEXION` — long terme, changement profond |

**Conclusion franche** : aucune des trois n'a d'integration "directe" a faire.
La plus exploitable = **le modele de structure de `claude-skills`**, coherent
avec le refactoring de `Chat.jsx` et l'architecture des outils. `freebuff`
pointe une vraie piste de fond (multi-agents) mais d'un autre univers technique.

---

## Question ouverte a trancher plus tard

La Forge gere-t-elle ses providers via **une config unifiee (registre)** ou
chaque provider est-il **cable quelque part** (frontend code en dur + env) ?
-> C'est LA question qui determine si une piste "registre" (freellm-like) a de
la valeur ou est redondante. **Non tranchee a ce jour.**
