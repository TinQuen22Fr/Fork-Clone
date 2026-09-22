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
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import "dayjs/locale/fr";
import { Copy, Check, Volume2, Square, ThumbsUp, ThumbsDown, RotateCcw, Terminal, Paperclip, Trash2 } from "lucide-react";

dayjs.extend(relativeTime);
dayjs.locale("fr");

const AI_AVATAR = "/logo-64.png";

const ttsAvailable =
  typeof window !== "undefined" && "speechSynthesis" in window;

function stripMarkdown(md) {
  return (md || "")
    .replace(/```[\s\S]*?```/g, " (bloc de code) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/[*_>#~-]/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function ActionButton({ onClick, title, active, testId, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      data-testid={testId}
      className={`p-1.5 border border-transparent rounded-sm transition-colors ${
        active
          ? "text-[#05d9e8] border-[#05d9e8]/40 bg-[#05d9e8]/10"
          : "text-gray-500 hover:text-white hover:border-white/20"
      }`}
    >
      {children}
    </button>
  );
}

export default function ChatMessage({
  message,
  isLast = false,
  onRegenerate,
  onFeedback,
  onDelete,
  regenerating = false,
}) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const [copiedTools, setCopiedTools] = useState(false);
  const [copiedStep, setCopiedStep] = useState(null);
  const [speaking, setSpeaking] = useState(false);

  const copyUser = async () => {
    try {
      await navigator.clipboard.writeText(message.content || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (_) {
      // ignore
    }
  };

  if (isUser) {
    return (
      <div
        className="flex justify-end mb-6 fade-in-up group"
        data-testid="chat-message-user"
      >
        <div className="max-w-[88%] sm:max-w-[80%] min-w-0 flex flex-col items-end">
          {(message.attachments?.length > 0 ||
            message.has_image ||
            message.file_name) && (
            <div className="mb-2 flex flex-wrap gap-1.5 justify-end">
              {(message.attachments?.length
                ? message.attachments.map((a) => a.name)
                : [message.file_name || "image"]
              ).map((name, i) => (
                <span
                  key={`${name}-${i}`}
                  className="inline-flex items-center gap-1.5 text-xs font-mono text-[#ffd700] border border-[#ffd700]/40 bg-[#ffd700]/10 px-2 py-1 max-w-[220px]"
                  data-testid={`attachment-chip-${message.id}-${i}`}
                >
                  <Paperclip className="w-3 h-3 flex-shrink-0" />
                  <span className="truncate">{name}</span>
                </span>
              ))}
            </div>
          )}
          <div className="bg-[#ffd700] text-black border-2 border-black p-4 font-medium rounded-br-none shadow-[4px_4px_0_0_#ff2a6d] whitespace-pre-wrap break-words">
            {message.content}
          </div>
          <div className="mt-1 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              onClick={copyUser}
              title={copied ? "Copié" : "Copier"}
              aria-label={copied ? "Copié" : "Copier"}
              data-testid={`copy-user-msg-${message.id}`}
              className="p-1 text-gray-600 hover:text-[#ffd700]"
            >
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
            {onDelete && (
              <button
                type="button"
                onClick={() => onDelete(message.id)}
                title="Supprimer ce message"
                aria-label="Supprimer ce message"
                data-testid={`delete-msg-${message.id}`}
                className="p-1 text-gray-600 hover:text-[#ff2a6d]"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content || "");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (_) {
      // ignore
    }
  };

  const formatStep = (s) =>
    `→ ${s.tool}(${
      s.input ? s.input.command || s.input.path || JSON.stringify(s.input) : ""
    })\n${s.output || ""}`;

  const copyToolStep = async (s, i) => {
    try {
      await navigator.clipboard.writeText(formatStep(s));
      setCopiedStep(i);
      setTimeout(() => setCopiedStep(null), 1500);
    } catch (_) {
      // ignore
    }
  };

  const copyTools = async () => {
    try {
      await navigator.clipboard.writeText(
        (message.tool_steps || []).map(formatStep).join("\n\n")
      );
      setCopiedTools(true);
      setTimeout(() => setCopiedTools(false), 1500);
    } catch (_) {
      // ignore
    }
  };

  const toggleSpeak = () => {
    if (!ttsAvailable) return;
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(stripMarkdown(message.content));
    utter.onend = () => setSpeaking(false);
    utter.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utter);
    setSpeaking(true);
  };

  const feedback = message.feedback || null;
  const ts = message.created_at ? dayjs(message.created_at).fromNow() : "";

  return (
    <div
      className="flex gap-4 mb-6 fade-in-up"
      data-testid="chat-message-assistant"
    >
      <img
        src={AI_AVATAR}
        alt="AI"
        className="w-10 h-10 border-2 border-white/30 object-cover flex-shrink-0"
      />
      <div className="flex-1 max-w-[88%] sm:max-w-[80%] min-w-0">
        <div className="text-xs uppercase tracking-[0.2em] text-[#05d9e8] font-bold mb-2 flex items-center gap-2 flex-wrap">
          <span data-testid={`msg-provider-${message.id}`}>
            // {(message.provider || "claude").toUpperCase().replace("_", " ")}
            {message.model ? ` · ${message.model}` : ""}
          </span>
          {message.fallback_used && (
            <span
              className="text-[#ffd700] border border-[#ffd700]/50 px-1.5 py-0.5 text-[9px] tracking-normal normal-case"
              title={
                Array.isArray(message.routing)
                  ? message.routing
                      .map((r) => `${r.provider} (${r.kind})`)
                      .join(" → ")
                  : "Bascule automatique"
              }
              data-testid={`fallback-badge-${message.id}`}
            >
              bascule auto
              {message.requested_provider &&
              message.requested_provider !== "auto"
                ? ` depuis ${message.requested_provider}`
                : ""}
            </span>
          )}
        </div>

        {Array.isArray(message.tool_steps) && message.tool_steps.length > 0 && (
          <details
            className="mb-2 border-2 border-[#ffd700]/40 bg-[#ffd700]/5 text-sm"
            data-testid={`tool-steps-${message.id}`}
          >
            <summary className="cursor-pointer px-3 py-2 flex items-center gap-2 text-[#ffd700] font-mono text-xs uppercase tracking-wider">
              <Terminal className="w-4 h-4" />
              {message.tool_steps.length} outil(s) utilisé(s)
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.preventDefault();
                  copyTools();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    copyTools();
                  }
                }}
                className="ml-auto flex items-center gap-1 border border-[#ffd700]/40 px-1.5 py-0.5 text-[10px] normal-case hover:bg-[#ffd700]/15 transition-colors"
                title="Copier tous les blocs d'outils"
                data-testid={`copy-tools-${message.id}`}
              >
                {copiedTools ? (
                  <Check className="w-3 h-3" />
                ) : (
                  <Copy className="w-3 h-3" />
                )}
                {copiedTools ? "Copié" : "Copier"}
              </span>
            </summary>
            <div className="px-3 pb-3 space-y-3">
              {message.tool_steps.map((s, i) => (
                <div key={i} className="border-l-2 border-[#05d9e8]/50 pl-3">
                  <div className="text-[#05d9e8] font-mono text-xs mb-1 flex items-start gap-2">
                    <span className="flex-1 break-all">
                      → {s.tool}({s.input && (s.input.command || s.input.path || JSON.stringify(s.input))})
                    </span>
                    <button
                      type="button"
                      onClick={() => copyToolStep(s, i)}
                      className="flex-shrink-0 text-gray-500 hover:text-[#ffd700] transition-colors"
                      title="Copier ce bloc"
                      data-testid={`copy-tool-step-${message.id}-${i}`}
                    >
                      {copiedStep === i ? (
                        <Check className="w-3.5 h-3.5 text-[#ffd700]" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                  <pre className="bg-black/50 p-2 text-[11px] text-gray-300 overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto">
                    {s.output}
                  </pre>
                </div>
              ))}
            </div>
          </details>
        )}

        <div className="bg-transparent text-white border-2 border-white/20 p-4 rounded-bl-none shadow-[4px_4px_0_0_rgba(5,217,232,0.4)]">
          <div className="md-body">
            <ReactMarkdown
              components={{
                img: ({ node, ...props }) => (
                  <a href={props.src} target="_blank" rel="noopener noreferrer" className="block my-3">
                    <img
                      {...props}
                      className="max-w-full rounded border-2 border-[#05d9e8]/60 shadow-[3px_3px_0_0_#ff2a6d] hover:opacity-90 transition-opacity"
                      loading="lazy"
                    />
                  </a>
                )
              }}
            >
              {message.content}
            </ReactMarkdown>
            {message.streaming && (
              <span
                className="inline-block w-2 h-4 align-middle bg-[#05d9e8] stream-caret"
                data-testid="stream-caret"
              />
            )}
          </div>
        </div>

        {/* Action bar */}
        {!message.streaming && (
        <div
          className="flex items-center gap-1 mt-2"
          data-testid={`msg-actions-${message.id}`}
        >
          <ActionButton
            onClick={copy}
            title={copied ? "Copié" : "Copier"}
            testId={`copy-msg-${message.id}`}
          >
            {copied ? <Check className="w-4 h-4 text-[#ffd700]" /> : <Copy className="w-4 h-4" />}
          </ActionButton>

          {ttsAvailable && (
            <ActionButton
              onClick={toggleSpeak}
              title={speaking ? "Arrêter la lecture" : "Lire à voix haute"}
              active={speaking}
              testId={`tts-msg-${message.id}`}
            >
              {speaking ? <Square className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
            </ActionButton>
          )}

          <ActionButton
            onClick={() => onFeedback && onFeedback(message, "up")}
            title="Bonne réponse"
            active={feedback === "up"}
            testId={`feedback-up-${message.id}`}
          >
            <ThumbsUp className="w-4 h-4" />
          </ActionButton>

          <ActionButton
            onClick={() => onFeedback && onFeedback(message, "down")}
            title="Mauvaise réponse"
            active={feedback === "down"}
            testId={`feedback-down-${message.id}`}
          >
            <ThumbsDown className="w-4 h-4" />
          </ActionButton>

          {isLast && (
            <ActionButton
              onClick={() => onRegenerate && onRegenerate(message)}
              title="Régénérer la réponse"
              testId={`regenerate-${message.id}`}
            >
              <RotateCcw className={`w-4 h-4 ${regenerating ? "animate-spin" : ""}`} />
            </ActionButton>
          )}

          {onDelete && (
            <ActionButton
              onClick={() => onDelete(message.id)}
              title="Supprimer ce message"
              testId={`delete-msg-${message.id}`}
            >
              <Trash2 className="w-4 h-4" />
            </ActionButton>
          )}

          {ts && (
            <span
              className="ml-2 text-[11px] text-gray-600 font-mono"
              data-testid={`msg-timestamp-${message.id}`}
            >
              {ts}
            </span>
          )}
          {message.stopped && (
            <span
              className="ml-2 text-[10px] font-mono uppercase tracking-wider text-[#ff2a6d] border border-[#ff2a6d]/50 px-1.5 py-0.5"
              data-testid={`stopped-badge-${message.id}`}
            >
              arrêté
            </span>
          )}
        </div>
        )}
      </div>
    </div>
  );
}
