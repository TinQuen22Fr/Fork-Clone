// Test autonome (Node pur, sans framework) de la construction blocks[] live.
// Objectif 6.3 : verifier que streamStore reconstruit cote client la MEME
// sequence ordonnee que le backend (_run_generation, etape 4) :
//   - deltas de texte coalesces en un seul bloc `text`,
//   - chaque outil -> paire ordonnee tool_call + tool_output,
//   - `seq` monotone 0..N, step_index correct,
//   - invariant : content reconstruit == somme des blocs text.
//
// Lancement : node src/lib/streamStore.test.mjs

import {
  getStreamState,
  resetStream,
  appendStreamText,
  addStreamTool,
  startStreamStep,
} from "./streamStore.js";

let failures = 0;
function assert(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error("  ✗ " + msg);
  } else {
    console.log("  ✓ " + msg);
  }
}

function eq(a, b, msg) {
  assert(JSON.stringify(a) === JSON.stringify(b), `${msg} (attendu ${JSON.stringify(b)}, recu ${JSON.stringify(a)})`);
}

// --- Cas 1 : etat initial ---
console.log("Cas 1 : etat initial");
resetStream();
eq(getStreamState().blocks, [], "blocks vide a l'init");
assert(getStreamState().hasContent === false, "hasContent false a l'init");

// --- Cas 2 : coalescence des deltas texte ---
console.log("Cas 2 : coalescence des deltas texte");
resetStream();
appendStreamText("Bon");
appendStreamText("jour");
appendStreamText(" monde");
const b2 = getStreamState().blocks;
eq(b2.length, 1, "un seul bloc text apres 3 deltas");
eq(b2[0].type, "text", "type text");
eq(b2[0].text, "Bonjour monde", "texte coalesce");
eq(b2[0].seq, 0, "seq 0");
assert(getStreamState().hasContent === true, "hasContent true apres texte");

// --- Cas 3 : flux intercale delta -> tool -> delta (miroir backend) ---
console.log("Cas 3 : flux intercale delta -> tool -> delta");
resetStream();
startStreamStep(0);
appendStreamText("Je lance la commande.");
addStreamTool({
  tool: "bash",
  input: { command: "ls" },
  output: "fichier.txt\n",
  truncated: false,
  exit_code: 0,
});
appendStreamText("Voila le resultat.");
const b3 = getStreamState().blocks;
eq(b3.map((b) => b.type), ["text", "tool_call", "tool_output", "text"], "ordre des types");
eq(b3.map((b) => b.seq), [0, 1, 2, 3], "seq monotone 0..3");
eq(b3[1].step_index, 0, "tool_call rattache a l'etape 0");
eq(b3[2].step_index, 0, "tool_output rattache a l'etape 0");
eq(b3[1].tool, "bash", "tool_call.tool");
eq(b3[1].input, { command: "ls" }, "tool_call.input");
eq(b3[2].output, "fichier.txt\n", "tool_output.output");
eq(b3[2].exit_code, 0, "tool_output.exit_code present");
eq(b3[2].truncated, false, "tool_output.truncated");

// Invariant : content reconstruit == somme des blocs text
const content = b3.filter((b) => b.type === "text").map((b) => b.text).join("");
eq(content, "Je lance la commande.Voila le resultat.", "invariant content == somme des text");

// --- Cas 4 : outil sans exit_code (pas de clef parasite) ---
console.log("Cas 4 : outil sans exit_code");
resetStream();
addStreamTool({ tool: "web_search", input: { q: "x" }, output: "res" });
const b4 = getStreamState().blocks;
assert(!("exit_code" in b4[1]), "pas de clef exit_code quand absent");

// --- Cas 5 : texte seul sans etape (step_index 0 par defaut) ---
console.log("Cas 5 : outil sans etape prealable");
resetStream();
addStreamTool({ tool: "bash", input: {}, output: "" });
const b5 = getStreamState().blocks;
eq(b5[0].step_index, 0, "step_index 0 par defaut sans etape");

console.log("");
if (failures === 0) {
  console.log("OK : tous les cas passent.");
  process.exit(0);
} else {
  console.error(`ECHEC : ${failures} assertion(s) en echec.`);
  process.exit(1);
}
