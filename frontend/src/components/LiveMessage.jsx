import React from "react";
import ChatMessage from "@/components/ChatMessage";
import { useStreamingMessage } from "@/hooks/useStreamingMessage";

// Message en cours de generation, rendu comme un <ChatMessage/> a part entiere.
//
// Role (Etape 2, suppression de la micro-bascule au done, cause C1) :
//   - unique abonne au store de streaming (via useStreamingMessage) : la page
//     Chat() ne se re-rend pas a chaque token, seul ce composant le fait ;
//   - rend le MEME composant <ChatMessage/> que les messages persistes, avec
//     exactement la meme forme de props. Place au meme emplacement de liste et
//     porteur de la cle stable STREAMING_SLOT_KEY, le slot reste homogene avant
//     et apres la cloture : React reconcilie au lieu de remonter.
//
// Ce composant ne touche ni au flux SSE ni a la persistance.
export default function LiveMessage({ provider, model, project, onRollbackStep }) {
  const message = useStreamingMessage({ provider, model });

  return (
    <ChatMessage
      message={message}
      project={project}
      onRollbackStep={onRollbackStep}
    />
  );
}
