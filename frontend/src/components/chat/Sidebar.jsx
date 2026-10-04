/*
 * Claude Unchained Forge - Copyright (C) 2026 Quentin Dumont
 * GNU GPL v3 ou ulterieure. Distribue SANS AUCUNE GARANTIE.
 */

import {
  Plus,
  LogOut,
  X,
  MessageSquare,
  Pencil,
  Check,
  Trash2,
} from "lucide-react";
import ThemeToggle from "@/components/chat/ThemeToggle";

// Barre laterale : liste des conversations, actions (nouvelle, renommer,
// supprimer) et pied de page. Composant pilote par Chat.jsx (props only).
export default function Sidebar({
  sidebarOpen,
  onClose,
  theme,
  onToggleTheme,
  conversations,
  activeId,
  onSelectConversation,
  onNewConversation,
  editingId,
  editingTitle,
  onEditingTitleChange,
  onStartRename,
  onCancelRename,
  onSubmitRename,
  onDeleteConversation,
  user,
  onLogout,
}) {
  return (
    <aside
      className={`${
        sidebarOpen ? "translate-x-0" : "-translate-x-full"
      } lg:translate-x-0 fixed lg:relative z-30 lg:z-auto top-0 left-0 h-full w-72 max-w-[85vw] flex-shrink-0 bg-[var(--bg-sidebar)] border-r-2 border-white/20 flex flex-col transition-transform`}
      data-testid="chat-sidebar"
    >
      <div className="p-5 border-b-2 border-white/10 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <img src="/logo-64.png" alt="" className="w-7 h-7 flex-shrink-0" />
          <div>
            <div className="font-heading font-black text-sm tracking-tight">
              THE FORGE
            </div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-mono">
              claude
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
          <button
            className="lg:hidden btn-ghost"
            onClick={onClose}
            data-testid="close-sidebar-btn"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      <div className="p-4">
        <button
          onClick={onNewConversation}
          className="btn-primary w-full flex items-center justify-center gap-2"
          data-testid="new-chat-btn"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-3 pb-4">
        <div className="text-xs uppercase tracking-[0.2em] text-gray-500 font-bold px-2 mb-2">
          Recent
        </div>
        {conversations.length === 0 && (
          <div className="text-gray-600 text-sm px-2">No conversations yet.</div>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            onClick={() => onSelectConversation(c.id)}
            className={`group flex items-center gap-2 px-3 py-2.5 cursor-pointer border-2 mb-2 transition-all ${
              c.id === activeId
                ? "border-[#ff2a6d] bg-[#ff2a6d]/10 shadow-[4px_4px_0_0_#ffd700]"
                : "border-transparent hover:border-white/20 hover:bg-white/5"
            }`}
            data-testid={`conv-item-${c.id}`}
          >
            <MessageSquare className="w-4 h-4 flex-shrink-0 text-gray-400" />
            {editingId === c.id ? (
              <form
                onSubmit={(e) => onSubmitRename(c.id, e)}
                onClick={(e) => e.stopPropagation()}
                className="flex-1 flex items-center gap-1"
              >
                <input
                  autoFocus
                  value={editingTitle}
                    onChange={(e) => onEditingTitleChange(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") onCancelRename();
                    }}
                    onBlur={() => onSubmitRename(c.id)}
                    className="flex-1 min-w-0 bg-black/60 border border-[#ffd700] text-sm px-1.5 py-1 outline-none text-white"
                    data-testid={`rename-input-${c.id}`}
                  />
                  <button
                    type="submit"
                    className="text-[#ffd700] hover:text-white flex-shrink-0"
                    data-testid={`rename-submit-${c.id}`}
                  >
                    <Check className="w-4 h-4" />
                  </button>
                </form>
              ) : (
                <>
                  <div className="flex-1 truncate text-sm font-medium">
                    {c.title || "New Chat"}
                  </div>
                  <button
                    onClick={(e) => onStartRename(c, e)}
                    className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-[#ffd700] transition-opacity"
                    title="Rename"
                    data-testid={`rename-conv-${c.id}`}
                  >
                    <Pencil className="w-4 h-4" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteConversation(c.id);
                    }}
                    className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-[#ff2a6d] transition-opacity"
                    title="Delete"
                    data-testid={`delete-conv-${c.id}`}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </>
              )}
            </div>
          ))}
        </div>

        <div className="p-4 border-t-2 border-white/10">
          <div className="flex items-center justify-between gap-2">
            <div className="flex-1 min-w-0">
              <div className="text-xs uppercase tracking-[0.2em] text-gray-500 font-bold">
                Signed in
              </div>
              <div className="text-sm font-medium truncate" data-testid="current-user-email">
                {user?.email}
              </div>
            </div>
            <button
              onClick={onLogout}
              className="btn-ghost border-2 border-white/20 hover:border-[#ff2a6d] hover:text-[#ff2a6d]"
              title="Logout"
              data-testid="logout-btn"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>

          <div
            className="mt-3 flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.15em] text-gray-600"
            data-testid="license-footer"
          >
            <a
              href="https://www.gnu.org/licenses/gpl-3.0.html"
              target="_blank"
              rel="noreferrer"
              className="border border-white/15 px-1.5 py-0.5 hover:border-[#ffd700] hover:text-[#ffd700] transition-colors"
              title="Logiciel libre sous GNU GPL v3"
              data-testid="license-badge"
            >
              GPLv3
            </a>
            <a
              href="https://github.com/TinQuen22Fr/Fork-Clone/tree/claude-ai"
              target="_blank"
              rel="noreferrer"
              className="hover:text-[#ffd700] transition-colors"
              title="Code source libre — Copyright (C) 2026 Quentin Dumont"
              data-testid="source-code-link"
            >
              Code source
            </a>
          </div>
        </div>
      </aside>

  );
}
