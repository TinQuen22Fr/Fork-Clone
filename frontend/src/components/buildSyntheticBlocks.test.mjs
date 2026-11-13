// Valide la logique de buildSyntheticBlocks (ChatMessage.jsx, etape 6.5).
// Node ne peut importer un .jsx : ce test rejoue la fonction a l'identique.
const STEP_LOG_TRUNC = 1200;
function blockExitCode(output) {
  if (!output) return null;
  const m = String(output).match(/(?:^|\n)\s*exit[ _]?code\s*[:=]?\s*(\d{1,3})\b/i);
  return m ? Number(m[1]) : null;
}
function buildSyntheticBlocks(message, toolSteps) {
  const blocks = []; let seq = 0;
  const content = message?.content || "";
  if (content) blocks.push({ type: "text", seq: seq++, text: content });
  const steps = Array.isArray(toolSteps) ? toolSteps : [];
  steps.forEach((s, i) => {
    const rawOut = s?.output || "";
    const truncated = rawOut.length > STEP_LOG_TRUNC;
    const out = truncated ? rawOut.slice(0, STEP_LOG_TRUNC) + `\n... [sortie tronquee : ${rawOut.length} caracteres au total]` : rawOut;
    blocks.push({ type: "tool_call", seq: seq++, tool: s?.tool, input: s?.input || {}, step_index: i });
    const outBlock = { type: "tool_output", seq: seq++, tool: s?.tool, output: out, truncated, step_index: i };
    const ec = blockExitCode(rawOut);
    if (ec != null) outBlock.exit_code = ec;
    blocks.push(outBlock);
  });
  return blocks;
}

let ok = 0, ko = 0;
const eq = (a, b, m) => { if (JSON.stringify(a) === JSON.stringify(b)) ok++; else { ko++; console.log("KO:", m, "attendu", JSON.stringify(b), "recu", JSON.stringify(a)); } };

// 1. texte + 1 outil
let r = buildSyntheticBlocks({ content: "Salut" }, [{ tool: "bash", input: { command: "ls" }, output: "a\nexit code: 0" }]);
eq(r.map(b => b.type), ["text", "tool_call", "tool_output"], "ordre");
eq(r.map(b => b.seq), [0, 1, 2], "seq monotone");
eq(r[1].step_index, 0, "step_index call");
eq(r[2].step_index, 0, "step_index output");
eq(r[2].exit_code, 0, "exit_code extrait");
eq(r[0].text, "Salut", "texte en seq0");

// 2. invariant Sigma text == content
const content = "Blabla";
r = buildSyntheticBlocks({ content }, [{ tool: "x", output: "y" }]);
eq(r.filter(b => b.type === "text").map(b => b.text).join(""), content, "invariant content");

// 3. 2 outils -> seq 1..4, step_index 0 et 1
r = buildSyntheticBlocks({ content: "t" }, [{ tool: "a", output: "o1" }, { tool: "b", output: "o2" }]);
eq(r.map(b => b.seq), [0, 1, 2, 3, 4], "seq 2 outils");
eq([r[1].step_index, r[3].step_index], [0, 1], "step_index 2 outils");

// 4. pas d'exit_code quand absent
r = buildSyntheticBlocks({ content: "t" }, [{ tool: "a", output: "sans code" }]);
eq("exit_code" in r[2], false, "pas d'exit_code parasite");

// 5. troncature
const big = "x".repeat(1500);
r = buildSyntheticBlocks({ content: "t" }, [{ tool: "a", output: big }]);
eq(r[2].truncated, true, "truncated true");
eq(r[2].output.length < big.length + 60, true, "sortie tronquee");

// 6. message sans content ni outils -> vide
eq(buildSyntheticBlocks({ content: "" }, []), [], "message vide -> []");

// 7. outils sans texte -> commence a seq0 sur tool_call
r = buildSyntheticBlocks({ content: "" }, [{ tool: "a", output: "o" }]);
eq(r.map(b => b.seq), [0, 1], "seq sans texte");
eq(r[0].type, "tool_call", "premier bloc tool_call si pas de texte");

// === Etape 7 (fallback retrocompatible) : scenarios dedies ===

// E7.1 ancien message avec content vide + outils (listing renvoie output=null,
// scenario "detail pas encore charge") -> tool_output.output == ""
r = buildSyntheticBlocks({ content: "" }, [{ tool: "bash", input: { command: "ls" }, output: null }]);
eq(r.map(b => b.type), ["tool_call", "tool_output"], "E7.1 ordre sans texte");
eq(r[1].output, "", "E7.1 output vide quand output=null");
eq("exit_code" in r[1], false, "E7.1 pas d'exit_code si output=null");
eq(r[1].truncated, false, "E7.1 pas tronque si vide");

// E7.2 ancien message avec content + outils dont certains sans output (mix
// listing/detail partiel) -> rendu stable, aucune exception
r = buildSyntheticBlocks(
  { content: "Resultat :" },
  [
    { tool: "bash", input: { command: "echo a" }, output: "a\nexit code: 0" },
    { tool: "bash", input: { command: "echo b" }, output: undefined },
  ]
);
eq(r.map(b => b.type), ["text", "tool_call", "tool_output", "tool_call", "tool_output"], "E7.2 5 blocs");
eq(r[2].exit_code, 0, "E7.2 exit_code 1er outil");
eq("exit_code" in r[4], false, "E7.2 pas d'exit_code 2eme outil (output undef)");
eq(r[4].output, "", "E7.2 output coerce string vide");

// E7.3 ancien message totalement vide (ni content, ni outils) -> tableau vide
eq(buildSyntheticBlocks({ content: null }, null), [], "E7.3 message null-null -> []");
eq(buildSyntheticBlocks({}, undefined), [], "E7.3 message {} -> []");
eq(buildSyntheticBlocks(null, []), [], "E7.3 message null -> []");

// E7.4 ancien message avec toolSteps non-array (defensif) -> ignore outils
r = buildSyntheticBlocks({ content: "txt" }, "pas-un-array");
eq(r.map(b => b.type), ["text"], "E7.4 toolSteps non-array ignore");
eq(r[0].text, "txt", "E7.4 texte conserve");

// E7.5 invariant seq strictement monotone meme sur mix complexe (regle etape 3)
r = buildSyntheticBlocks(
  { content: "A" },
  [
    { tool: "x", output: "o1" },
    { tool: "y", output: "o2\nexit code: 42" },
    { tool: "z", output: "o3" },
  ]
);
const seqs = r.map(b => b.seq);
eq(seqs, [0, 1, 2, 3, 4, 5, 6], "E7.5 seq monotone sur 7 blocs");
eq(r[4].exit_code, 42, "E7.5 exit_code preserve au milieu");
eq(r.filter(b => b.type === "tool_call").map(b => b.step_index), [0, 1, 2], "E7.5 step_index call");
eq(r.filter(b => b.type === "tool_output").map(b => b.step_index), [0, 1, 2], "E7.5 step_index output");

console.log(`\n${ok} assertions OK, ${ko} KO`);
process.exit(ko ? 1 : 0);
