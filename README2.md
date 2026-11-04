# Claude Unchained Forge — README2 (vue d'ensemble complète)

> Ce document ne remplace pas `README.md` (qui reste la référence d'installation
> rapide). Il raconte **la genèse**, **ce que l'outil est vraiment**, et
> **tout ce qui a été ajouté** depuis le portage initial — pour qu'un nouvel
> arrivant (humain ou agent) comprenne le projet en une lecture.

---

## 1. Genèse

Point de départ : un utilisateur qui paie déjà un abonnement **Claude Pro**
(22 €/mois) et refuse catégoriquement de payer en plus une API Anthropic au
token. Problème : Claude Pro ne donne pas d'accès API classique.

**Décision technique fondatrice** : utiliser le jeton OAuth de Claude Code
(`claude setup-token`, jeton `sk-ant-oat...`) directement sur l'API Messages
d'Anthropic (`/v1/messages`), avec les en-têtes `anthropic-beta:
oauth-2025-04-20,claude-code-20250219` et l'identité système imposée par ce
mode (« You are Claude Code, Anthropic's official CLI for Claude »). La
consommation est décomptée du forfait Pro/Max de l'utilisateur — **zéro
facture au token**, usage strictement personnel (instance mono-utilisateur,
inscriptions fermées).

De ce choix fondateur découle toute la philosophie du projet :

- **100 % auto-hébergé**, sur la Dedibox personnelle de l'utilisateur.
- **100 % open-source** (GPLv3), code public sur
  [`TinQuen22Fr/Fork-Clone`](https://github.com/TinQuen22Fr/Fork-Clone), branche `claude-ai`.
- **Zéro dépendance propriétaire** à une plateforme tierce (pas de crédits,
  pas d'upsell, pas de couche cachée) — y compris Emergent, dont ce dépôt ne
  conserve plus aucune trace depuis le portage initial.
- **Le projet construit le projet** : une fois le chat fonctionnel, l'utilisateur
  a demandé à sa propre Forge de coder ses nouvelles fonctionnalités. C'est le
  point de départ du **dogfooding** (section 3).

---

## 2. Ce que c'est : une API de vibe coding personnelle, multi-provider

**Claude Unchained Forge n'est pas un simple clone de ChatGPT.** C'est une
**plateforme de « vibe coding » personnelle** : une interface de chat qui sert
à la fois d'assistant conversationnel **et** d'agent de développement complet,
capable de lire/écrire des fichiers, exécuter des commandes shell, gérer des
projets entiers (y compris leurs dépôts Git), les prévisualiser en direct, et
les pousser sur GitHub — le tout piloté en langage naturel.

Caractéristiques clés :

- **Multi-provider réel, pas de vendor lock-in.** Claude n'est qu'un provider
  parmi d'autres ; l'utilisateur choisit à chaque message (ou laisse le mode
  `auto` décider) quel moteur répond.
- **Agent, pas simple chatbot.** Les outils (`bash`, `read_file`, `web_search`,
  `fetch_url`, `screenshot_url`) donnent au modèle une vraie prise sur le
  système de fichiers et le web, avec les garde-fous de sécurité qui vont avec.
- **Gestion de projets de bout en bout.** Hub de projets, previews live avec
  domaine dédié par projet, import/export `.zip`, sauvegarde/push GitHub direct
  depuis le chat.
- **Vocation personnelle assumée.** Pas de RBAC, pas de multi-tenant, pas de
  facturation : une seule personne, une seule machine, un contrôle total.

---

## 3. Le DOGFOODING : la Forge qui se construit elle-même

C'est le principe d'usage central du projet, et il mérite d'être souligné : la
Forge **est utilisée pour développer la Forge**.

### 3.1 Le principe

1. L'utilisateur ouvre un **projet** dans le Hub qui correspond au clone du
   dépôt `Fork-Clone` lui-même (dogfooding = « manger sa propre nourriture pour
   croquette »).
2. Il discute avec sa propre Forge (n'importe quel provider : Claude, Gemini,
   OpenCode, un modèle gratuit...) et lui demande d'ajouter une fonctionnalité,
   de corriger un bug, de refactoriser un bout de `server.py`.
3. L'agent **lit et modifie le code réel de la Forge** via ses outils `bash` /
   `read_file`, à l'intérieur d'un **workspace isolé**
   (`workspace/Fork-Clone/`), jamais dans l'installation de production
   (`/var/www/forge`).
4. La modification est testée via la **preview intégrée** de ce projet (son
   propre backend FastAPI + frontend Vite, lancés sur des ports dédiés,
   isolés de la session de production — cookies, tokens, base Mongo
   `forge_preview` clonée, tout est cloisonné).
5. Une fois validée par l'utilisateur, il clique sur **« Enregistrer sur
   GitHub »** directement depuis le chat : commit + push vers `claude-ai`.
6. Il se connecte en SSH sur sa Dedibox et lance `upgrade.sh`, qui tire le
   nouveau code et redéploie la Forge de **production**.

Autrement dit : **chaque nouvelle fonctionnalité de cette application a été
écrite par l'application elle-même**, pilotée en langage naturel par son seul
utilisateur, puis revue, testée en preview et déployée à la main.

### 3.2 Les protections nécessaires pour que ça marche sans dérailler

Le dogfooding a révélé des pièges précis, tous corrigés dans le code :

| Risque identifié | Correction |
|---|---|
| Le modèle croit tourner sur une plateforme cloud, réclame `sudo`/root | `forge_system_prompt()` injecte un bloc d'identité réelle (auto-hébergé, aucun privilège, pas d'outil SaaS tiers) à **tous** les providers |
| Le modèle déborde sur d'autres projets du workspace ou sur la racine de prod | Cloisonnement strict par `ContextVar _current_project` : l'outil `bash` a son `cwd` forcé sur `workspace/<projet>/`, interdiction explicite d'inspecter les autres dossiers |
| Confusion entre « corriger en preview » et « déployer en prod » | Le prompt système rappelle explicitement qu'un projet dogfoodé n'est jamais la Forge de production : tout changement doit repasser par GitHub puis `upgrade.sh` |
| Double préfixe `/api/api` quand la Forge s'auto-préview | `frontend/src/lib/api.js` détecte si l'URL backend injectée se termine déjà par `/api` avant de concaténer |
| Collision de session entre l'instance hôte et le projet dogfoodé | Noms de cookie (`COOKIE_NAME`) et de clé localStorage (`VITE_AUTH_TOKEN_KEY`) uniques par projet, injectés automatiquement par `preview_runtime.py` (`forge_preview_<projet>_session` / `..._token`) |
| La preview backend dogfoodée (FastAPI de ~190 Ko) ne démarrait pas (code 0 silencieux) | Le détecteur de type de projet ne scannait que les 20 000 premiers caractères du fichier d'entrée pour trouver `FastAPI(...)` — relevé à 2 000 000 caractères |
| Previews écrivant par erreur dans la base Mongo de production | Base dédiée `forge_preview`, clonée fraîche depuis la prod à chaque `upgrade.sh` (`mongodump`/`mongorestore --nsFrom/--nsTo`), injectée de force dans l'environnement des previews |
| Le modèle oublie le contexte du projet entre deux tours | Fetcher de contexte (section 5.9) : snapshot structurel + faits mémorisés, réinjectés automatiquement dans le prompt système à chaque appel |

### 3.3 Pourquoi c'est important

Ce mode d'usage fait de l'historique Git du projet un véritable **journal de
développement assisté** : chaque fonctionnalité listée dans ce document a été
conçue, codée, testée et documentée par un agent IA tournant *dans* la Forge,
sur la demande de son unique utilisateur humain — avec le code source complet
disponible publiquement pour vérification.

---

## 4. Stack technique

| Couche | Techno |
|---|---|
| Backend | FastAPI (Python), MongoDB (Motor, async) |
| Auth | JWT (cookie `HttpOnly` + repli `Authorization: Bearer`), compte admin seedé au premier démarrage |
| Frontend | React 19 + Vite 6 + Tailwind (thème neo-brutaliste), `react-markdown` + `remark-gfm`, `framer-motion` |
| Previews | Gestionnaire de process maison (`backend/preview_runtime.py`), Nginx wildcard par sous-domaine |
| Déploiement | Nginx + systemd, Ubuntu 26.04 LTS, scripts `install.sh` / `upgrade.sh` idempotents |
| TTS | Kokoro (local, ONNX) avec repli automatique sur `edge-tts` |
| Captures d'écran | Playwright (Chromium headless) |

---

## 5. Fonctionnalités

### 5.1 Multi-providers, avec vrai routage et vrai tool-calling

Dix providers pris en charge, chacun détecté dynamiquement (actif seulement si
sa clé est configurée) :

| Provider | Nature d'accès | Remarque |
|---|---|---|
| **Claude** | OAuth abonnement Claude Pro/Max | Provider par défaut, outils natifs Anthropic |
| **Gemini** | Clé API (`google-genai`) | Function calling natif |
| **Groq, Cerebras, SambaNova, NVIDIA NIM, OpenRouter** | Clé API, passerelle compatible OpenAI | Gratuits/freemium selon le fournisseur |
| **OpenCode (Zen/Go)** | Clé API, **3 transports** détectés par modèle (`/chat/completions`, `/messages`, `/responses`) avec réessai automatique si le mauvais transport est utilisé | Dizaines de modèles tiers (DeepSeek, Qwen, GLM, Kimi, MiniMax, GPT via Responses...) |
| **Ollama local** | Aucune clé, modèle local | Toujours en dernier recours dans la chaîne auto |
| **Ollama Cloud** | Clé API | Modèles gratuits + secours intra-provider |

Points forts du routeur (`generate_ai_response` / `_build_chain`) :

- **Choix explicite = aucune bascule silencieuse.** Si l'utilisateur sélectionne
  un provider précis et qu'il échoue, l'erreur exacte remonte au frontend — fini
  les réponses de Claude données en douce à la place du provider demandé.
  Seul le mode **« Auto (meilleur dispo) »** garde une cascade résiliente de
  secours.
- **Tool-calling réellement exécuté sur tous les providers**, pas seulement
  Claude : adaptateurs de schéma dédiés par protocole (outils OpenAI,
  `tools`/`tool_calls` ; Anthropic `/messages` ; OpenAI Responses ; déclaration
  de fonction Gemini ; schéma Ollama), avec boucle d'itération sur chacun.
- **Parsing correct des modèles qui écrivent leurs appels d'outils en texte
  brut** (DSML/XML, typique de Qwen/DeepSeek) au lieu de les laisser s'afficher
  tels quels.
- **Sélecteur de modèle dynamique** : catalogue live par provider (mis en
  cache), filtrage `:free` pour OpenRouter, favoris épinglables (jusqu'à 6).
- **Résumé automatique de l'historique long** : au-delà d'un seuil de tokens
  estimé, les messages anciens sont condensés par un appel LLM dédié et
  persistés, les récents restant intacts.

### 5.2 Boîte à outils de l'agent

- `bash` — commandes shell arbitraires, `cwd` verrouillé sur le projet actif,
  verrou technique anti-lecture de secrets (`.env`, `.pem`, `id_rsa`...).
- `read_file` — même verrou anti-secrets.
- `web_search` — cascade Google CSE → DuckDuckGo → repli Wikipedia (keyless).
- `fetch_url` — récupération et nettoyage de page web, protégé anti-SSRF
  (rejet des IP privées/loopback/link-local, y compris après redirection).
- `screenshot_url` — capture Playwright, image servie et insérée en Markdown
  dans la réponse.
- Statut par entrée d'outil (`success`/`error`) suivi et affiché, pas de simple
  couple appel/résultat binaire.

### 5.3 Streaming temps réel & fiabilité du flux

- `POST /api/chat/stream` en Server-Sent Events (texte qui s'affiche au fil de
  la génération, y compris pendant une boucle d'outils).
- **Rejeu d'historique à la reconnexion** : un tampon borné et séquencé permet
  à un client qui se reconnecte en plein tour de ne perdre aucun événement.
- **Fin de flux explicite et idempotente** (`finished`), qui a éliminé les faux
  bandeaux « réseau indisponible » et les messages qui « sautaient » à l'écran.
- **États terminaux garantis** (`done`/`error`), y compris si le client a
  disparu, la tâche est annulée ou le serveur redémarre en plein tour — fin des
  tours fantômes « bloqués en cours ».
- **Mode Autonomie** : si la connexion de l'utilisateur coupe, la génération
  continue côté serveur (tâche détachée) ; au retour, un bandeau liste les
  tours en cours et terminés pendant l'absence (`GET /chat/status`).
- **Reality checks automatiques** : après une étape qui touche au code d'un
  projet, les scripts `checks/NN_*_test.py` du projet sont lancés
  automatiquement et leur verdict rattaché au tour — un tour ne se déclare
  « fini » que si le check passe.

### 5.4 Pièces jointes universelles & multi-fichiers

- Images (vision native selon provider), PDF (extraction texte via `pypdf`),
  et tout fichier texte/code (~60 extensions) injecté en bloc de code dans le
  prompt — fonctionne même avec les providers sans support document natif.
- Jusqu'à 10 pièces jointes par message, glisser-déposer global, dictée vocale
  (Web Speech API en premier choix, repli serveur Whisper).

### 5.5 Gestion de projets & Hub

- **Hub de projets** : écran d'accueil avec création de projet depuis un
  prompt, cartes avec compteur de sessions, badge Git, lien preview.
- **Import/export `.zip`** d'un projet (protection anti zip-slip, exclusion de
  `node_modules`/`venv`/`.git`...).
- **Suppression de projet** depuis le Hub (arrête la preview, supprime dossier
  + conversations).
- **Sauvegarde GitHub en un clic** depuis la barre de saisie : statut du
  dépôt, sélection dépôt/branche (ou création à la volée), **bouton « Générer »
  le message de commit** à partir du `git diff` réel (génération déterministe,
  sans appel LLM), push sans `--force`.
- **Fork de conversation** (duplication avec historique complet).

### 5.6 Previews live par projet

- Détection automatique du type de projet (Vite/Next/CRA, statique, Python/
  FastAPI) **y compris en monorepo** (`frontend/` + `backend/`, lancés sur deux
  ports, backend exposé via `/api/` par Nginx).
- Démarrage/arrêt/redémarrage/journal depuis l'UI, autostart après redémarrage
  du backend.
- Domaine dédié par projet (`https://<projet>.preview.<domaine>`, wildcard
  DNS + Nginx) avec régénération automatique de la map en tâche de fond.
- Base Mongo isolée (`forge_preview`) pour ne jamais risquer la prod.

### 5.7 Synthèse vocale & dictée

- Lecture audio des réponses via moteur neural (Kokoro local, repli `edge-tts`
  sans clé ni quota), sélecteur de voix (voix FR gratuites épinglées), arrêt
  immédiat garanti (un seul flux audio à la fois).
- Dictée micro pour la saisie.

### 5.8 PWA & mobile

- Application installable (manifeste, icônes, service worker volontairement
  sans cache pour éviter de figer une ancienne version du bundle).
- Interface responsive retravaillée (mobile portrait/paysage, tablette),
  composer repensé en deux rangées sur petit écran.

### 5.9 Fetcher de contexte / mémoire persistante

- Scan structurel d'un projet à l'ouverture (stack, arborescence, README, état
  Git, règles locales).
- Capture continue de **faits clés** (règles explicites, décisions
  d'architecture, chemins système, faits factuels), triés par priorité et
  dédupliqués en base, puis réinjectés automatiquement dans le prompt système
  à chaque tour — la Forge garde la mémoire d'un projet d'une session à l'autre
  et après redémarrage.
- Bandeau discret dans l'UI (`contexte N fait(s)`) avec détail dépliable coloré
  par priorité.

### 5.10 Cadrage système & cloisonnement (voir aussi §3.2)

- `.forge-rules` par projet : fichier de règles locales qui complète (sans
  jamais assouplir) le cadrage global injecté à tous les providers.
- Une conversation non rattachée à un projet reste strictement en lecture
  seule.

---

## 6. Sécurité

Un audit défensif complet est consigné dans [`AUDIT_SECURITE.md`](./AUDIT_SECURITE.md)
et exportable en PDF depuis l'interface (`GET /api/audit/export-pdf`). État au
dernier audit : **tous les risques critiques, élevés et moyens identifiés sont
résolus ou formellement acceptés par design** (choix explicites et documentés,
propres à une machine dédiée mono-utilisateur sans inscriptions publiques).

Protections actives notables :
- Anti-SSRF sur `fetch_url` (résolution DNS + rejet des IP privées/internes,
  y compris après redirection).
- Verrou technique anti-lecture de secrets sur `bash` et `read_file`.
- Rate limiting en mémoire sur `POST /auth/login` (5 échecs/min/IP, HTTP 429).
- Jetons GitHub chiffrés au repos (Fernet) et jamais exposés en ligne de
  commande (header HTTP temporaire) ni renvoyés au navigateur.
- Validation stricte des paramètres TTS, assainissement des chemins exposés,
  CORS explicite (liste + regex localhost dev uniquement), masquage
  systématique des secrets dans les sorties d'outils et les réponses IA
  persistées (`redact_secrets`).

---

## 7. Déploiement

Voir [`README.md`](./README.md) pour l'installation/mise à jour pas à pas, et
[`DEPLOIEMENT.md`](./DEPLOIEMENT.md) pour le détail complet (cadrage système,
previews, Nginx, Playwright, variables d'environnement, vérifications post-
déploiement, retour arrière).

Résumé du cycle de vie :

```
install.sh   → première installation / réinstallation complète
upgrade.sh   → mise à jour courante (pull, dépendances si besoin, build, restart)
```

`upgrade.sh` ne remplace **jamais** une unité systemd déjà en place sans
diff explicite, et préserve systématiquement le durcissement
`ProtectSystem=strict` / `ReadWritePaths` configuré manuellement.

---

## 8. Historique synthétique

Pour le détail exhaustif, daté, de chaque brique livrée (avec fichiers
modifiés et vérifications effectuées), voir [`memory/PRD.md`](./memory/PRD.md).
Grandes étapes dans l'ordre :

1. Portage initial + moteur Claude via OAuth abonnement.
2. Tool calling agentique (bash, read_file) + barre d'actions type claude.ai.
3. Routeur multi-providers avec cascade de bascule résiliente.
4. OpenCode : 3 transports + sélecteur de modèle dynamique.
5. Bouton Stop, pièces jointes universelles, affichage mobile corrigé.
6. PWA, streaming SSE natif, glisser-déposer, quota OpenCode Go.
7. Multi-fichiers, favoris de modèles, identité visuelle « unchained ».
8. 5 providers gratuits supplémentaires + résumé auto de l'historique long.
9. Outils web de l'agent (`web_search`, `fetch_url`, `screenshot_url`) + dictée.
10. Suppression granulaire de message + garde-fous secrets.
11. Voix neurale (Kokoro / edge-tts).
12. Instance Preview dédiée, puis Hub de projets + preview par projet.
13. Preview automatique par projet (domaine + port dédiés) + map Nginx auto-régénérée.
14. Runtime de preview multi-cibles (monorepos frontend+backend).
15. Cadrage système + cloisonnement workspace par projet (dogfooding sécurisé).
16. Menu « + » : pièce jointe / sauvegarde GitHub / fork de chat.
17. Intégration de patterns `forge-core` : rejeu SSE, fin de flux explicite,
    statut terminal idempotent, chaîne d'actions, cycle de vie d'outil,
    reality checks, session reprenable, journal de boucle.
18. Bouton « Générer le message de commit » depuis le diff réel.
19. Mode Autonomie (reprise après coupure) + Fetcher de contexte persistant.
20. Fixes critiques de dogfooding : détection FastAPI tronquée, double
    `/api/api`, isolation des sessions preview.
21. Refonte tool-calling multi-providers (fin du fallback silencieux vers Claude).
22. Identité dynamique provider/modèle dans le prompt système.
23. Synchronisation sécurité (audit, export PDF, protections ci-dessus) et
    évolutions client (thème clair/sombre, tableaux Markdown, anti-coupures
    réseau perçues).

---

## 9. Licence

GNU GPLv3 — voir [`LICENSE`](./LICENSE) et [`NOTICE`](./NOTICE) pour les
dépendances tierces. Code source public :
<https://github.com/TinQuen22Fr/Fork-Clone/tree/claude-ai>.
