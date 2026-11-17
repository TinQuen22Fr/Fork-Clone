# Intégration provider `9router`

## Contexte & cadrage (non négociable)

- **pid** : `9router` (définitif, plus de `router9`).
- **Préfixe d'env** : `ROUTER9_*` (un nom d'env ne peut pas commencer par un chiffre côté shell/dotenv → dissociation du pid logique).
- **Base URL par défaut** : `http://localhost:20128` (normalisée en `/v1`).
- **Auth** : `Authorization: Bearer <ROUTER9_API_KEY>`.
- **Catalogue** : `GET /v1/models`.
- **Inférence** : `POST /v1/chat/completions` (streaming SSE OpenAI-compatible).
- **Hors scope strict** : aucune logique de combo, fallback, routage auto, budget tokens côté Forge — tout est géré par le dashboard 9Router.

## Cartographie (vérifiée en lecture)

| Zone | Fichier | Lignes | Rôle |
|---|---|---|---|
| Déclaration providers | `backend/server.py` | 270-297 | Boucle `free_providers` (4-tuple `(pid, label, default_base, env_prefix)`) |
| Normalisation URL | `backend/server.py` | 169-196 | `_normalize_base_url(..., assume_v1=...)` |
| Registres | `backend/server.py` | 2389-2415 | `FREE_PROVIDER_IDS`, `PROVIDER_IDS`, `PROVIDER_LABELS` |
| Disponibilité | `backend/server.py` | ~2440 | `_provider_available(pid)` |
| Headers auth | `backend/server.py` | 2461-2470 | `_free_provider_headers` → `Bearer` par défaut |
| Catalogue `/v1/models` | `backend/server.py` | `_fetch_catalog` | OpenAI-compatible |
| Streaming | `backend/server.py` | `_stream_openai_compat` | SSE `data:` + `[DONE]` |
| Doc env | `backend/env.example` | 331-337 | Bloc modèle `PROXY_*` |
| Sélecteur UI | `frontend/src/pages/Chat.jsx` | lit `/models` → `data.providers` | **Aucune modification** |
| Tests | `backend/tests/` | 42 verts | Fichier dédié à ajouter |

## Ordre d'exécution

1. Étape 1 — Déclaration dans `Settings.free_providers` *(workspace)*
2. Étape 2 — Enregistrement dans les registres globaux *(workspace)*
3. Étape 3 — Disponibilité conditionnée à clé + URL *(workspace)*
4. Étape 4 — Bloc `ROUTER9_*` dans `env.example` *(workspace)*
5. Étape 5 — Fichier de test dédié + suite complète verte *(workspace)* → commit + push GitHub
6. Déploiement prod : `sudo ./upgrade.sh` + saisie `ROUTER9_API_KEY` + redémarrage backend *(prod, par l'utilisateur)*
7. Étape 6 — Contrôle contrat réseau `curl /v1/models` *(prod)*
8. Étape 7 — Validation UI : sélecteur `9Router`, modèles peuplés, streaming OK *(prod)*

**Règle d'arrêt** : si une étape échoue, ne pas passer à la suivante ; après échec post-étape 6, revenir au workspace pour correctif.

## Suivi d'exécution

### Étape 1 — ✅ Terminée
- **Fichier modifié** : `backend/server.py` l.270-297.
- **Changement structurel** : boucle `free_providers` passée d'un 3-tuple à un 4-tuple `(pid, label, default_base, env_prefix)` pour dissocier pid logique et préfixe d'env.
- **Entrée ajoutée** : `("9router", "9Router", "http://localhost:20128", "ROUTER9")`.
- **Normalisation** : `assume_v1=(pid in ("proxy", "9router"))`.
- **Vérification** : `python -c "import server; p=server.settings.free_providers['9router']; print(p['base_url'], p['label'])"` → `http://localhost:20128/v1 9Router` ✅.

### Étape 2 — ✅ Terminée
- **Fichier modifié** : `backend/server.py` l.2393 + l.2414.
- **Ajouts** : `"9router"` dans `FREE_PROVIDER_IDS` (propagé à `PROVIDER_IDS`), `"9router": "9Router"` dans `PROVIDER_LABELS`.
- **Vérification** : `True True 9Router` ✅.

### Étape 3 — ✅ Terminée
- **Fichier modifié** : **aucun** — la règle par défaut de la branche `FREE_PROVIDER_IDS` dans `_provider_available` (`return bool(conf["api_key"] and conf["base_url"])`) couvre exactement le besoin. Seule `proxy` a une dérogation (URL seule).
- **Vérification** : `sans cle: False` / `avec cle: True` ✅.

### Étape 4 — ✅ Terminée
- **Fichier modifié** : `backend/env.example` — bloc ajouté juste après `PROXY_TIMEOUT=180` (4 variables `ROUTER9_*` + 3 lignes de commentaires expliquant le pid `9router` vs préfixe `ROUTER9_`).
- **Variables ajoutées** : `ROUTER9_BASE_URL`, `ROUTER9_API_KEY`, `ROUTER9_MODEL`, `ROUTER9_TIMEOUT`.
- **Vérification** : `grep '^ROUTER9_' backend/env.example` renvoie 4 lignes ✅.

### Étape 5 — ✅ Terminée
- **Fichier créé** : `backend/tests/test_9router_provider.py` (95 lignes, 5 tests).
- **Couverture** :
  1. `test_9router_declared_in_free_providers` — pid déclaré dans `Settings.free_providers`, label correct, base_url normalisée `http://localhost:20128/v1`.
  2. `test_9router_in_global_registries` — pid présent dans `FREE_PROVIDER_IDS` et `PROVIDER_IDS`.
  3. `test_9router_label_registered` — `PROVIDER_LABELS["9router"] == "9Router"`.
  4. `test_9router_availability_requires_key_and_url` — `_provider_available` : `False` sans clé, `True` avec clé.
  5. `test_env_example_documents_router9_block` — les 4 variables `ROUTER9_*` sont documentées dans `env.example`.
- **Vérification fichier dédié** : `python -m pytest backend/tests/test_9router_provider.py -q` → `5 passed in 3.53s` ✅.
- **Vérification suite unitaire élargie** (89 tests sans dépendance réseau/live-backend) : `89 passed, 1 warning in 11.24s` ✅ — **aucune régression**.
- **Suite complète `backend/tests/` lancée depuis la racine** : 108 passed, 15 failed, 19 errors, 1 skipped — les 34 échecs/erreurs sont **tous préexistants** et non liés à ce chantier (tests d'intégration qui frappent un backend HTTP non lancé dans ce workspace de preview : `http://127.0.0.1:8001`, et tests d'install `kokoro`/scripts shell). Aucune régression introduite par le chantier.

### Étape 6 — ⏳ À faire (prod, par l'utilisateur)
### Étape 7 — ⏳ À faire (prod, par l'utilisateur)

### Etape 6 — Terminee (prod)
- Controle contrat reseau : au demarrage de forge-backend.service, le backend appelle GET http://localhost:20128/v1/models -> HTTP 200 (confirme dans les logs systemd, 2026-11-17 01:24:03).
- Variables prod : les 4 ROUTER9_* renseignees cote serveur, grep ^ROUTER9_ -> 4 lignes.
- Service : forge-backend.service active (running), aucune erreur de parsing.

### Etape 7 — Terminee (prod)
- UI : provider 9Router present dans le selecteur, liste des modeles peuplee depuis /v1/models.
- Filtrage modeles : comportement connu de 9Router (limite par defaut aux 8 derniers modeles du provider) -> resolu cote dashboard 9Router par edition de la base JSON/SQLite du combo. Aucune modification Forge (conforme au cadrage).

### Etape 8 — Terminee (prod)
- Streaming bout-en-bout : chat provider 9ROUTER + modele opus-4-8 (ajoute en base 9Router), prompt "dis bonjour en une phrase" -> reponse "Bonjour." recue.
- Chaine validee : /api/chat/send -> POST /v1/chat/completions (SSE OpenAI-compatible). En-tete UI "9Router: opus-4-8 (choisi)" coherent.

## Cloture
- Les 8 etapes sont vertes. Integration 9router operationnelle en prod (decouverte catalogue + inference streaming). Aucune regression. Chantier clos.
