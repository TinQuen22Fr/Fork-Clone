// Store externe minimal pour l'etat de streaming (Etape 2, etendu Etape 6).
//
// Objectif : pendant qu'une reponse est generee, les deltas arrivent a haute
// frequence (plusieurs dizaines de fois par seconde). Si cet etat vivait dans
// le composant de page Chat(), chaque token provoquerait le re-rendu de TOUTE
// la page (sidebar, header, liste complete des messages, composer...).
//
// En sortant cet etat dans un store externe, seul le composant qui s'y abonne
// (<LiveMessage />) se re-rend a chaque token. Le parent passe au travers.
//
// Etape 6 : l'etat ne stocke plus un simple `text` mais une liste de blocs
// ordonnes (`blocks[]`, meme forme que le backend) afin que le LIVE et la
// relecture passent par le MEME composant de rendu (MessageBlocks). C'est ce
// qui garantit la parite visuelle avant/apres la cloture.
//
// API basee sur useSyncExternalStore (React 18+), sans dependance externe.

const listeners = new Set();

let state = {
  blocks: [],
  tools: [],
  steps: [],
  info: null,
  // Booleen derive : y a-t-il quelque chose a afficher dans la bulle ?
  // Il ne change qu'a la transition vide -> non vide (et retour), ce qui
  // permet au parent de ne se re-rendre qu'une ou deux fois par generation.
  hasContent: false,
};

export function getStreamState() {
  return state;
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit() {
  for (const l of listeners) l();
}

// --- Mutations ---

export function resetStream() {
  if (
    state.blocks.length === 0 &&
    state.tools.length === 0 &&
    state.steps.length === 0 &&
    state.info === null
  ) {
    return; // deja a zero : on evite un re-rendu inutile.
  }
  state = { blocks: [], tools: [], steps: [], info: null, hasContent: false };
  emit();
}

function withContent(next) {
  const hasContent =
    next.blocks.some((b) => b.type === "text" && b.text) ||
    next.tools.length > 0;
  return { ...next, hasContent };
}

export function appendStreamText(text) {
  if (!text) return;
  const blocks = [...state.blocks];
  const last = blocks[blocks.length - 1];
  if (last && last.type === "text") {
    // Coalescence : un delta de texte suivant un bloc texte l'etend.
    blocks[blocks.length - 1] = { ...last, text: (last.text || "") + text };
  } else {
    blocks.push({ type: "text", seq: blocks.length, text });
  }
  const next = withContent({ ...state, blocks });
  const wasEmpty = !state.hasContent;
  state = next;
  // On ne notifie que si le texte avance, ou si le booleen a bascule.
  if (next.hasContent || !wasEmpty) emit();
}

export function setStreamInfo(info) {
  state = { ...state, info };
  emit();
}

export function addStreamTool(tool) {
  // L'outil est ajoute a la liste, et rattache a l'etape courante si elle existe.
  const tools = [...state.tools, tool];
  let steps = state.steps;
  if (steps.length > 0) {
    steps = [...steps];
    const last = { ...steps[steps.length - 1] };
    last.tools = [...(last.tools || []), tool];
    steps[steps.length - 1] = last;
  }
  // Etape 6 : l'outil devient une paire de blocs `tool_call` + `tool_output`,
  // inseree a sa position reelle (apres le texte deja recu), a la maniere du
  // backend. step_index correspond a l'etape courante (la derniere connue).
  const blocks = [...state.blocks];
  const stepIndex = steps.length > 0 ? steps[steps.length - 1].index : 0;
  const seq = blocks.length;
  blocks.push({
    type: "tool_call",
    seq,
    tool: tool.tool,
    input: tool.input || {},
    step_index: stepIndex,
  });
  blocks.push({
    type: "tool_output",
    seq: seq + 1,
    tool: tool.tool,
    output: tool.output || "",
    truncated: Boolean(tool.truncated),
    step_index: stepIndex,
  });
  if (tool.exit_code != null) {
    blocks[blocks.length - 1].exit_code = tool.exit_code;
  }
  state = withContent({ ...state, blocks, tools, steps });
  emit();
}

export function startStreamStep(index) {
  state = {
    ...state,
    steps: [
      ...state.steps,
      { index, intention: "", tools: [], status: "running" },
    ],
  };
  emit();
}

export function finishStreamStep(step) {
  const incoming = step || {};
  const idx = state.steps.findIndex((x) => x.index === incoming.index);
  let steps;
  if (idx === -1) {
    steps = [...state.steps, { ...incoming, status: "done" }];
  } else {
    steps = [...state.steps];
    steps[idx] = { ...steps[idx], ...incoming, status: "done" };
  }
  state = { ...state, steps };
  emit();
}

// Valeur derivee "a-t-il quelque chose a afficher", lisible par le parent
// sans provoquer de re-rendu a chaque token.
export function getStreamHasContent() {
  return state.hasContent;
}
