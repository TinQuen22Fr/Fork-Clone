import React, { useMemo, useSyncExternalStore } from "react";
import ChatMessage from "@/components/ChatMessage";
import {
  getStreamState,
  subscribe,
} from "@/lib/streamStore";

// Bulle du message en cours de generation.
//
// Elle est le SEUL composant a s'abonner au store de streaming. Consequence :
// pendant qu'une reponse arrive (des dizaines de deltas par seconde), seul ce
// composant se re-rend. La page Chat(), sa sidebar, son header et la liste
// complete des messages ne sont plus recalcules a chaque token.
export default function StreamingBubble({
  provider,
  model,
  project,
  onRollbackStep,
}) {
  const state = useSyncExternalStore(subscribe, getStreamState, getStreamState);

  const message = useMemo(
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

  return (
    <ChatMessage
      message={message}
      project={project}
      onRollbackStep={onRollbackStep}
    />
  );
}
