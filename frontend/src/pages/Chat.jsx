import React, { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError } from "@/lib/api";
import ChatMessage from "@/components/ChatMessage";
import {
  Plus,
  Send,
  ImagePlus,
  Trash2,
  LogOut,
  Flame,
  X,
  MessageSquare,
  Menu,
  Pencil,
  Check,
  Cpu,
  Square,
  Paperclip,
} from "lucide-react";

export default function Chat() {
  const { user, logout } = useAuth();
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [imageFile, setImageFile] = useState(null);
    const [attachedFile, setAttachedFile] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const [sending, setSending] = useState(false);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [regenerating, setRegenerating] = useState(false);
  const [provider, setProvider] = useState(
    () => localStorage.getItem("forge_provider") || "claude"
  );
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const abortControllerRef = useRef(null);

  // Initial load - fetch conversations
  useEffect(() => {
    if (user && user !== false && user !== null) fetchConversations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  // Load messages when active conv changes
  useEffect(() => {
    if (activeId) loadMessages(activeId);
    else setMessages([]);
  }, [activeId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  // redirect if logged out
  if (user === false) return <Navigate to="/login" replace />;
  if (user === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#050505]">
        <div className="typing-dots">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    );
  }

  const fetchConversations = async () => {
    try {
      const { data } = await api.get("/conversations");
      const list = Array.isArray(data) ? data : (data?.conversations || []);
      setConversations(list);
      if (list.length > 0 && !activeId) setActiveId(list[0].id);
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const loadMessages = async (cid) => {
    setLoadingMsgs(true);
    try {
      const { data } = await api.get(`/conversations/${cid}/messages`);
      setMessages(data);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoadingMsgs(false);
    }
  };

  const newConversation = async () => {
    try {
      const { data } = await api.post("/conversations", { title: "New Chat" });
      setConversations((prev) => [data, ...prev]);
      setActiveId(data.id);
      setMessages([]);
      setSidebarOpen(false);
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const deleteConversation = async (cid) => {
    if (!window.confirm("Delete this conversation?")) return;
    try {
      await api.delete(`/conversations/${cid}`);
      setConversations((prev) => prev.filter((c) => c.id !== cid));
      if (activeId === cid) {
        setActiveId(null);
        setMessages([]);
      }
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const startRename = (c, e) => {
    e?.stopPropagation();
    setEditingId(c.id);
    setEditingTitle(c.title || "");
  };

  const cancelRename = () => {
    setEditingId(null);
    setEditingTitle("");
  };

  const submitRename = async (cid, e) => {
    e?.preventDefault();
    e?.stopPropagation();
    const title = editingTitle.trim();
    if (!title) {
      cancelRename();
      return;
    }
    setConversations((prev) =>
      prev.map((c) => (c.id === cid ? { ...c, title } : c))
    );
    cancelRename();
    try {
      await api.patch(`/conversations/${cid}`, { title });
    } catch (e2) {
      setError(formatApiError(e2));
      fetchConversations();
    }
  };

  const regenerate = async (message) => {
    if (regenerating || sending) return;
    setRegenerating(true);
    setError("");
    try {
      const { data } = await api.post("/chat/regenerate", {
        conversation_id: activeId,
      });
      setMessages((prev) => {
        const copy = [...prev];
        const idx = copy.findIndex((m) => m.id === message.id);
        if (idx !== -1) copy[idx] = data.ai_message;
        else copy.push(data.ai_message);
        return copy;
      });
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setRegenerating(false);
    }
  };

  const submitFeedback = async (message, value) => {
    const newVal = message.feedback === value ? null : value;
    setMessages((prev) =>
      prev.map((m) => (m.id === message.id ? { ...m, feedback: newVal } : m))
    );
    try {
      await api.patch(`/messages/${message.id}/feedback`, { feedback: newVal });
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const TEXT_FILE_EXT = [
    ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md",
    ".csv", ".html", ".css", ".yaml", ".yml", ".sh", ".java",
    ".c", ".cpp", ".go", ".rs", ".rb", ".php", ".sql", ".xml",
  ];

  const onPickFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) {
      setError("Le fichier ne doit pas dépasser 10 Mo.");
      return;
    }
    if (f.type.startsWith("image/")) {
      setImageFile(f);
      const reader = new FileReader();
      reader.onload = (ev) => setImagePreview(ev.target.result);
      reader.readAsDataURL(f);
    } else {
      setAttachedFile(f);
    }
  };

  const clearImage = () => {
    setImageFile(null); setAttachedFile(null);
    setImagePreview(null);
    setAttachedFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const sendMessage = async (e) => {
    e?.preventDefault();
    if (sending) return;
    const trimmed = text.trim();
    if (!trimmed && !imageFile && !attachedFile) return;
    setError("");

    let convId = activeId;
    if (!convId) {
      try {
        const { data } = await api.post("/conversations", { title: "New Chat" });
        setConversations((prev) => [data, ...prev]);
        convId = data.id;
        setActiveId(convId);
      } catch (err) {
        setError(formatApiError(err));
        return;
      }
    }

    // Optimistic user message
    const optimisticUser = {
      id: `tmp-${Date.now()}`,
      conversation_id: convId,
      role: "user",
      content: trimmed || "(image)",
      has_image: !!imageFile,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser]);
    const sentText = trimmed;
    const sentImage = imageFile;
    const sentAttached = attachedFile;
    setText("");
    clearImage();
    setSending(true);

    abortControllerRef.current = new AbortController();

    try {
      const form = new FormData();
      form.append("conversation_id", convId);
      form.append("text", sentText);
      if (sentImage) form.append("image", sentImage);
      if (sentAttached) form.append("file", sentAttached);
      form.append("provider", provider);
      const { data } = await api.post("/chat/send", form, {
        headers: { "Content-Type": "multipart/form-data" },
        signal: abortControllerRef.current.signal,
      });
      setMessages((prev) => {
        const without = prev.filter((m) => m.id !== optimisticUser.id);
        return [...without, data.user_message, data.ai_message];
      });
      // refresh conv list for updated title/order
      fetchConversations();
    } catch (err) {
      const aborted =
        err.code === "ERR_CANCELED" ||
        err.name === "CanceledError" ||
        err.message === "canceled";
      if (!aborted) {
        setError(formatApiError(err));
      }
      setMessages((prev) => prev.filter((m) => m.id !== optimisticUser.id));
    } finally {
      setSending(false);
      abortControllerRef.current = null;
      textareaRef.current?.focus();
    }
  };

  const stopGeneration = () => {
    abortControllerRef.current?.abort();
  };

  const handleProviderChange = (e) => {
    const value = e.target.value;
    setProvider(value);
    localStorage.setItem("forge_provider", value);
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const activeConv = Array.isArray(conversations) ? conversations.find((c) => c.id === activeId) : null;
  const lastMsg = messages[messages.length - 1];
  const lastAssistantId = lastMsg && lastMsg.role === "assistant" ? lastMsg.id : null;

  return (
    <div className="h-screen w-full flex bg-[#050505] text-white overflow-hidden">
      {/* Sidebar */}
      <aside
        className={`${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        } md:translate-x-0 fixed md:relative z-30 md:z-auto top-0 left-0 h-full w-72 bg-[#0d0d0d] border-r-2 border-white/20 flex flex-col transition-transform`}
        data-testid="chat-sidebar"
      >
        <div className="p-5 border-b-2 border-white/10 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Flame className="w-6 h-6 text-[#ff2a6d]" />
            <div>
              <div className="font-heading font-black text-sm tracking-tight">
                THE FORGE
              </div>
              <div className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-mono">
                claude
              </div>
            </div>
          </div>
          <button
            className="md:hidden btn-ghost"
            onClick={() => setSidebarOpen(false)}
            data-testid="close-sidebar-btn"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4">
          <button
            onClick={newConversation}
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
              onClick={() => {
                setActiveId(c.id);
                setSidebarOpen(false);
              }}
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
                  onSubmit={(e) => submitRename(c.id, e)}
                  onClick={(e) => e.stopPropagation()}
                  className="flex-1 flex items-center gap-1"
                >
                  <input
                    autoFocus
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Escape") cancelRename();
                    }}
                    onBlur={() => submitRename(c.id)}
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
                    onClick={(e) => startRename(c, e)}
                    className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-[#ffd700] transition-opacity"
                    title="Rename"
                    data-testid={`rename-conv-${c.id}`}
                  >
                    <Pencil className="w-4 h-4" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteConversation(c.id);
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
              onClick={logout}
              className="btn-ghost border-2 border-white/20 hover:border-[#ff2a6d] hover:text-[#ff2a6d]"
              title="Logout"
              data-testid="logout-btn"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="border-b-2 border-white/10 px-4 md:px-8 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <button
              className="md:hidden btn-ghost"
              onClick={() => setSidebarOpen(true)}
              data-testid="open-sidebar-btn"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-[0.3em] text-[#ffd700] font-bold">
                // active session
              </div>
              <div className="font-heading font-black truncate text-lg">
                {activeConv?.title || "No conversation selected"}
              </div>
            </div>
          </div>
          <div className="text-xs font-mono text-gray-500 hidden md:block">
            model: <span className="text-[#05d9e8]">claude-sonnet-5</span>
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 md:px-8 py-6">
          <div className="max-w-4xl mx-auto" data-testid="messages-container">
            {!activeId && (
              <EmptyState onStart={newConversation} />
            )}
            {activeId && messages.length === 0 && !loadingMsgs && (
              <EmptyChat />
            )}
            {messages.map((m) => (
              <ChatMessage
                key={m.id}
                message={m}
                isLast={m.id === lastAssistantId}
                onRegenerate={regenerate}
                onFeedback={submitFeedback}
                regenerating={regenerating}
              />
            ))}
            {sending && (
              <div className="flex gap-4 mb-6">
                <div className="w-10 h-10 border-2 border-white/30 bg-[#0a0a0a] flex items-center justify-center flex-shrink-0">
                  <Flame className="w-5 h-5 text-[#ff2a6d] pulse-glow" />
                </div>
                <div className="border-2 border-white/20 p-4 shadow-[4px_4px_0_0_rgba(5,217,232,0.4)]">
                  <div className="typing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="px-4 md:px-8 pb-2">
            <div
              className="max-w-4xl mx-auto border-2 border-[#ff2a6d] bg-[#ff2a6d]/10 text-[#ff2a6d] p-3 text-sm font-mono flex items-center justify-between"
              data-testid="chat-error"
            >
              <span>{error}</span>
              <button onClick={() => setError("")} className="ml-2">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* Input dock */}
        <div className="px-4 md:px-8 pb-6 pt-2">
          <div className="max-w-4xl mx-auto">
            {attachedFile && (
              <div className="mb-3 inline-flex items-center gap-3 border-2 border-[#05d9e8] p-2 bg-black/40">
                <div className="text-xs font-mono text-[#05d9e8] flex items-center gap-2">
                  <span>[Piece jointe] {attachedFile.name}</span>
                  <span className="text-gray-400 text-[10px]">({(attachedFile.size / 1024).toFixed(1)} Ko)</span>
                </div>
                <button
                  type="button"
                  onClick={clearImage}
                  className="btn-ghost text-gray-400 hover:text-[#ff2a6d]"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}
            {imagePreview && (
              <div className="mb-3 inline-flex items-center gap-3 border-2 border-[#ffd700] p-2 bg-black/40">
                <img
                  src={imagePreview}
                  alt="preview"
                  className="w-16 h-16 object-cover"
                />
                <div className="text-xs font-mono text-[#ffd700]">
                  {imageFile?.name}
                </div>
                <button
                  onClick={clearImage}
                  className="btn-ghost text-gray-400 hover:text-[#ff2a6d]"
                  data-testid="clear-image-btn"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}
            <form
              onSubmit={sendMessage}
              className="border-2 border-white/20 bg-[#0a0a0a]/90 backdrop-blur-xl shadow-[8px_8px_0_0_#ff2a6d] flex items-end gap-2 p-3"
              data-testid="chat-input-form"
            >
              <input
                type="file"
                accept="image/*,.txt,.py,.js,.jsx,.ts,.tsx,.json,.md,.csv,.html,.css,.yaml,.yml,.sh,text/*"
                ref={fileInputRef}
                onChange={onPickFile}
                className="hidden"
                data-testid="attach-file-input"
              />
              <div
                className="flex items-center gap-1 flex-shrink-0 border-2 border-white/20 hover:border-[#05d9e8]/60 bg-black/40 px-2 py-1"
                title="Choisir le modèle IA"
              >
                <Cpu className="w-4 h-4 text-gray-500" />
                <select
                  value={provider}
                  onChange={handleProviderChange}
                  className="bg-transparent text-[11px] uppercase tracking-wider font-mono text-gray-300 outline-none cursor-pointer"
                  data-testid="provider-select"
                >
                  <option value="claude" className="bg-[#0a0a0a] text-white">
                    Claude
                  </option>
                  <option value="gemini" className="bg-[#0a0a0a] text-white">
                    Gemini
                  </option>
                </select>
              </div>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="btn-ghost border-2 border-white/20 hover:border-[#ffd700] hover:text-[#ffd700] flex-shrink-0"
                title="Attach image or text/code file"
                data-testid="attach-image-btn"
              >
                <Paperclip className="w-5 h-5" />
              </button>
              <textarea
                ref={textareaRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={onKeyDown}
                rows={1}
                placeholder="Forge a message... (Enter to send, Shift+Enter for new line)"
                className="flex-1 bg-transparent border-none outline-none text-white placeholder:text-gray-600 resize-none max-h-40 py-2"
                style={{ minHeight: "2.5rem" }}
                data-testid="chat-text-input"
              />
              {sending ? (
                <button
                  type="button"
                  onClick={stopGeneration}
                  className="flex-shrink-0 flex items-center gap-2 border-2 border-[#ff2a6d] bg-[#ff2a6d]/20 hover:bg-[#ff2a6d]/40 text-[#ff2a6d] px-3 py-2 font-mono uppercase text-xs tracking-wider transition-colors"
                  data-testid="stop-message-btn"
                  title="Stop generation"
                >
                  <Square className="w-4 h-4 fill-current" />
                  <span className="hidden sm:inline">Stop</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!text.trim() && !imageFile}
                  className="btn-primary flex-shrink-0 flex items-center gap-2"
                  data-testid="send-message-btn"
                >
                  <Send className="w-4 h-4" />
                  <span className="hidden sm:inline">Send</span>
                </button>
              )}
            </form>
            <div className="text-center text-[10px] uppercase tracking-[0.3em] text-gray-600 mt-3 font-mono">
              claude_unchained_zerodollar_forge // raw output, verify before trusting
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function EmptyState({ onStart }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-20">
      <Flame className="w-16 h-16 text-[#ff2a6d] mb-6" />
      <h2 className="font-heading text-3xl md:text-5xl font-black tracking-tighter mb-4">
        WELCOME TO <span className="text-[#ffd700]">THE FORGE</span>
      </h2>
      <p className="text-gray-400 max-w-md mb-8">
        Start a new conversation to unleash Claude.
      </p>
      <button
        onClick={onStart}
        className="btn-primary flex items-center gap-2"
        data-testid="empty-state-new-chat-btn"
      >
        <Plus className="w-4 h-4" /> Start Chat
      </button>
    </div>
  );
}

function EmptyChat() {
  const suggestions = [
    "Write me a function in Rust that...",
    "Explain quantum entanglement in 3 lines",
    "Roast my CV (paste below)",
    "Generate a startup name and pitch",
  ];
  return (
    <div className="py-12">
      <div className="text-center mb-10">
        <div className="text-xs uppercase tracking-[0.3em] text-[#ffd700] font-bold mb-2">
          // session initialized
        </div>
        <h2 className="font-heading text-3xl md:text-4xl font-black tracking-tighter">
          WHAT DO WE <span className="text-[#ff2a6d]">FORGE</span> TODAY?
        </h2>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-2xl mx-auto">
        {suggestions.map((s, i) => (
          <div
            key={i}
            className="border-2 border-white/15 p-4 text-sm text-gray-300 hover:border-[#ffd700] hover:text-white hover:shadow-[4px_4px_0_0_#05d9e8] transition-all cursor-default"
            data-testid={`suggestion-${i}`}
          >
            {s}
          </div>
        ))}
      </div>
    </div>
  );
}
