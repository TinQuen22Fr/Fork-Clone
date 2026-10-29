# Onglets projets dans le header (style Emergent)

> Spécification technique — rédigée le 2026-10-25
> Statut : **À FAIRE** — en attente du feu vert utilisateur.

---

## 1. Objectif

Permettre de naviguer entre plusieurs projets ouverts via des **onglets persistants** dans le header, sans perdre le contexte d'un projet en en ouvrant un autre.

**Comportement cible (calqué sur Emergent)** :

- Un bouton **Accueil** permanent affiche le hub (grille de projets).
- Cliquer sur un projet dans le hub l'ouvre dans un **onglet** dans le header.
- Revenir sur Accueil **ne ferme pas** les onglets ouverts.
- Ouvrir un second projet ajoute un onglet **à côté**, sans fermer le premier.
- Voyant **vert** = onglet actif, **gris** = onglet inactif.
- Clic sur un onglet inactif → bascule dessus.
- **Croix** sur un onglet → le ferme (le retire de la barre).
- F5 / Ctrl+Shift+R → restaure les onglets ouverts et la conversation active.

---

## 2. Cartographie des fichiers impactés

| Fichier | Rôle actuel | Impact |
|---|---|---|
| `frontend/src/pages/Chat.jsx` | Orchestrateur : état `activeId`, conversations, affiche `ProjectHub` ou conversation | **Lourd** — nouvel état `openTabs`, fonctions `closeTab`/`switchTab` |
| `frontend/src/components/chat/ChatHeader.jsx` | Barre supérieure : titre, bouton retour hub, badges | **Lourd** — accueille `<ProjectTabs>` |
| `frontend/src/components/chat/ProjectTabs.jsx` | *(à créer)* | **Nouveau** — composant visuel de la barre d'onglets |
| `frontend/src/components/ProjectHub.jsx` | Hub de sélection projets (grille cartes) | **Léger** — callback `onOpenProject` inchangé, alimente les onglets |
| `frontend/src/components/chat/Sidebar.jsx` | Tiroir latéral : liste conversations | **Inchangé** |

---

## 3. Flux actuel vs flux cible

### Flux actuel

```
activeId === null  →  <ProjectHub> affiché
       ↓ clic projet
activeId = conv.id →  <ProjectHub> disparaît, conversation affichée
       ↓ bouton ←
activeId = null    →  retour hub, contexte projet perdu
```

### Flux cible

```
activeId === null, openTabs = []       →  Hub affiché, barre d'onglets vide (sauf Accueil)
       ↓ clic projet A
activeId = convA.id, openTabs = [A]    →  Conversation A affichée, onglet A vert
       ↓ clic Accueil
activeId = null, openTabs = [A]        →  Hub affiché, onglet A gris (toujours là)
       ↓ clic projet B depuis hub
activeId = convB.id, openTabs = [A, B] →  Conversation B affichée, onglet B vert, A gris
       ↓ clic onglet A
activeId = convA.id, openTabs = [A, B] →  Conversation A affichée, onglet A vert, B gris
       ↓ croix sur B
activeId = convA.id, openTabs = [A]    →  Onglet B retiré, A toujours actif
       ↓ F5
activeId = convA.id, openTabs = [A]    →  Restauré tel quel depuis localStorage
```

---

## 4. Structure de données

### État `openTabs`

```js
// Tableau ordonné, persisté dans localStorage (clé "forge_open_tabs")
const [openTabs, setOpenTabs] = useState([
  // Exemple :
  { projectName: "Fork-Clone",       conversationId: "abc123" },
  { projectName: "storm-monitor-20km", conversationId: "def456" },
]);
```

### Clés localStorage

| Clé | Contenu | Déjà existante |
|---|---|---|
| `forge_active_conversation` | ID de la conversation active | ✅ (correctif précédent) |
| `forge_open_tabs` | JSON du tableau `openTabs` | ❌ À créer |

---

## 5. Étapes d'implémentation

### Étape 1 — État `openTabs` + persistance (`Chat.jsx`)

**Actions** :
- Déclarer `OPEN_TABS_KEY = "forge_open_tabs"`.
- `useState` avec init depuis `localStorage` (try/catch, fallback `[]`).
- `useEffect` miroir : persister `openTabs` dans `localStorage` à chaque changement.
- Modifier `openProject(project)` : ajouter `{ projectName, conversationId }` à `openTabs` si absent.
- Modifier `startTask(prompt, project)` : idem.
- Créer `closeTab(projectName)` : retire de `openTabs` ; si c'était l'actif → bascule sur le tab précédent ou `null`.
- Créer `switchTab(projectName)` : `setActiveId(conversationId)` du tab correspondant.

**Vérification** :
```bash
cd frontend && node node_modules/vite/bin/vite.js build --logLevel error; echo "EXIT=$?"
```

**Critère** : build OK, état vit en mémoire + localStorage, pas de rendu visible encore.

---

### Étape 2 — Composant `<ProjectTabs>` (`chat/ProjectTabs.jsx`)

**Actions** :
- Créer `frontend/src/components/chat/ProjectTabs.jsx`.
- Props : `tabs`, `activeProjectName`, `onSwitch(projectName)`, `onClose(projectName)`, `onHome()`.
- Rendu :
  - Bouton fixe **Accueil** (icône grille/home, appelle `onHome`), voyant vert si `activeProjectName === null`.
  - Map sur `tabs` : bouton avec voyant (cercle vert/gris), nom projet tronqué, croix `<X>`.
  - Scroll horizontal si débordement (`overflow-x-auto`, `scrollbar-hide`).
- Style : `bg-[var(--bg-sidebar)]`, `border-white/10`, `text-xs font-mono`, cohérent avec le thème existant.

**Critère** : composant compilé, importable.

---

### Étape 3 — Intégration dans `ChatHeader`

**Actions** :
- `ChatHeader.jsx` : ajouter props `tabs`, `activeProjectName`, `onSwitchTab`, `onCloseTab`, `onHome`.
- Insérer `<ProjectTabs>` entre le bouton hamburger et le bloc titre/badges.
- **Supprimer** le bouton « retour au hub » existant (← X) → remplacé par le bouton Accueil de `<ProjectTabs>`.
- `Chat.jsx` : passer les nouvelles props à `<ChatHeader>` :
  - `activeProjectName` = `activeConv?.project || null`
  - `tabs` = `openTabs`
  - `onSwitchTab` = `switchTab`
  - `onCloseTab` = `closeTab`
  - `onHome` = `() => setActiveId(null)`

**Dépendances** : étapes 1 + 2.

**Critère** : build OK, barre d'onglets visible dans le header.

---

### Étape 4 — Accueil ne ferme pas les onglets

**Actions** :
- Vérifier que `{!activeId && <ProjectHub />}` reste tel quel.
- Les onglets sont **toujours** rendus dans `ChatHeader` même quand `activeId === null`.
- Le bouton Accueil passe en « actif » (voyant vert) quand on est sur le hub.

**Dépendances** : étape 3.

**Critère** : ouvrir 2 projets → cliquer Accueil → les 2 onglets sont toujours là en gris.

---

### Étape 5 — Synchronisation au rechargement (F5)

**Actions** :
- `openTabs` déjà persisté (étape 1). `activeId` déjà persisté (correctif précédent).
- Après validation de `activeId` dans `fetchConversations` : vérifier que le tab correspondant est dans `openTabs` (sinon l'ajouter).
- Purger les `openTabs` dont la `conversationId` n'existe plus dans la liste chargée.

**Dépendances** : étapes 1 + 4.

**Critère** : F5 sur une conversation → même onglet actif, tous les autres onglets visibles.

---

### Étape 6 — Responsive mobile/tablette

**Actions** :
- `ProjectTabs.jsx` : scroll horizontal (`overflow-x-auto`, `scrollbar-hide`).
- Taille texte réduite sur mobile (`text-[10px] sm:text-xs`).
- Noms tronqués (`max-w-[120px] truncate`).
- Bouton Accueil toujours visible (position sticky à gauche, pas scrollé).

**Dépendances** : étape 2 (peut être fusionné directement).

**Critère** : pas de débordement à 375px, onglets accessibles par scroll horizontal.

---

## 6. Ordonnancement

```
Étape 1 (état openTabs)         ─┐
                                  ├── Étape 3 (intégration) ── Étape 4 (accueil) ── Étape 5 (F5)
Étape 2 (composant + étape 6)  ─┘
```

Étapes 1 et 2 sont indépendantes → codables en parallèle.
Étape 6 fusionnable avec étape 2.

**Estimation** : ~3 tours d'agent (1 : étapes 1+2, 2 : étapes 3+4, 3 : étapes 5+6 + tests build).

---

## 7. Risques et mitigations

| Risque | Impact | Mitigation |
|---|---|---|
| Projet ouvert dans 2 navigateurs → `openTabs` désynchronisés | Faible | Pas de synchro cross-tab (pas critique) |
| Conversation supprimée → onglet pointe sur un id mort | Moyen | Étape 5 : purge au chargement |
| `conversationId` change si conversation supprimée/recréée | Moyen | `switchTab` passe par `openProject` (retrouve ou recrée) |
| Trop d'onglets → barre déborde | Faible | Scroll horizontal (étape 6) |
| Bouton « retour hub » duplique « Accueil » | UX confuse | Étape 3 supprime le doublon |

---

## 8. Fichiers livrables (résumé)

| Action | Fichier |
|---|---|
| **Modifier** | `frontend/src/pages/Chat.jsx` |
| **Modifier** | `frontend/src/components/chat/ChatHeader.jsx` |
| **Créer** | `frontend/src/components/chat/ProjectTabs.jsx` |
| Inchangé | `frontend/src/components/ProjectHub.jsx` |
| Inchangé | `frontend/src/components/chat/Sidebar.jsx` |
