# Évolutions futures possibles — Claude Unchained Forge

Pistes d'évolution **à faire**, **en réflexion** et **écartées** (avec raison).
Mémoire partagée pour ne pas réouvrir dix fois le même débat.

Statuts : `RETENU` (à faire) · `EN RÉFLEXION` · `ÉCARTÉ` (avec date + raison)

> **Note d'archivage** : extrait de l'ancien registre
> `evolutions-futures-possibles.md`. La part déjà livrée est consignée dans
> `PROJETS_AMELIORATIONS/Finis/forge-core/evolutions-livrees.md`.

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

### Duel de modèles (comparaison côte à côte) — écarté le 21/09/2026
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
