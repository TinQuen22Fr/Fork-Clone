# Reprise de travail — The Forge

> Fichier de consignation créé pour éviter toute perte d'information lors des
> déconnexions réseau rencontrées sur téléphone portable.

## WORKFLOW OFFICIEL (rappel utilisateur — NE PAS DÉROGER)

1. La Forge en production est dans `/var/www/forge` : **je n'y ai PAS accès**.
2. Je travaille UNIQUEMENT dans le workspace/sandbox :
   `/var/www/forge/workspace/Fork-Clone/`.
3. Mes modifications de code sont testées via la **fonction preview** dans ce projet.
4. Avant chaque test, c'est **l'UTILISATEUR** qui redémarre Backend/Frontend
   manuellement avec l'outil journal — **PAS MOI**.
5. Je ne redémarre JAMAIS un process backend/frontend moi-même.
   Je ne tue JAMAIS un PID à l'aveugle.
6. Une fois testé et validé par l'utilisateur, il revient en prod et clique sur
   **« Enregistrer sur GitHub »**.
7. Puis l'utilisateur se connecte en SSH sur sa Dedibox, dans `/var/www/forge`,
   et lance `upgrade.sh` pour mettre à jour la Forge en production.

## Date de consignation
2026-10-02 17:26:38 CEST (mise à jour du workflow après rappel utilisateur)

## Environnement
- Répertoire de travail : `/var/www/forge/workspace/Fork-Clone/`
- Branche active : `claude-ai`
- Backend workspace actif : port 8192 (géré par la Forge, NE PAS y toucher)
- Frontend Vite workspace : port 8092 (géré par la Forge, NE PAS y toucher)

## Ce qui est TERMINÉ et commité
1. Correctif anti-réponse-vide (commit `b386c91`) — validé par 60 appels de tests.
2. Sécurisation de `upgrade.sh` (commit `4c4e73d`) — `--untracked-files=no` ajouté,
   blocage conservé si des fichiers suivis sont modifiés.
3. Export PDF d'audit (commit `2939973`) — bouton d'action UI + règle anti-régressions.

## Ce qui est ÉCRIT mais PAS ENCORE COMMITÉ (en attente de test utilisateur)
1. `backend/server.py` : endpoint `GET /api/chat/status` (fonction `chat_status`)
   — résumé lisible des derniers tours exécutés (outils, résultats, erreurs).
2. `frontend/src/pages/Chat.jsx` : fonction `fetchStatusTurns()` + bandeau UI
   « pendant ton absence » résumant les travaux exécutés côté serveur.
3. `REPRISE_MODE_AUTONOME.md` : ce fichier (non suivi).

## Décisions à prendre avec l'utilisateur
- Trancher le point resté ouvert sur `upgrade.sh` : faut-il éradiquer le bloc
  `git stash` automatique distant pour le remplacer par un arrêt net (`die`) ?
- Mode Max (à faire) : `max_mode` backend (100 itérations d'outils) + toggle UI.
- Fetcher de contexte / mémoire persistante (à faire).

## Prochaine étape
L'utilisateur redémarre Backend/Frontend via l'outil journal, teste le bandeau
de reprise, puis valide ou demande des ajustements. Commit après validation.
