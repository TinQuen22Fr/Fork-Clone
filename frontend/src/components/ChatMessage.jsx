import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import dayjs from "dayjs";
import relativeTime from "dayjs/plugin/relativeTime";
import "dayjs/locale/fr";
import { Copy, Check, Volume2, Square, ThumbsUp, ThumbsDown, RotateCcw, Terminal } from "lucide-react";

dayjs.extend(relativeTime);
dayjs.locale("fr");

const AI_AVATAR =
  "https://static.prod-images.emergentagent.com/jobs/ed4e7d81-8953-45de-a23c-d48e941ecd1d/images/c7086dd78e0bfe64bf4737e1adc397f7dc41438f8c4eee76e4b486e0869312c3.png";

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
  regenerating = false,
}) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
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
        <div className="max-w-[80%] flex flex-col items-end">
          {message.has_image && (
            <div className="mb-2 text-xs font-mono text-[#ffd700] text-right">
              [image attached]
            </div>
          )}
          <div className="bg-[#ffd700] text-black border-2 border-black p-4 font-medium rounded-br-none shadow-[4px_4px_0_0_#ff2a6d] whitespace-pre-wrap break-words">
            {message.content}
          </div>
          <button
            type="button"
            onClick={copyUser}
            title={copied ? "Copié" : "Copier"}
            aria-label={copied ? "Copié" : "Copier"}
            data-testid={`copy-user-msg-${message.id}`}
            className="mt-1 p-1 text-gray-600 hover:text-[#ffd700] opacity-0 group-hover:opacity-100 transition-opacity"
          >
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
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
      <div className="flex-1 max-w-[80%]">
        <div className="text-xs uppercase tracking-[0.2em] text-[#05d9e8] font-bold mb-2">
          // CLAUDE
        </div>

        {Array.isArray(message.tool_steps) && message.tool_steps.length > 0 && (
          <details
            className="mb-2 border-2 border-[#ffd700]/40 bg-[#ffd700]/5 text-sm"
            data-testid={`tool-steps-${message.id}`}
          >
            <summary className="cursor-pointer px-3 py-2 flex items-center gap-2 text-[#ffd700] font-mono text-xs uppercase tracking-wider">
              <Terminal className="w-4 h-4" />
              {message.tool_steps.length} outil(s) utilisé(s)
            </summary>
            <div className="px-3 pb-3 space-y-3">
              {message.tool_steps.map((s, i) => (
                <div key={i} className="border-l-2 border-[#05d9e8]/50 pl-3">
                  <div className="text-[#05d9e8] font-mono text-xs mb-1">
                    → {s.tool}({s.input && (s.input.command || s.input.path || JSON.stringify(s.input))})
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
          </div>
        </div>

        {/* Action bar */}
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

          {ts && (
            <span
              className="ml-2 text-[11px] text-gray-600 font-mono"
              data-testid={`msg-timestamp-${message.id}`}
            >
              {ts}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
