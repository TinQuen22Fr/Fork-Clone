# Amélioration — Rendu en blocs façon Emergent (fin de tour)

> Statut : **idée à instruire** (non planifiée, non exécutée). Consignée à la demande de l'utilisateur, à traiter plus tard, séparément du bug timeout.
> Créé le : 2026-11-07

## Contexte

Pendant la génération, le frontend affiche déjà le contenu **bloc par bloc** de manière incrémentale (via `streamStore`, flux SSE). Le ressenti « temps réel » est correct en cours de tour.

## Problème identifié

À la **fin du tour**, il y a une bascule :
- Pendant le tour : rendu incrémental depuis `streamStore` (granularité fine, bloc par bloc).
- À la clôture : rechargement depuis la BDD, le message devient un **markdown monolithique** rendu d'un coup.

Cette bascule fait perdre la granularité visuelle « blocs » obtenue pendant le streaming. Le rendu final n'a pas la même structure progressive que le style observé chez Emergent (enchaînement de blocs distincts : réflexion, action outil, résultat, texte…).

## Objectif

Conserver, après la fin du tour, une présentation **structurée en blocs** cohérente avec ce qui a été streamé, au lieu d'un bloc markdown unique rechargé depuis la BDD.

## Pistes (à valider, non tranchées)

1. Persister la **structure en blocs** du tour (et pas seulement le markdown final concaténé) afin que le rechargement BDD restitue les mêmes blocs que le streaming.
2. Au moment de la clôture, **fusionner** l'état `streamStore` dans l'historique sans écraser la granularité (éviter le « reload markdown monolithique »).
3. Composant d'affichage capable de rendre une **liste de blocs typés** (texte / appel outil / sortie outil / réflexion) de façon identique en live et en relecture.

## Dépendances / précautions

- **Ne pas** mélanger ce chantier avec le correctif du bug `timeout of 60000ms exceeded` (sujet distinct).
- Impact probable sur le schéma de persistance des messages (BDD) → à cadrer avant toute écriture.
- Vérifier la compatibilité avec la mémoïsation du rendu markdown prévue par ailleurs.

## Décision

En attente. « On verra ensuite » (utilisateur, 2026-11-07).
