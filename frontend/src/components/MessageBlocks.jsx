/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 *
 * Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
 * modifier selon les termes de la GNU General Public License telle que publiée
 * par la Free Software Foundation, soit la version 3, soit (à votre choix)
 * toute version ultérieure. Il est distribué SANS AUCUNE GARANTIE.
 * Voir le fichier LICENSE ou <https://www.gnu.org/licenses/>.
 */

import React, { useState } from "react";
import { Copy, Check, Terminal, ChevronRight } from "lucide-react";
import MarkdownBody from "@/components/MarkdownBody";

// Etape 6 (chantier rendu-blocs) : rendu « liste de blocs types » unifie.
//
// Parcourt `blocks[]` dans l'ordre `seq` et intercale texte et actions
// (appel d'outil + sortie), reconstituant l'enchainement reel « texte ->
// outil -> texte » a la maniere d'Emergent. Utilise a l'identique en live
// (via useStreamingMessage) et en relecture (message persiste) : c'est ce
// qui garantit la parite visuelle avant/apres la cloture.
//
// Style Emergent : chaque entree d'outil (appel + sortie) est REPLIEE par
// defaut derriere un chevron `>` ; seuls les blocs texte s'affichent
// directement (pas de chevron). On apparie un `tool_call` avec le
// `tool_output` qui le suit (meme step_index) pour un rendu compact.

function toolArg(input) {
  if (!input || typeof input !== "object") return "";
  return input.command || input.path || JSON.stringify(input);
}

// Entree d'outil « a la Emergent » : ligne d'appel + chevron qui deplie la
// sortie. Repliee par defaut. `output` peut etre absent tant que le detail
// complet n'est pas charge (on affiche alors un placeholder).
function ToolEntry({ call, output, messageId }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const seq = call?.seq ?? output?.seq ?? 0;
  const tool = call?.tool ?? output?.tool;
  const input = call?.input;
  const out = output?.output;
  const exitCode = output?.exit_code;
  const truncated = output?.truncated;

  const copy = async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(out || "");
    } catch {
      // ignore
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className="border-l-2 border-[#05d9e8]/50 bg-[#05d9e8]/5"
      data-testid={`tool-entry-${messageId}-${seq}`}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-start gap-2 px-2 py-1.5 text-left text-[#05d9e8] font-mono text-xs hover:bg-[#05d9e8]/10 transition-colors"
        aria-expanded={open}
        data-testid={`tool-toggle-${messageId}-${seq}`}
      >
        <ChevronRight
          className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <Terminal className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
        <span className="flex-1 break-all">
          {tool}({toolArg(input)})
        </span>
        {exitCode != null && (
          <span
            className={`flex-shrink-0 font-mono text-[10px] px-1 py-0.5 border ${
              exitCode === 0
                ? "text-green-400 border-green-400/40"
                : "text-[#ff2a6d] border-[#ff2a6d]/40"
            }`}
          >
            exit {exitCode}
          </span>
        )}
        <span className="flex-shrink-0 text-[10px] uppercase tracking-wider text-gray-500">
          étape {(call?.step_index ?? output?.step_index ?? 0) + 1}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-2 pl-7">
          <div className="flex items-center gap-2 mb-1">
            <button
              type="button"
              onClick={copy}
              className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-gray-500 hover:text-[#ffd700] transition-colors"
              title="Copier cette sortie"
              data-testid={`copy-block-output-${messageId}-${seq}`}
            >
              {copied ? <Check className="w-3 h-3 text-[#ffd700]" /> : <Copy className="w-3 h-3" />}
              {copied ? "Copié" : "Copier la sortie"}
            </button>
            {truncated && (
              <span className="text-[10px] text-gray-500 font-mono">(sortie tronquée)</span>
            )}
          </div>
          <pre className="bg-black/50 p-2 text-[11px] text-gray-300 overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto">
            {out != null && out !== "" ? out : "Chargement…"}
          </pre>
        </div>
      )}
    </div>
  );
}

// Apparie un tableau de blocs tries en une liste d'elements de rendu :
// - un bloc `text`/`thinking` reste tel quel (rendu direct) ;
// - un `tool_call` est fusionne avec le `tool_output` consecutif de meme
//   step_index pour former une entree d'outil repliable unique ;
// - un `tool_output` orphelin (sans call) devient une entree d'outil seule.
function pairBlocks(sorted) {
  const items = [];
  for (let i = 0; i < sorted.length; i++) {
    const b = sorted[i];
    if (b.type === "tool_call") {
      const next = sorted[i + 1];
      if (
        next &&
        next.type === "tool_output" &&
        (next.step_index ?? -1) === (b.step_index ?? -2)
      ) {
        items.push({ kind: "tool", key: b.seq, call: b, output: next });
        i += 1;
      } else {
        items.push({ kind: "tool", key: b.seq, call: b, output: null });
      }
    } else if (b.type === "tool_output") {
      items.push({ kind: "tool", key: b.seq, call: null, output: b });
    } else {
      items.push({ kind: "block", key: b.seq, block: b });
    }
  }
  return items;
}

export default function MessageBlocks({ message, detailData, streaming = false }) {
  // Les details (charges paresseusement) peuvent completer un message dont le
  // listing n'embarque pas `blocks` ; on prefere le detail s'il est present.
  const blocks = detailData?.blocks ?? message.blocks;

  if (!Array.isArray(blocks) || blocks.length === 0) return null;

  const sorted = [...blocks].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));

  // Etape 6.2 : en live, un caret clignotant marque le dernier bloc texte
  // (parite visuelle avec l'ancien rendu streaming). En relecture, `streaming`
  // est faux : aucun caret.
  const lastTextSeq = streaming
    ? sorted.reduce(
        (acc, b) => (b.type === "text" ? b.seq : acc),
        null,
      )
    : null;

  const items = pairBlocks(sorted);

  return (
    <div className="space-y-3" data-testid={`blocks-${message.id}`}>
      {items.map((item) => {
        if (item.kind === "tool") {
          return (
            <ToolEntry
              key={item.key}
              call={item.call}
              output={item.output}
              messageId={message.id}
            />
          );
        }
        const block = item.block;
        if (block.type === "text") {
          return (
            <div
              key={block.seq}
              className="bg-transparent text-white border-2 border-white/20 p-4 rounded-bl-none shadow-[4px_4px_0_0_rgba(5,217,232,0.4)]"
            >
              <div className="md-body">
                <MarkdownBody>{block.text}</MarkdownBody>
                {streaming && block.seq === lastTextSeq && (
                  <span
                    className="inline-block w-2 h-4 ml-0.5 align-text-bottom bg-[#05d9e8] animate-pulse"
                    data-testid={`stream-caret-${message.id}`}
                  />
                )}
              </div>
            </div>
          );
        }
        if (block.type === "thinking") {
          return (
            <div
              key={block.seq}
              className="text-sm text-gray-400 italic border-l-2 border-gray-500 pl-3"
            >
              {block.text}
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}
