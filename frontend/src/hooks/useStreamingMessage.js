// Hook module — Etape 2 (rendu blocs facon Emergent), etendu Etape 6.
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
// Etape 6 : le message live expose `blocks` (liste ordonnee) au lieu d'un
// simple `content`, afin de passer par le meme composant de rendu que les
// messages persistes (MessageBlocks). `content` reste calcule en derive pour
// les chemins qui en ont besoin (copie TTS, recherche).
//
// Ce hook ne touche ni au flux SSE ni a la persistance.

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
      // `content` derive (invariant backend) : somme des blocs texte.
      content: state.blocks
        .filter((b) => b.type === "text")
        .map((b) => b.text || "")
        .join(""),
      // Etape 6 : le rendu vit dans `blocks`, ordonne comme le backend.
      blocks: state.blocks,
      provider: state.info?.provider || provider,
      model: state.info?.model || model,
      tool_steps: state.tools,
      steps: state.steps,
      streaming: true,
    }),
    [state.blocks, state.info, state.tools, state.steps, provider, model]
  );
}
