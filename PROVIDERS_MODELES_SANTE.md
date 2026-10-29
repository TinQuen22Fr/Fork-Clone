# Providers & modèles : assainissement, santé et routage

> Spécification technique — rédigée le 2026-10-25
> Statut : **À FAIRE** — en attente du feu vert utilisateur.
> Décision validée : healthcheck NVIDIA **à la demande** (option a).

## Contexte & griefs utilisateur

- **Claude = OAuth abonnement UNIQUEMENT.** Aucune clé API Anthropic. L'API perso
  réutilise le compte claude.ai via le Bearer OAuth d'abonnement.
- Griefs concrets observés :
  1. Des modèles Claude proposés **n'existent pas** / **sans quota** / **pas pour
     le compte OAuth** → divergence réelle entre liste affichée et accès réel.
  2. **NVIDIA NIM** : des centaines de modèles listés, **beaucoup en erreur**
     (tier payant, preview, retirés, 400/403/quota). NVIDIA quasi jamais utilisé.

## Causes racines prouvées (lecture backend/server.py)

| Grief | Cause racine dans le code | Preuve |
|---|---|---|
| Modèles Claude fantômes | Liste statique de repli `CLAUDE_MODELS` (ligne ~69) contient des modèles inventés (`claude-opus-5-5`, `claude-fable-5-1`…) ; de plus `CLAUDE_MODEL` configuré est **toujours réinjecté en tête** même s'il n'existe pas | `_fetch_catalog` ligne ~6289-6333 |
| NVIDIA noyé d'erreurs | **Aucun filtrage** du catalogue NVIDIA ; seul OpenRouter filtre (`:free`) | `_fetch_catalog` ligne ~6350 |
| Pas de nature d'accès visible | `/models` renvoie `available` (bool) mais **aucun** `auth_type` / `tier` | `list_models` ligne ~6381 |
| Pas de routage par pertinence | Mode auto suit une **priorité fixe**, ignore le contenu du prompt | `_build_chain` ligne ~2864 |

---

## Briques (additives, livrables séparément, aucune ne casse l'existant)

### Brique 0 — Assainir Claude OAuth  *(PRIORITAIRE)*

**Objectif** : n'afficher pour Claude que les modèles réellement accessibles au
compte OAuth.

**Fichier** : `backend/server.py`

**Actions** :
1. Dans `_fetch_catalog` (bloc `if pid == "claude"`), quand
   `GET api.anthropic.com/v1/models` **répond** → utiliser **exclusivement** la
   liste retournée. Ne plus fusionner avec `CLAUDE_MODELS`.
2. Réduire la constante `CLAUDE_MODELS` (ligne ~69) aux **seuls modèles réellement
   existants** ; purger les entrées fantômes (`claude-opus-5-5`,
   `claude-fable-5-1`, et toute entrée non vérifiable). Elle ne sert plus QUE de
   repli en cas d'échec total de l'API.
3. Ne réinjecter `CLAUDE_MODEL` (configuré) en tête **que s'il est présent** dans
   la liste découverte. Sinon ne pas le forcer.

**Vérification** :
```bash
cd backend && python -c "import ast; ast.parse(open('server.py').read()); print('AST OK')"
cd backend && python -m pytest tests/test_regression.py -q 2>&1 | tail -20
```

**Critère de fin** : avec OAuth valide, `/models` Claude = strictement la réponse
de l'API ; en cas d'échec, repli sur une liste statique propre (sans fantômes).

---

### Brique 1 — Healthcheck à la demande + filtrage NVIDIA  *(PRIORITAIRE)*

**Objectif** : vérifier réellement qu'un modèle répond, sur action explicite.
NVIDIA quasi inutilisé → **pas** de test systématique au chargement.

**Fichiers** : `backend/server.py` + `frontend` (bouton)

**Actions backend** :
1. Nouvel endpoint `POST /models/health` : corps `{ provider, model }`.
   Envoie une **micro-complétion** (`max_tokens: 1`, prompt `"ping"`) au provider
   ciblé. Classe le résultat :
   - `ok` : complétion réussie
   - `listed_but_failing` : modèle listé mais 400/403/quota
   - `unreachable` : réseau/5xx
2. Cache court en mémoire par `(provider, model)` (TTL ~10 min) pour éviter de
   re-pinger au moindre clic.
3. **Ne jamais lever** : toute exception → statut `unreachable` + message tronqué.
4. (NVIDIA) : pas de filtrage automatique du catalogue au chargement (coûteux) ;
   le filtrage visuel se fait via le statut healthcheck dans l'UI.

**Actions frontend** :
- Bouton « Vérifier » à côté du sélecteur de modèle (ou par entrée NVIDIA).
- Affiche la pastille de statut : ✅ opérationnel / ⚠️ listé mais échoue /
  ❌ injoignable. Mémorise le dernier statut connu.

**Vérification** :
```bash
cd backend && python -c "import ast; ast.parse(open('server.py').read()); print('AST OK')"
cd frontend && node node_modules/vite/bin/vite.js build --logLevel error; echo "EXIT=$?"
```

**Critère de fin** : clic « Vérifier » sur un modèle NVIDIA douteux → pastille
rouge/orange sans planter ; modèle sain → pastille verte.

---

### Brique 2 — Métadonnées d'accès & capacités

**Objectif** : distinguer visuellement la nature d'accès et les capacités.

**Fichiers** : `backend/server.py` + `frontend`

**Actions backend** — enrichir chaque entrée `/models` :
- `auth_type` : `oauth_subscription` (Claude), `api_key` (Groq/Cerebras/SambaNova/
  NVIDIA/OpenRouter/OpenCode/Proxy), `local` (Ollama).
- `tier` : `subscription` (Claude), `free` (OpenRouter `:free`, gratuits),
  `paid`, `local`.
- `capabilities` déduites de l'id : `vision`, `reasoning`, `tools`,
  `long_context` (heuristique sur le nom de modèle).

**Actions frontend** :
- Pastilles/badges dans le sélecteur (OAuth / API / Local, Free / Paid, icônes
  capacités).

**Vérification** :
```bash
cd backend && python -c "import ast; ast.parse(open('server.py').read()); print('AST OK')"
cd frontend && node node_modules/vite/bin/vite.js build --logLevel error; echo "EXIT=$?"
```

**Critère de fin** : d'un coup d'œil, Claude = « OAuth / Abonnement », les autres
= « API », Ollama = « Local ».

---

### Brique 3 — Routage par pertinence  *(le plus sensible, en dernier)*

**Objectif** : en mode auto, réordonner la chaîne selon la demande.

**Fichier** : `backend/server.py` (`_build_chain`)

**Actions** :
- `pick_best_provider(prompt, task_hint)` : signaux simples —
  image présente → provider `vision` ; prompt de code → provider fort en code ;
  « réfléchis/analyse » → provider `reasoning`.
- Réordonne la chaîne auto ; **conserve** le fallback existant ; un choix
  explicite n'est jamais court-circuité.

**Vérification** :
```bash
cd backend && python -c "import ast; ast.parse(open('server.py').read()); print('AST OK')"
cd backend && python -m pytest tests/test_tool_loop.py tests/test_regression.py -q 2>&1 | tail -20
```

**Critère de fin** : prompt avec image part vers un provider vision ; prompt de
code vers un provider code ; fallback intact.

---

## Ordre recommandé

```
Brique 0 (Claude OAuth)   ← corrige un BUG, prioritaire
    ↓
Brique 1 (healthcheck + NVIDIA)  ← corrige un BUG, prioritaire
    ↓
Brique 2 (métadonnées)    ← confort visuel, faible risque
    ↓
Brique 3 (routage)        ← le plus sensible, en dernier
```

Briques 0 et 1 sont indépendantes l'une de l'autre (parallélisables).
Brique 2 peut être codée en parallèle. Brique 3 après stabilisation.

## Risques

| Risque | Impact | Mitigation |
|---|---|---|
| Repli statique Claude encore trop permissif | Modèles fantômes persistants | Purge stricte de `CLAUDE_MODELS` (Brique 0) |
| Healthcheck consomme du quota provider | Faible (max_tokens:1 + cache) | TTL cache + à la demande uniquement |
| Heuristique capacités imprécise | Badge erroné | Heuristique conservatrice, non bloquante |
| Routage auto choisit mal | Réponse sous-optimale | Fallback conservé, choix explicite prioritaire |

## Livrables

- `backend/server.py` : `_fetch_catalog` (B0), `POST /models/health` (B1),
  `/models` enrichi (B2), `_build_chain`/`pick_best_provider` (B3).
- `CLAUDE_MODELS` purgée (B0).
- Frontend : bouton « Vérifier » + pastilles statut (B1), badges accès/capacités
  (B2).
- Ce rapport.
