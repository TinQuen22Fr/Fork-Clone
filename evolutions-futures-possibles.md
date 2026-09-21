# Évolutions futures possibles — Claude Unchained Forge

Ce document liste les pistes d'évolution de la forge, avec leur statut.
Il sert de mémoire partagée entre toi et l'agent : ce qui est retenu, ce qui est
écarté et **pourquoi**, pour ne pas réouvrir dix fois le même débat.

Statuts : `RETENU` (à faire) · `EN RÉFLEXION` · `ÉCARTÉ` (avec date + raison)

**Rappel du cap de la forge** : outil personnel, 100 % autonome, sans dépendance
ni facturation à l'usage. Elle sert à *travailler* avec les modèles (code, analyse
de fichiers, agent avec outils), pas à les comparer ni à les mettre en concurrence.

---

## Déjà livré (pour référence)

| Fonctionnalité | Détail |
|---|---|
| Multi-providers | Claude (OAuth Pro), Gemini, Ollama Cloud, OpenCode Zen/Go, Ollama local |
| Routeur + cascade | Bascule automatique sur erreur (crédits, région, quota, réseau…), Ollama local en dernier recours |
| Mode Auto | Route vers le meilleur provider disponible selon les clés actives |
| 3 transports OpenCode | `/chat/completions`, `/messages`, `/responses` avec détection auto du bon endpoint |
| Sélecteur de modèle | Catalogue live des 37 modèles OpenCode + 20 Ollama Cloud |
| Agent avec outils | `bash` et `read_file` en boucle, y compris **en streaming** |
| Streaming SSE | Texte mot à mot, arrêt immédiat, texte partiel conservé (badge « ARRÊTÉ ») |
| Pièces jointes | Images, PDF (extraction texte), ~60 formats texte/code, **multi-fichiers** |
| Glisser-déposer | N'importe où sur la fenêtre, plusieurs fichiers d'un coup |
| Favoris de modèles | Jusqu'à 6 modèles épinglés, accessibles en un clic |
| Suivi du quota Go | Jauges 5 h / semaine / mois dans l'en-tête |
| PWA | Installable sur l'écran d'accueil, plein écran, icône « unchained » |
| Responsive | Téléphone, tablette et ordinateur |
| **Providers cloud gratuits** | Groq, Cerebras, SambaNova, NVIDIA NIM, OpenRouter via un adaptateur unifié compatible OpenAI (streaming inclus) |
| **Découverte dynamique des modèles** | `GET /v1/models` interrogé par provider, cache 1 h, filtre `:free` pour OpenRouter, zéro modèle codé en dur |
| **Résumé automatique de l'historique** | Condensation incrémentale des vieux messages au-delà d'un seuil de tokens, résumé persisté par conversation |

### Détail — cascade multi-providers gratuits (livré le 21/06/2026)
- Priorité 1 : Claude Pro (OAuth) puis OpenCode Go.
- Priorité 2 : providers gratuits actifs, dans l'ordre de `PROVIDER_PRIORITY`,
  avec les modèles découverts dynamiquement.
- Priorité 3 : Gemini et Ollama Cloud, puis **repli ultime sur Ollama local**
  (toujours placé en dernier, quelle que soit la configuration).
- Bascule transparente sur 429 (quota), 503 (indisponible), timeout, erreur réseau,
  crédits épuisés ou restriction régionale. Chaque bascule est tracée dans les logs
  et dans le champ `routing` du message, avec un badge visible dans l'interface.
- Une clé absente ou vide = provider simplement ignoré, jamais d'erreur bloquante.

### Détail — résumé automatique de l'historique (livré le 21/06/2026)
- Déclenché quand l'historique brut dépasse `HISTORY_SUMMARY_THRESHOLD_TOKENS`
  (estimation ≈ 4 caractères par token).
- Les `HISTORY_SUMMARY_KEEP_RECENT` derniers messages partent toujours mot pour mot ;
  les plus anciens sont condensés en puces factuelles.
- La consigne de condensation préserve explicitement : instructions et préférences
  de l'utilisateur, décisions prises, faits techniques (chemins, versions, valeurs)
  et problèmes encore ouverts.
- Le résumé est **incrémental et persisté** dans la conversation (`summary`,
  `summarized_ids`) : on ne recondense que ce qui a été ajouté depuis.
- Un échec de condensation retombe silencieusement sur la troncature simple :
  une conversation ne peut jamais être bloquée par le résumé.

---

## RETENU — prochaines étapes

### 1. Alerte quota
Notification dans l'app quand une fenêtre du forfait Go dépasse 90 %, pour éviter
de tomber à sec en pleine session. La donnée est déjà récupérée (`/api/opencode/usage`),
il ne reste que le déclencheur et l'affichage.

### 2. Recherche dans l'historique
Champ de recherche sur les conversations et le contenu des messages. Devient vite
indispensable dès qu'il y a plusieurs dizaines de sessions.

### 3. Export d'une conversation
Téléchargement en Markdown (et/ou copie du fil complet). Utile pour archiver une
session de travail ou la réinjecter ailleurs.

### 4. Dossier de travail pour l'agent
Fixer un répertoire de travail par conversation, pour que `bash` et `read_file`
opèrent sur un projet précis du serveur sans repréciser les chemins.

---

## EN RÉFLEXION

### Multi-images avec modèles vision
Le backend accepte déjà N images et les transmet toutes aux modèles vision.
Reste à vérifier au cas par cas quels modèles OpenCode/Ollama Cloud les acceptent
réellement (beaucoup sont texte seul) et à afficher un avertissement clair sinon.

### Prompts système par conversation
Pouvoir définir une consigne système différente selon la session (ex. « expert
sysadmin Debian » vs « relecteur de code Python »), au lieu d'un seul prompt global.

### Encore plus de providers gratuits
Cinq passerelles sont intégrées (Groq, Cerebras, SambaNova, NVIDIA NIM, OpenRouter).
Pour en ajouter une autre compatible OpenAI, il suffit désormais d'une ligne dans
`Settings.free_providers` : l'adaptateur, le streaming, la découverte des modèles et
la cascade sont déjà factorisés. Référence à suivre :
<https://github.com/open-free-llm-api/awesome-freellm-apis>.

---

## ÉCARTÉ

### Duel de modèles (comparaison côte à côte) — écarté le 21/06/2026
**Raison (retour utilisateur)** : ne correspond pas à l'usage de la forge. C'est un
outil de travail, pas un banc d'essai. Comparer deux réponses côte à côte double la
consommation de quota pour un gain nul dans le flux de travail réel, et encombre une
interface pensée pour une conversation unique et lisible, y compris sur téléphone.
La cascade automatique répond déjà au vrai besoin : obtenir *une* réponse, quel que
soit le modèle disponible.

### Hébergement géré / crédits à l'usage — écarté définitivement
**Raison** : contraire au principe fondateur. La forge tourne sur le serveur
personnel (Dedibox), s'appuie sur l'abonnement Claude Pro et le forfait OpenCode Go
déjà payés, et ne doit jamais introduire de facturation à l'appel.

### Inscription publique — écarté définitivement
**Raison** : instance strictement personnelle. Le lien d'inscription est masqué et
`ALLOW_PUBLIC_REGISTER` reste à `false`.
