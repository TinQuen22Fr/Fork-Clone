import { useEffect, useRef, useState } from "react";
import { Smile } from "lucide-react";

/**
 * Catégories d'emojis courants. Liste volontairement compacte et sûre
 * (emojis Unicode largement supportés), pas de dépendance externe.
 */
const EMOJI_GROUPS = [
  {
    id: "smileys",
    label: "Smileys",
    emojis: [
      "😀", "😃", "😄", "😁", "😆", "😅", "🤣", "😂", "🙂", "🙃",
      "😉", "😊", "😇", "🥰", "😍", "🤩", "😘", "😗", "😚", "😙",
      "😋", "😛", "😜", "🤪", "😝", "🤑", "🤗", "🤭", "🤔", "🤐",
      "😐", "😶", "😏", "😒", "🙄", "😬", "😌", "😔", "😴", "😷",
      "🤒", "🤕", "🤠", "🥳", "😎", "🤓", "🧐", "😕", "😟", "🙁",
      "😮", "😯", "😲", "😳", "🥺", "😦", "😧", "😨", "😰", "😢",
      "😭", "😱", "😖", "😣", "😞", "😓", "😩", "😫", "🥱", "😤",
      "😡", "😠", "🤬", "😈", "👿", "💀", "💩", "🤡", "👻", "👽",
    ],
  },
  {
    id: "gestures",
    label: "Gestes",
    emojis: [
      "👍", "👎", "👌", "🤌", "🤏", "✌️", "🤞", "🤟", "🤘", "🤙",
      "👈", "👉", "👆", "👇", "☝️", "✋", "🤚", "🖐️", "🖖", "👋",
      "🤝", "🙏", "✍️", "💪", "🦾", "👏", "🙌", "👐", "🤲", "🤜",
      "🤛", "✊", "👊", "🫶", "❤️", "🧡", "💛", "💚", "💙", "💜",
      "🖤", "🤍", "🤎", "💔", "❣️", "💕", "💞", "💓", "💗", "💖",
    ],
  },
  {
    id: "objects",
    label: "Objets",
    emojis: [
      "🔥", "✨", "⭐", "🌟", "💫", "⚡", "💥", "💢", "💦", "💨",
      "🎉", "🎊", "🎁", "🏆", "🥇", "🎯", "🚀", "💡", "🔧", "⚙️",
      "🛠️", "🔨", "🧰", "💻", "🖥️", "⌨️", "🖱️", "📱", "🔋", "🔌",
      "📦", "📁", "📂", "📝", "📌", "📍", "🔒", "🔓", "🔑", "🔗",
      "✅", "❌", "⚠️", "❓", "❗", "💯", "🆗", "🆕", "🔝", "⏳",
    ],
  },
];

export default function EmojiPicker({ onPick, disabled }) {
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState(EMOJI_GROUPS[0].id);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    const onToggle = () => setOpen((v) => !v);
    window.addEventListener("forge:toggle-emoji", onToggle);
    return () => window.removeEventListener("forge:toggle-emoji", onToggle);
  }, []);

  const activeGroup =
    EMOJI_GROUPS.find((g) => g.id === group) || EMOJI_GROUPS[0];

  return (
    <div className="relative flex-shrink-0" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className={`btn-ghost border-2 flex-shrink-0 ${
          open
            ? "border-[#ffd700] text-[#ffd700]"
            : "border-white/20 hover:border-[#ffd700] hover:text-[#ffd700]"
        } disabled:opacity-40`}
        title="Insérer un emoji (Ctrl+E)"
        aria-label="Insérer un emoji"
        aria-expanded={open}
        data-testid="emoji-btn"
      >
        <Smile className="w-5 h-5" />
      </button>

      {open && (
        <div
          className="absolute bottom-full left-0 mb-2 z-50 w-[280px] max-w-[90vw] bg-[var(--bg-dock)] border-2 border-white/20 shadow-xl"
          data-testid="emoji-popover"
        >
          <div className="flex items-center border-b border-white/10">
            {EMOJI_GROUPS.map((g) => (
              <button
                key={g.id}
                type="button"
                onClick={() => setGroup(g.id)}
                className={`flex-1 px-2 py-2 text-[10px] font-mono uppercase tracking-wider transition-colors ${
                  g.id === group
                    ? "text-[#ffd700] border-b-2 border-[#ffd700]"
                    : "text-gray-400 hover:text-white"
                }`}
                data-testid={`emoji-tab-${g.id}`}
              >
                {g.label}
              </button>
            ))}
          </div>
          <div
            className="grid grid-cols-8 gap-0.5 p-2 max-h-[200px] overflow-y-auto"
            data-testid="emoji-grid"
          >
            {activeGroup.emojis.map((emo, i) => (
              <button
                key={`${activeGroup.id}-${i}`}
                type="button"
                onClick={() => {
                  onPick?.(emo);
                  setOpen(false);
                }}
                className="text-lg leading-none p-1 hover:bg-white/10 rounded transition-colors"
                data-testid="emoji-item"
                aria-label={`Emoji ${emo}`}
              >
                {emo}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
