import React from "react";

// Squelette de chargement affiche pendant que les messages d'une conversation
// arrivent depuis l'API. Il evite le "trou blanc" percu au clic sur un projet
// (le dock s'affiche immediatement, mais le fil reste vide le temps du fetch).
// Trois blocs de tailles decroissantes imitent un echange user/assistant.
export default function MessagesSkeleton() {
  return (
    <div className="msgs-skeleton" data-testid="messages-skeleton" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div className="msgs-skeleton-row" key={i} style={{ animationDelay: `${i * 120}ms` }}>
          <div className="msgs-skeleton-avatar" />
          <div className="msgs-skeleton-lines">
            <div className="msgs-skeleton-line" style={{ width: "70%" }} />
            <div className="msgs-skeleton-line" style={{ width: "92%" }} />
            <div className="msgs-skeleton-line" style={{ width: "55%" }} />
          </div>
        </div>
      ))}
    </div>
  );
}
