/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 *
 * Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
 * modifier selon les termes de la GNU General Public License telle que publiée
 * par la Free Software Foundation, soit la version 3, soit (à votre choix)
 * toute version ultérieure. Il est distribué SANS AUCUNE GARANTIE.
 * Voir le fichier LICENSE ou <https://www.gnu.org/licenses/>.
 */

import React, { useState, useCallback } from "react";
import api from "@/lib/api";
import { speak, stopSpeech } from "@/lib/tts";
import MarkdownBody from "@/components/MarkdownBody";
import MessageBlocks from "@/components/MessageBlocks";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import "dayjs/locale/fr";
import { Copy, Check, Volume2, Square, ThumbsUp, ThumbsDown, RotateCcw, RotateCw, Terminal, Paperclip, Trash2 } from "lucide-react";

dayjs.extend(relativeTime);
dayjs.locale("fr");

const AI_AVATAR = "/logo-64.png";

// Miroir front de backend._make_tool_blocks : limite de troncature des sorties.
const STEP_LOG_TRUNC = 1200;

/**
 * Reconstruit une liste de blocs ordonnes (type="text" / "tool_call" / "tool_output")
 * pour les anciens messages persistes AVANT l'etape 4 (donc sans champ `blocks[]`).
 *
 * Contrat identique a `MessageBlocks` : chaque bloc porte `type`, `seq` (monotone),
 * et pour les outils `tool`, `input`/`output`, `step_index`, `exit_code?`, `truncated?`.
 * Invariant : le texte global vient en premier (seq 0), puis les paires d'outils.
 *
 * @param {{content?: string}} message
 * @param {Array<{tool?: string, input?: object, output?: string}>} toolSteps
 * @returns {Array<object>}
 */
export function buildSyntheticBlocks(message, toolSteps) {
  const blocks = [];
  let seq = 0;

  const content = message?.content || "";
  if (content) {
    blocks.push({ type: "text", seq: seq++, text: content });
  }

  const steps = Array.isArray(toolSteps) ? toolSteps : [];
  steps.forEach((s, i) => {
    const rawOut = s?.output || "";
    const truncated = rawOut.length > STEP_LOG_TRUNC;
    const out = truncated
      ? rawOut.slice(0, STEP_LOG_TRUNC) +
        `\n... [sortie tronquee : ${rawOut.length} caracteres au total]`
      : rawOut;

    blocks.push({
      type: "tool_call",
      seq: seq++,
      tool: s?.tool,
      input: s?.input || {},
      step_index: i,
    });

    const outBlock = {
      type: "tool_output",
      seq: seq++,
      tool: s?.tool,
      output: out,
      truncated,
      step_index: i,
    };
    const ec = blockExitCode(rawOut);
    if (ec != null) outBlock.exit_code = ec;
    blocks.push(outBlock);
  });

  return blocks;
}

// Miroir front de backend._block_exit_code : extrait un code de sortie type `exit N`.
function blockExitCode(output) {
  if (!output) return null;
  const m = String(output).match(/(?:^|\n)\s*exit[ _]?code\s*[:=]?\s*(\d{1,3})\b/i);
  return m ? Number(m[1]) : null;
}

function stripMarkdown(md) {
  return (md || "")
    .replace(/```[\s\S]*?```/g, " (bloc de code) ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/[*_>#~-]/g, " ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

// Decoupe un message en paragraphes lisibles (blocs de code ignores).
// Un paragraphe trop long est redecoupe par phrases pour demarrer vite.
function splitForSpeech(md, maxLen = 350) {
  const noCode = (md || "").replace(/```[\s\S]*?(```|$)/g, "\n\n");
  const out = [];
  for (const para of noCode.split(/\n\s*\n/)) {
    const clean = stripMarkdown(para);
    if (!clean) continue;
    if (clean.length <= maxLen) {
      out.push(clean);
      continue;
    }
    let cur = "";
    for (const sent of clean.match(/[^.!?…]+[.!?…]*\s*/g) || [clean]) {
      if (cur && (cur + sent).length > maxLen) {
        out.push(cur.trim());
        cur = "";
      }
      cur += sent;
    }
    if (cur.trim()) out.push(cur.trim());
  }
  return out;
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

function ChatMessage({
  message,
  isLast = false,
  onRegenerate,
  onReplay,
  onFeedback,
  onDelete,
  regenerating = false,
  project,
  onRollbackStep,
}) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const [ttsError, setTtsError] = useState("");
  const [copiedTools, setCopiedTools] = useState(false);
  const [copiedStep, setCopiedStep] = useState(null);
  const [copiedStepGroup, setCopiedStepGroup] = useState(null);
  const [rollbackBusy, setRollbackBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  // Rendu paresseux : le contenu des blocs repliés (étapes, outils) n'est
  // construit dans le DOM qu'à l'ouverture, ce qui allège l'affichage d'une
  // conversation riche en sorties d'outils.
  const [toolsOpen, setToolsOpen] = useState(false);
  const [openSteps, setOpenSteps] = useState({});
  // --- Chargement paresseux des sorties d'outils ---
  // Le listing renvoie tool_steps/steps SANS output (allegement payload).
  // On charge le detail complet du message uniquement a l'ouverture.
  const [detailData, setDetailData] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const fetchDetails = useCallback(async () => {
    if (detailData || detailLoading) return;
    setDetailLoading(true);
    try {
      const { data } = await api.get(`/messages/${message.id}/details`);
      setDetailData(data);
    } catch (_) {
      // En cas d'echec, on laisse les output a null (texte « Chargement… » reste)
    } finally {
      setDetailLoading(false);
    }
  }, [message.id, detailData, detailLoading]);

  // Merge : si les details sont charges, on utilise leurs tool_steps/steps
  // (avec output complet) ; sinon on garde ceux du listing (output=null).
  const toolSteps = detailData?.tool_steps ?? message.tool_steps;
  const steps = detailData?.steps ?? message.steps;

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
            {onReplay && (
              <button
                type="button"
                onClick={() => onReplay(message)}
                title="Rejouer ce prompt"
                aria-label="Rejouer ce prompt"
                data-testid={`replay-msg-${message.id}`}
                className="p-1 text-gray-600 hover:text-[#ffd700]"
              >
                <RotateCw className="w-3.5 h-3.5" />
              </button>
            )}
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

  const formatStepGroup = (st) => {
    const parts = [];
    if (st.intention) parts.push(`Intention : ${st.intention}`);
    (Array.isArray(st.tools) ? st.tools : []).forEach((s, j) => {
      parts.push(`[Outils ${j + 1}] ${formatStep(s)}`);
    });
    return parts.join("\n\n");
  };

  const copyStepGroup = async (st, i) => {
    try {
      await navigator.clipboard.writeText(formatStepGroup(st));
      setCopiedStepGroup(i);
      setTimeout(() => setCopiedStepGroup(null), 1500);
    } catch (_) {
      // ignore
    }
  };

  const copyTools = async () => {
    try {
      await navigator.clipboard.writeText(
        (toolSteps || []).map(formatStep).join("\n\n")
      );
      setCopiedTools(true);
      setTimeout(() => setCopiedTools(false), 1500);
    } catch (_) {
      // ignore
    }
  };

  const toggleSpeak = async () => {
    if (speaking) {
      stopSpeech();
      setSpeaking(false);
      return;
    }
    const chunks = splitForSpeech(message.content);
    if (!chunks.length) return;
    setSpeaking(true);
    setTtsError("");
    const showError = (e) => {
      setSpeaking(false);
      if (e.name !== "CanceledError" && e.code !== "ERR_CANCELED") {
        setTtsError(
          e.ttsDetail
            ? `Synthèse vocale : ${e.ttsDetail}`.slice(0, 160)
            : "Lecture impossible : " + (e.message || "erreur inconnue").slice(0, 120)
        );
        setTimeout(() => setTtsError(""), 8000);
      }
    };
    try {
      await speak(chunks, {
        id: message.id,
        onEnd: () => setSpeaking(false),
        onError: showError,
      });
    } catch (e) {
      showError(e);
    }
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

        {(steps.length > 0 || message.steps_count > 0) && (
          <div
            className="mb-3 space-y-2"
            data-testid={`steps-${message.id}`}
          >
            {!steps.length && detailLoading && (
              <div className="text-center text-gray-400 text-xs py-4 animate-pulse">Chargement des étapes…</div>
            )}
            {steps.map((st, i) => {
              const toolCount = Array.isArray(st.tools) ? st.tools.length : 0;
              const isDone = st.status === "done";
              return (
                <details
                  key={st.index ?? i}
                  className="group border-2 border-[#05d9e8]/50 bg-[#05d9e8]/5"
                  data-testid={`step-${message.id}-${st.index ?? i}`}
                  onToggle={(e) => {
                    const k = st.index ?? i;
                    const o = e.currentTarget.open;
                    setOpenSteps((prev) => (!!prev[k] === o ? prev : { ...prev, [k]: o }));
                    if (o) fetchDetails();
                  }}
                >
                  <summary className="px-3 py-1.5 flex items-center gap-2 cursor-pointer list-none [&::-webkit-details-marker]:hidden select-none">
                    <span className="font-mono text-[11px] uppercase tracking-wider text-[#05d9e8] transition-transform group-open:rotate-90">
                      {"\u25b6"}
                    </span>
                    <span className="font-mono text-[11px] uppercase tracking-wider text-[#05d9e8]">
                      Etape {(st.index ?? i) + 1}
                    </span>
                    <span
                      className={`font-mono text-[10px] uppercase tracking-wider ${
                        isDone ? "text-green-400" : "text-[#ffd700]"
                      }`}
                    >
                      {isDone ? "terminee" : "en cours"}
                    </span>
                    <span className="ml-auto font-mono text-[10px] uppercase tracking-wider text-gray-500">
                      {toolCount} outil{toolCount > 1 ? "s" : ""}
                    </span>
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(e) => {
                        e.preventDefault();
                        copyStepGroup(st, st.index ?? i);
                      }}
                      className="flex items-center gap-1 border border-[#05d9e8]/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-[#05d9e8] hover:bg-[#05d9e8]/15 transition-colors"
                      title="Copier cette etape"
                      data-testid={`copy-step-${message.id}-${st.index ?? i}`}
                    >
                      {copiedStepGroup === (st.index ?? i) ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                      {copiedStepGroup === (st.index ?? i) ? "Copie" : "Copier"}
                    </span>
                    {project && onRollbackStep && (
                      <span
                        role="button"
                        tabIndex={0}
                        data-testid={`rollback-step-${message.id}-${st.index ?? i}`}
                        title="Annuler cette etape : restaure le workspace au snapshot precedent"
                        onClick={(e) => {
                          e.preventDefault();
                          if (rollbackBusy) return;
                          const dest = st.index ?? i;
                          const nombre = dest + 1;
                          if (
                            window.confirm(
                              `Annuler l'etape ${nombre} ? Les modifications de fichiers effectuees par cette etape seront restaurees au snapshot precedent.`
                            )
                          ) {
                            setRollbackBusy(true);
                            Promise.resolve(
                              onRollbackStep ? onRollbackStep(message.id) : null
                            ).finally(() => setRollbackBusy(false));
                          }
                        }}
                        className={`flex items-center gap-1 border px-1.5 py-0.5 text-[10px] uppercase tracking-wider transition-colors ${
                          rollbackBusy
                            ? "border-gray-500 text-gray-500 cursor-wait"
                            : "border-[#ff6b6b]/40 text-[#ff6b6b] hover:bg-[#ff6b6b]/15"
                        }`}
                      >
                        <RotateCcw
                          className={`w-3 h-3 ${rollbackBusy ? "animate-spin" : ""}`}
                        />
                        {rollbackBusy ? "Restauration..." : "Annuler cette etape"}
                      </span>
                    )}
                  </summary>
                  {openSteps[st.index ?? i] && st.intention && (
                    <div className="px-3 py-2 text-sm text-gray-200 whitespace-pre-wrap border-t border-[#05d9e8]/30 border-b border-[#05d9e8]/20">
                      {st.intention}
                    </div>
                  )}
                  {openSteps[st.index ?? i] && Array.isArray(st.tools) && st.tools.length > 0 && (
                    <div className="px-3 py-2 space-y-2 border-t border-[#05d9e8]/20">
                      {st.tools.map((s, j) => (
                        <div key={j} className="border-l-2 border-[#ffd700]/50 pl-3">
                          <div className="text-[#ffd700] font-mono text-xs mb-1 flex items-start gap-2">
                            <span className="flex-1 break-all">
                              {"\u2192"} {s.tool}({s.input && (s.input.command || s.input.path || JSON.stringify(s.input))})
                            </span>
                            <button
                              type="button"
                              onClick={() => copyToolStep(s, `g${st.index ?? i}-${j}`)}
                              className="flex-shrink-0 text-gray-500 hover:text-[#ffd700] transition-colors"
                              title="Copier ce bloc"
                              data-testid={`copy-step-group-${message.id}-${st.index ?? i}-${j}`}
                            >
                              {copiedStep === `g${st.index ?? i}-${j}` ? (
                                <Check className="w-3.5 h-3.5 text-[#ffd700]" />
                              ) : (
                                <Copy className="w-3.5 h-3.5" />
                              )}
                            </button>
                          </div>
                          <pre className="bg-black/50 p-2 text-[11px] text-gray-300 overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto">
                            {s.output ?? (detailLoading ? "Chargement…" : "")}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}
                </details>
              );
            })}
          </div>
        )}

        {(toolSteps.length > 0 || message.tool_steps_count > 0) && (
          <details
            className="mb-2 border-2 border-[#ffd700]/40 bg-[#ffd700]/5 text-sm"
            data-testid={`tool-steps-${message.id}`}
            onToggle={(e) => {
              const open = e.currentTarget.open;
              setToolsOpen(open);
              if (open) fetchDetails();
            }}
          >
            <summary className="cursor-pointer px-3 py-2 flex items-center gap-2 text-[#ffd700] font-mono text-xs uppercase tracking-wider">
              <Terminal className="w-4 h-4" />
              {(toolSteps.length || message.tool_steps_count || 0)} outil(s) utilisé(s)
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
            {toolsOpen && (
            <div className="px-3 pb-3 space-y-3">
              {!toolSteps.length && detailLoading && (
                <div className="text-center text-gray-400 text-xs py-4 animate-pulse">Chargement des détails…</div>
              )}
              {toolSteps.map((s, i) => (
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
                    {s.output ?? (detailLoading ? "Chargement…" : "")}
                  </pre>
                </div>
              ))}
            </div>
            )}
          </details>
        )}

        {/* Etape 7 : messages recents -> rendu par blocs ordonnes.
            Anciens messages (sans `blocks[]`) mais avec des outils -> blocs
            synthetiques reconstruits (fallback retrocompatible, etape 6.5).
            Message texte seul -> rendu markdown direct. */}
        {Array.isArray(message.blocks) && message.blocks.length > 0 ? (
          <MessageBlocks
            message={message}
            detailData={detailData}
            streaming={message.streaming}
          />
        ) : Array.isArray(toolSteps) && toolSteps.length > 0 ? (
          <MessageBlocks
            message={{ ...message, blocks: buildSyntheticBlocks(message, toolSteps) }}
            detailData={detailData}
            streaming={message.streaming}
          />
        ) : (
          <div className="bg-transparent text-white border-2 border-white/20 p-4 rounded-bl-none shadow-[4px_4px_0_0_rgba(5,217,232,0.4)]">
            <div className="md-body">
              <MarkdownBody>{message.content}</MarkdownBody>
              {message.streaming && (
                <span
                  className="inline-block w-2 h-4 align-middle bg-[#05d9e8] stream-caret"
                  data-testid="stream-caret"
                />
              )}
            </div>
          </div>
        )}

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

          <ActionButton
            onClick={toggleSpeak}
            title={
              ttsError || (speaking ? "Arrêter la lecture" : "Lire à voix haute")
            }
            active={speaking}
            testId={`tts-msg-${message.id}`}
          >
            {speaking ? <Square className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </ActionButton>
          {ttsError && (
            <span
              className="text-[10px] font-mono text-[#ff2a6d]"
              data-testid={`tts-error-${message.id}`}
            >
              {ttsError}
            </span>
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
              title="Réessayer la réponse"
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

// Evite de re-rendre tous les messages a chaque fragment recu en streaming.
export default React.memo(ChatMessage, (a, b) =>
  a.message.id === b.message.id &&
  a.message.content === b.message.content &&
  a.message.blocks === b.message.blocks &&
  a.message.tool_steps === b.message.tool_steps &&
  a.message.steps === b.message.steps &&
  a.isLast === b.isLast &&
  a.regenerating === b.regenerating
);
