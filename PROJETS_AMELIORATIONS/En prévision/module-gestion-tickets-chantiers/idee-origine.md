# Module de gestion de tickets / chantiers (idée)

## Objectif
Remplacer la gestion manuelle par dossiers (`En prévision/` → `En cours/` →
`Finis/`) par un module intégré à l'UI de la forge, piloté depuis un bouton
dans le Header, pour suivre visuellement l'état des chantiers.

## Principe
Reproduire le cycle de vie des chantiers sous forme de tickets :
- **En prévision** : idées / chantiers pas encore démarrés.
- **En cours** : chantiers actifs.
- **Finis** : chantiers terminés (rebasculables en « En cours » si repris).

Dans un chantier fini, le dossier « Améliorations possibles après fin de
chantier » devient la liste des sous-tâches rouvrables au moment d'une reprise.

## Pistes de mise en œuvre
- Source de vérité : l'arborescence `PROJETS_AMELIORATIONS/` (le module la lit
  et l'écrit), pour ne pas dupliquer l'information.
- Bouton dédié dans le Header ouvrant une vue kanban ou liste à 3 colonnes.
- Actions : créer un ticket, le déplacer d'un état à l'autre, l'ouvrir pour
  voir le plan et les étapes (fichiers `.md` du chantier).

## Statut
`EN RÉFLEXION` — idée consignée, non planifiée.
