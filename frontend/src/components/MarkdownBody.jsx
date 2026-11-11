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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Copy, Check } from "lucide-react";

// Bloc de code markdown avec bouton de copie. Extrait de ChatMessage pour être
// partagé entre l'ancien chemin (contenu global) et le nouveau (blocs ordonnés),
// sans dupliquer le rendu ni casser la mémoïsation de ChatMessage.
export function MdCodeBlock({ node, children, ...props }) {
  const [done, setDone] = useState(false);
  const extract = (n) => {
    if (!n) return "";
    if (n.type === "text") return n.value || "";
    return (n.children || []).map(extract).join("");
  };
  const raw = extract(node).replace(/\n$/, "");
  const lang = (node?.children?.[0]?.properties?.className || [])
    .map(String).find((c) => c.startsWith("language-"))?.slice(9) || "";
  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(raw);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = raw;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
    setDone(true);
    setTimeout(() => setDone(false), 1500);
  };
  return (
    <div className="md-codeblock relative my-3 border-2 border-white/20 bg-black/50">
      <div className="flex items-center justify-between px-2 py-1 border-b border-white/10 text-[10px] uppercase tracking-wider text-gray-400">
        <span>{lang || "code"}</span>
        <button
          type="button"
          onClick={doCopy}
          data-testid="md-code-copy"
          className="flex items-center gap-1 px-2 py-0.5 border border-white/20 hover:border-[#05d9e8] hover:text-[#05d9e8] transition-colors"
        >
          {done ? <Check size={12} /> : <Copy size={12} />}
          {done ? "Copié" : "Copier"}
        </button>
      </div>
      <pre {...props} className="p-3 text-[12px] overflow-x-auto m-0 bg-transparent">{children}</pre>
    </div>
  );
}

// Rendu markdown unique (GFM) utilisé par le corps des messages, qu'ils soient
// rendus via l'ancien chemin (content global) ou le nouveau (blocs ordonnés).
export default function MarkdownBody({ children }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        img: ({ node, ...props }) => (
          <a href={props.src} target="_blank" rel="noopener noreferrer" className="block my-3">
            <img
              {...props}
              className="max-w-full rounded border-2 border-[#05d9e8]/60 shadow-[3px_3px_0_0_#ff2a6d] hover:opacity-90 transition-opacity"
              loading="lazy"
            />
          </a>
        ),
        pre: MdCodeBlock,
        table: ({ node, ...props }) => (
          <div className="md-table-wrap">
            <table {...props} />
          </div>
        )
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
