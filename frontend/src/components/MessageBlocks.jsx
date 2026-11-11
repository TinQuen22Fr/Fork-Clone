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
import { Copy, Check, Terminal } from "lucide-react";
import MarkdownBody from "@/components/MarkdownBody";

// Etape 6 (chantier rendu-blocs) : rendu « liste de blocs types » unifie.
//
// Parcourt `blocks[]` dans l'ordre `seq` et intercale texte et actions
// (appel d'outil + sortie), reconstituant l'enchainement reel « texte ->
// outil -> texte » a la maniere d'Emergent. Utilise a l'identique en live
// (via useStreamingMessage) et en relecture (message persiste) : c'est ce
// qui garantit la parite visuelle avant/apres la cloture.
//
// Un bloc `text` est rendu en markdown ; un couple `tool_call`/`tool_output`
// est rendu comme une entree d'outil (appel + sortie) rattachee a son etape.
// Les anciens messages (sans `blocks`) passent par l'ancien chemin et ne
// passent pas par ici (etape 7).

function toolArg(input) {
  if (!input || typeof input !== "object") return "";
  return input.command || input.path || JSON.stringify(input);
}

function ToolCallLine({ block, messageId }) {
  return (
    <div className="flex items-start gap-2 text-[#05d9e8] font-mono text-xs">
      <Terminal className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
      <span className="flex-1 break-all">
        {block.tool}({toolArg(block.input)})
      </span>
      <span className="flex-shrink-0 text-[10px] uppercase tracking-wider text-gray-500">
        étape {(block.step_index ?? 0) + 1}
      </span>
    </div>
  );
}

function ToolOutputBlock({ block, messageId }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(block.output || "");
    } catch {
      // ignore
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="border-l-2 border-[#ffd700]/50 pl-3">
      <div className="flex items-center gap-2 mb-1">
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-gray-500 hover:text-[#ffd700] transition-colors"
          title="Copier cette sortie"
          data-testid={`copy-block-output-${messageId}-${block.seq}`}
        >
          {copied ? <Check className="w-3 h-3 text-[#ffd700]" /> : <Copy className="w-3 h-3" />}
          {copied ? "Copié" : "Copier la sortie"}
        </button>
        {block.exit_code != null && (
          <span
            className={`font-mono text-[10px] px-1 py-0.5 border ${
              block.exit_code === 0
                ? "text-green-400 border-green-400/40"
                : "text-[#ff2a6d] border-[#ff2a6d]/40"
            }`}
          >
            exit {block.exit_code}
          </span>
        )}
        {block.truncated && (
          <span className="text-[10px] text-gray-500 font-mono">(sortie tronquée)</span>
        )}
      </div>
      <pre className="bg-black/50 p-2 text-[11px] text-gray-300 overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto">
        {block.output}
      </pre>
    </div>
  );
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

  return (
    <div className="space-y-3" data-testid={`blocks-${message.id}`}>
      {sorted.map((block) => {
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
        if (block.type === "tool_call") {
          return (
            <div key={block.seq} className="border-l-2 border-[#05d9e8]/50 pl-3 py-1">
              <ToolCallLine block={block} messageId={message.id} />
            </div>
          );
        }
        if (block.type === "tool_output") {
          return (
            <ToolOutputBlock key={block.seq} block={block} messageId={message.id} />
          );
        }
        return null;
      })}
    </div>
  );
}
