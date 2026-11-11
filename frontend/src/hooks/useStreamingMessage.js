// Hook module — Etape 2 (rendu blocs facon Emergent).
//
// But : supprimer la micro-bascule visuelle au `done` (cause C1 documentee
// dans PROJETS_AMELIORATIONS/etape-1-micro-bascule.md).
//
// Rappel du probleme : pendant la generation, <LiveMessage/> rend un
// message volatil d'id "streaming" ; a la cloture, ce composant disparait et
// un nouveau <ChatMessage/> d'id BDD apparait dans la liste. React voit deux
// arbres distincts (cles differentes) -> demontage + remontage -> flash/reflow.
//
// Solution : exposer une CLE STABLE partagee ("streaming-slot") et le message
// live construit depuis le store. Le rendu de la page place la bulle live dans
// la MEME liste, a la MEME position que le futur message BDD, avec cette cle
// stable. Au `done`, la cle de l'emplacement ne change pas -> React reconcilie
// au lieu de remonter -> aucune bascule visible.
//
// Ce hook ne touche ni au flux SSE ni a la persistance : il se contente de
// derouter la construction du message live hors de Chat.jsx (pas de fichier a
// rallonge) et de fournir l'identite de cle a reutiliser.

import { useMemo, useSyncExternalStore } from "react";
import { getStreamState, subscribe } from "@/lib/streamStore";

// Cle stable de l'emplacement du message en cours de generation.
// Reutilisee a l'identique pour le message BDD a la cloture (voir Chat.jsx),
// afin que React reconcilie le meme noeud au lieu de le remonter.
export const STREAMING_SLOT_KEY = "streaming-slot";

// Construit le message live affichable depuis le store de streaming.
// `provider` et `model` servent de repli tant que l'info serveur n'est pas
// encore arrivee dans le flux.
export function useStreamingMessage({ provider, model }) {
  const state = useSyncExternalStore(subscribe, getStreamState, getStreamState);

  return useMemo(
    () => ({
      id: "streaming",
      role: "assistant",
      content: state.text,
      provider: state.info?.provider || provider,
      model: state.info?.model || model,
      tool_steps: state.tools,
      steps: state.steps,
      streaming: true,
    }),
    [state.text, state.info, state.tools, state.steps, provider, model]
  );
}
