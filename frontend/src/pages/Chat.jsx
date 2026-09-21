/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 *
 * Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
 * modifier selon les termes de la GNU General Public License telle que publiée
 * par la Free Software Foundation, soit la version 3, soit (à votre choix)
 * toute version ultérieure. Il est distribué SANS AUCUNE GARANTIE.
 * Voir le fichier LICENSE ou <https://www.gnu.org/licenses/>.
 */

import React, { useEffect, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError, postSSE } from "@/lib/api";
import ChatMessage from "@/components/ChatMessage";
import {
  Plus,
  Send,
  Paperclip,
  FileText,
  Square,
  Trash2,
  LogOut,
  X,
  MessageSquare,
  Menu,
  Pencil,
  Check,
  Cpu,
  Gauge,
  Star,
  Mic,
  MicOff,
  Loader2,
} from "lucide-react";

const MAX_ATTACHMENTS = 10;
const MAX_TOTAL_BYTES = 16 * 1024 * 1024;
const MAX_FAVORITES = 6;

export default function Chat() {
  const { user, logout } = useAuth();
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState([]); // [{file, preview}]
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
  const [models, setModels] = useState([]);
  const [autoChain, setAutoChain] = useState([]);
  const [modelOverride, setModelOverride] = useState(
    () => localStorage.getItem("forge_model_override") || ""
  );
  const [favorites, setFavorites] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("forge_favorites") || "[]");
    } catch (_) {
      return [];
    }
  });
  const [streamText, setStreamText] = useState("");
  const [streamTools, setStreamTools] = useState([]);
  const [streamInfo, setStreamInfo] = useState(null);
  const [usage, setUsage] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const fileInputRef = useRef(null);
  const abortRef = useRef(null);
  const recognitionRef = useRef(null);
  const recorderRef = useRef(null);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  // Initial load - fetch conversations
  useEffect(() => {
    if (user && user !== false && user !== null) {
      fetchConversations();
      api
        .get("/models")
        .then(({ data }) => {
          setModels(data.providers || []);
          setAutoChain(data.auto?.chain || []);
        })
        .catch(() => {});
      refreshUsage();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  const refreshUsage = () => {
    api
      .get("/opencode/usage")
      .then(({ data }) => setUsage(data?.available ? data.usage : null))
      .catch(() => setUsage(null));
  };

  // Glisser-déposer d'un fichier n'importe où sur la fenêtre.
  useEffect(() => {
    let depth = 0;
    const hasFiles = (e) =>
      Array.from(e.dataTransfer?.types || []).includes("Files");
    const onEnter = (e) => {
      if (!hasFiles(e)) return;
      depth += 1;
      setDragging(true);
    };
    const onOver = (e) => {
      if (hasFiles(e)) e.preventDefault();
    };
    const onLeave = () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const onDrop = (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      acceptFiles(e.dataTransfer.files);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        provider,
        model: modelOverride || null,
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

  const acceptFiles = (list) => {
    const incoming = Array.from(list || []);
    if (!incoming.length) return;
    setAttachments((prev) => {
      const room = MAX_ATTACHMENTS - prev.length;
      if (room <= 0) {
        setError(`Maximum ${MAX_ATTACHMENTS} fichiers par message.`);
        return prev;
      }
      const kept = [];
      let total = prev.reduce((sum, a) => sum + a.file.size, 0);
      for (const f of incoming.slice(0, room)) {
        if (total + f.size > MAX_TOTAL_BYTES) {
          setError("Pièces jointes trop lourdes au total (max 16 Mo).");
          break;
        }
        total += f.size;
        kept.push({ file: f, preview: null, id: `${f.name}-${f.size}-${Math.random()}` });
      }
      if (incoming.length > room) {
        setError(`Maximum ${MAX_ATTACHMENTS} fichiers par message.`);
      }
      // Vignettes pour les images, en tâche de fond.
      kept.forEach((att) => {
        if (!att.file.type.startsWith("image/")) return;
        const reader = new FileReader();
        reader.onload = (ev) =>
          setAttachments((cur) =>
            cur.map((a) => (a.id === att.id ? { ...a, preview: ev.target.result } : a))
          );
        reader.readAsDataURL(att.file);
      });
      return [...prev, ...kept];
    });
  };

  const onPickFile = (e) => {
    acceptFiles(e.target.files);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeAttachment = (id) =>
    setAttachments((prev) => prev.filter((a) => a.id !== id));

  const clearAttachments = () => {
    setAttachments([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const abortRequest = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  const sendMessage = async (e) => {
    e?.preventDefault();
    if (sending) return;
    const trimmed = text.trim();
    if (!trimmed && !attachments.length) return;
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
      content:
        trimmed ||
        (attachments.length > 1
          ? `(${attachments.length} fichiers : ${attachments
              .map((a) => a.file.name)
              .join(", ")})`
          : `(fichier : ${attachments[0]?.file.name})`),
      has_image: attachments.some((a) => a.file.type.startsWith("image/")),
      attachments: attachments.map((a) => ({ name: a.file.name })),
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimisticUser]);
    const sentText = trimmed;
    const sentFiles = attachments.map((a) => a.file);
    setText("");
    clearAttachments();
    setSending(true);

    const controller = new AbortController();
    abortRef.current = controller;
    setStreamText("");
    setStreamTools([]);
    setStreamInfo(null);
    try {
      const form = new FormData();
      form.append("conversation_id", convId);
      form.append("text", sentText);
      sentFiles.forEach((f) => form.append("files", f));
      form.append("provider", provider);
      if (modelOverride) form.append("model", modelOverride);

      let acc = "";
      await postSSE("/chat/stream", form, {
        signal: controller.signal,
        onEvent: ({ event, data }) => {
          if (event === "user_message") {
            setMessages((prev) =>
              prev.map((m) => (m.id === optimisticUser.id ? data : m))
            );
            optimisticUser.id = data.id;
          } else if (event === "delta") {
            acc += data.text;
            setStreamText(acc);
          } else if (event === "start") {
            setStreamInfo({ provider: data.provider, model: data.model });
          } else if (event === "tool") {
            setStreamTools((prev) => [...prev, data]);
          } else if (event === "error") {
            setError(data.detail);
          } else if (event === "done") {
            setMessages((prev) => [...prev, data]);
            setStreamText("");
            setStreamTools([]);
            setStreamInfo(null);
          }
        },
      });
      fetchConversations();
      refreshUsage();
    } catch (err) {
      const aborted = err?.name === "AbortError";
      if (aborted) {
        setError("Génération arrêtée.");
        // Le serveur enregistre le texte déjà produit en tâche de fond.
        await new Promise((r) => setTimeout(r, 700));
      } else {
        setError(err?.message || "Erreur inconnue");
        setMessages((prev) => prev.filter((m) => m.id !== optimisticUser.id));
      }
      // Le serveur conserve ce qui a déjà été généré : on resynchronise.
      loadMessages(convId);
      fetchConversations();
    } finally {
      abortRef.current = null;
      setStreamText("");
      setStreamTools([]);
      setStreamInfo(null);
      setSending(false);
      textareaRef.current?.focus();
    }
  };

  const appendDictation = (chunk) => {
    const clean = (chunk || "").trim();
    if (!clean) return;
    setText((prev) => (prev ? `${prev.replace(/\s+$/, "")} ${clean}` : clean));
  };

  // 1er choix : dictée natively du navigateur (gratuite, instantanée).
  const startNativeDictation = () => {
    const Ctor =
      window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Ctor) return false;
    const rec = new Ctor();
    rec.lang = navigator.language || "fr-FR";
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (e) => {
      for (let i = e.resultIndex; i < e.results.length; i += 1) {
        if (e.results[i].isFinal) appendDictation(e.results[i][0].transcript);
      }
    };
    rec.onerror = (e) => {
      if (e.error !== "aborted") setError(`Dictée : ${e.error}`);
      setListening(false);
    };
    rec.onend = () => setListening(false);
    recognitionRef.current = rec;
    rec.start();
    setListening(true);
    return true;
  };

  // Repli : on enregistre l'audio et le backend le transcrit (Whisper).
  const startRecordingFallback = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setListening(false);
        if (!chunks.length) return;
        setTranscribing(true);
        try {
          const form = new FormData();
          form.append("audio", new Blob(chunks, { type: "audio/webm" }), "dictee.webm");
          const { data } = await api.post("/stt", form, {
            headers: { "Content-Type": "multipart/form-data" },
          });
          appendDictation(data.text);
        } catch (err) {
          setError(formatApiError(err));
        } finally {
          setTranscribing(false);
        }
      };
      recorderRef.current = rec;
      rec.start();
      setListening(true);
    } catch (_) {
      setError("Micro inaccessible : autorise l'accès au microphone.");
    }
  };

  const toggleDictation = () => {
    if (listening) {
      recognitionRef.current?.stop();
      recorderRef.current?.stop();
      recognitionRef.current = null;
      setListening(false);
      return;
    }
    if (!startNativeDictation()) startRecordingFallback();
  };

  const currentModel = () =>
    modelOverride || models.find((m) => m.id === provider)?.model || "";

  const isFavorite = favorites.some(
    (f) => f.provider === provider && f.model === (modelOverride || "")
  );

  const persistFavorites = (list) => {
    setFavorites(list);
    localStorage.setItem("forge_favorites", JSON.stringify(list));
  };

  const toggleFavorite = () => {
    const entry = {
      provider,
      model: modelOverride || "",
      label: currentModel() || provider,
    };
    const without = favorites.filter(
      (f) => !(f.provider === entry.provider && f.model === entry.model)
    );
    if (without.length !== favorites.length) {
      persistFavorites(without);
      return;
    }
    if (favorites.length >= MAX_FAVORITES) {
      setError(`Maximum ${MAX_FAVORITES} favoris. Retires-en un d'abord.`);
      return;
    }
    persistFavorites([...favorites, entry]);
  };

  const applyFavorite = (fav) => {
    setProvider(fav.provider);
    localStorage.setItem("forge_provider", fav.provider);
    setModelOverride(fav.model);
    if (fav.model) localStorage.setItem("forge_model_override", fav.model);
    else localStorage.removeItem("forge_model_override");
  };

  const handleProviderChange = (e) => {
    const value = e.target.value;
    setProvider(value);
    localStorage.setItem("forge_provider", value);
    // Un modèle choisi pour un provider n'a aucun sens pour un autre.
    setModelOverride("");
    localStorage.removeItem("forge_model_override");
  };

  const handleModelChange = (e) => {
    const value = e.target.value;
    setModelOverride(value);
    if (value) localStorage.setItem("forge_model_override", value);
    else localStorage.removeItem("forge_model_override");
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
  const activeModel = models.find((m) => m.id === provider);
  const catalog = activeModel?.models || [];
  return (
    <div className="h-full w-full flex bg-[#050505] text-white overflow-hidden">
      {/* Sidebar */}
      <aside
        className={`${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        } lg:translate-x-0 fixed lg:relative z-30 lg:z-auto top-0 left-0 h-full w-72 max-w-[85vw] flex-shrink-0 bg-[#0d0d0d] border-r-2 border-white/20 flex flex-col transition-transform`}
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
          <button
            className="lg:hidden btn-ghost"
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
              href="https://www.gnu.org/licenses/gpl-3.0.html#howto"
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

      {/* Superposition glisser-déposer */}
      {dragging && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm pointer-events-none"
          data-testid="drop-overlay"
        >
          <div className="border-4 border-dashed border-[#ffd700] px-8 py-10 text-center">
            <Paperclip className="w-10 h-10 text-[#ffd700] mx-auto mb-4" />
            <div className="font-heading font-black text-xl sm:text-2xl tracking-tighter">
              LÂCHE TON FICHIER
            </div>
            <div className="text-gray-400 text-sm mt-2 font-mono">
              image, PDF, texte ou code — 16 Mo max
            </div>
          </div>
        </div>
      )}

      {/* Fond cliquable quand le tiroir est ouvert sur mobile/tablette */}
      {sidebarOpen && (
        <div
          className="lg:hidden fixed inset-0 z-20 bg-black/70"
          onClick={() => setSidebarOpen(false)}
          data-testid="sidebar-backdrop"
        />
      )}

      {/* Main */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <header className="border-b-2 border-white/10 px-3 sm:px-4 lg:px-8 py-3 sm:py-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            <button
              className="lg:hidden btn-ghost flex-shrink-0"
              onClick={() => setSidebarOpen(true)}
              data-testid="open-sidebar-btn"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-[0.3em] text-[#ffd700] font-bold">
                // active session
              </div>
              <div className="font-heading font-black truncate text-base sm:text-lg">
                {activeConv?.title || "No conversation selected"}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3 flex-shrink-0 min-w-0">
            {usage && <UsageBadge usage={usage} />}
            <div
              className="text-[10px] sm:text-xs font-mono text-gray-500 hidden sm:block truncate max-w-[220px] lg:max-w-[420px] text-right"
              data-testid="active-model-label"
            >
            {provider === "auto" ? (
              <>
                AUTO:{" "}
                <span className="text-[#05d9e8]">
                  {autoChain.length
                    ? models.find((m) => m.id === autoChain[0])?.model ||
                      autoChain[0]
                    : "aucun provider configuré"}
                </span>
                {autoChain.length > 1 && (
                  <span className="text-gray-600">
                    {" "}
                    → {autoChain.slice(1).join(" → ")}
                  </span>
                )}
              </>
            ) : (
              <>
                {activeModel?.label || provider}:{" "}
                <span className="text-[#05d9e8]">
                  {modelOverride || activeModel?.model || "…"}
                </span>
                {modelOverride && (
                  <span className="text-[#ffd700]"> (choisi)</span>
                )}
                {activeModel && activeModel.available === false && (
                  <span className="text-[#ff2a6d]"> (non configuré)</span>
                )}
              </>
            )}
            </div>
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-3 sm:px-4 lg:px-8 py-4 sm:py-6">
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
              <>
                {(streamText || streamTools.length > 0) && (
                  <ChatMessage
                    key="streaming"
                    message={{
                      id: "streaming",
                      role: "assistant",
                      content: streamText,
                      provider: streamInfo?.provider || provider,
                      model:
                        streamInfo?.model || modelOverride || activeModel?.model,
                      tool_steps: streamTools,
                      streaming: true,
                    }}
                  />
                )}
                {!streamText && (
                  <div className="flex gap-4 mb-6">
                    <div className="w-10 h-10 border-2 border-white/30 bg-[#0a0a0a] flex items-center justify-center flex-shrink-0">
                      <img src="/logo-64.png" alt="" className="w-7 h-7 pulse-glow" />
                    </div>
                    <div className="border-2 border-white/20 p-4 shadow-[4px_4px_0_0_rgba(5,217,232,0.4)] flex items-center gap-4">
                      <div className="typing-dots">
                        <span></span>
                        <span></span>
                        <span></span>
                      </div>
                      <button
                        type="button"
                        onClick={abortRequest}
                        className="text-[11px] font-mono uppercase tracking-wider text-gray-500 hover:text-[#ff2a6d] border border-white/20 hover:border-[#ff2a6d] px-2 py-1 transition-colors"
                        data-testid="stop-generation-btn"
                      >
                        annuler
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="px-3 sm:px-4 lg:px-8 pb-2 flex-shrink-0">
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
        <div className="px-3 sm:px-4 lg:px-8 pt-2 safe-bottom flex-shrink-0">
          <div className="max-w-4xl mx-auto">
            {favorites.length > 0 && (
              <div
                className="mb-2 flex gap-1.5 overflow-x-auto pb-1"
                data-testid="favorites-bar"
              >
                <span className="text-[10px] font-mono uppercase tracking-wider text-gray-600 flex items-center flex-shrink-0 pr-1">
                  favoris
                </span>
                {favorites.map((fav) => {
                  const active =
                    fav.provider === provider && fav.model === (modelOverride || "");
                  return (
                    <button
                      key={`${fav.provider}:${fav.model}`}
                      type="button"
                      onClick={() => applyFavorite(fav)}
                      className={`flex-shrink-0 text-[11px] font-mono px-2 py-1 border-2 transition-colors ${
                        active
                          ? "border-[#ffd700] text-[#ffd700] bg-[#ffd700]/10"
                          : "border-white/20 text-gray-400 hover:border-[#ffd700]/60 hover:text-[#ffd700]"
                      }`}
                      title={`${fav.provider} · ${fav.label}`}
                      data-testid={`favorite-chip-${fav.provider}-${fav.model || "default"}`}
                    >
                      {fav.label}
                    </button>
                  );
                })}
              </div>
            )}
            {attachments.length > 0 && (
              <div
                className="mb-3 flex gap-2 overflow-x-auto pb-1"
                data-testid="attachment-list"
              >
                {attachments.map((att) => (
                  <div
                    key={att.id}
                    className="flex items-center gap-2 border-2 border-[#ffd700] p-1.5 bg-black/40 flex-shrink-0"
                  >
                    {att.preview ? (
                      <img
                        src={att.preview}
                        alt=""
                        className="w-10 h-10 object-cover"
                      />
                    ) : (
                      <div className="w-10 h-10 flex items-center justify-center bg-[#ffd700]/10 border border-[#ffd700]/40">
                        <FileText className="w-5 h-5 text-[#ffd700]" />
                      </div>
                    )}
                    <div className="text-[11px] font-mono text-[#ffd700] max-w-[150px]">
                      <div className="truncate" data-testid="attachment-name">
                        {att.file.name}
                      </div>
                      <div className="text-gray-500">
                        {att.file.size < 1024
                          ? `${att.file.size} o`
                          : `${(att.file.size / 1024).toFixed(1)} Ko`}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeAttachment(att.id)}
                      className="btn-ghost text-gray-400 hover:text-[#ff2a6d] p-1"
                      data-testid="clear-image-btn"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
                {attachments.length > 1 && (
                  <button
                    type="button"
                    onClick={clearAttachments}
                    className="flex-shrink-0 text-[10px] font-mono uppercase tracking-wider text-gray-500 hover:text-[#ff2a6d] border-2 border-white/20 hover:border-[#ff2a6d] px-2"
                    data-testid="clear-all-attachments-btn"
                  >
                    tout retirer
                  </button>
                )}
              </div>
            )}
            <form
              onSubmit={sendMessage}
              className="border-2 border-white/20 bg-[#0a0a0a]/90 backdrop-blur-xl shadow-[4px_4px_0_0_#ff2a6d] sm:shadow-[8px_8px_0_0_#ff2a6d] flex flex-col sm:flex-row sm:items-end gap-2 p-2 sm:p-3"
              data-testid="chat-input-form"
            >
              <input
                type="file"
                multiple
                ref={fileInputRef}
                onChange={onPickFile}
                className="hidden"
                data-testid="image-file-input"
              />
              {/* Ligne 1 sur mobile : contrôles. Sur >=sm, `contents` fait
                  disparaître ce conteneur pour garder une seule rangée. */}
              <div className="flex items-center gap-2 min-w-0 sm:contents">
                <div
                  className="flex items-center gap-1 flex-1 sm:flex-none min-w-0 border-2 border-white/20 hover:border-[#05d9e8]/60 bg-black/40 px-2 py-1"
                  title="Choisir le modèle IA"
                >
                  <Cpu className="w-4 h-4 text-gray-500 flex-shrink-0" />
                  <select
                    value={provider}
                    onChange={handleProviderChange}
                    className="flex-1 min-w-0 bg-transparent text-[11px] uppercase tracking-wider font-mono text-gray-300 outline-none cursor-pointer"
                    data-testid="provider-select"
                  >
                    <option value="auto" className="bg-[#0a0a0a] text-white">
                      Auto (meilleur dispo)
                    </option>
                    {models.map((m) => (
                      <option
                        key={m.id}
                        value={m.id}
                        className="bg-[#0a0a0a] text-white"
                      >
                        {m.label}
                        {m.available === false ? " (non configuré)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
                {catalog.length > 0 && (
                  <div
                    className="flex items-center gap-1 flex-1 sm:flex-none min-w-0 border-2 border-white/20 hover:border-[#ffd700]/60 bg-black/40 px-2 py-1"
                    title="Choisir un modèle précis chez ce provider"
                  >
                    <select
                      value={modelOverride}
                      onChange={handleModelChange}
                      className="flex-1 min-w-0 sm:max-w-[150px] bg-transparent text-[11px] font-mono text-gray-300 outline-none cursor-pointer"
                      data-testid="model-select"
                    >
                      <option value="" className="bg-[#0a0a0a] text-white">
                        défaut ({activeModel?.model})
                      </option>
                      {catalog.map((m) => (
                        <option key={m} value={m} className="bg-[#0a0a0a] text-white">
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <button
                  type="button"
                  onClick={toggleFavorite}
                  className={`btn-ghost border-2 flex-shrink-0 ${
                    isFavorite
                      ? "border-[#ffd700] text-[#ffd700]"
                      : "border-white/20 hover:border-[#ffd700] hover:text-[#ffd700]"
                  }`}
                  title={
                    isFavorite
                      ? "Retirer des favoris"
                      : "Épingler ce modèle dans les favoris"
                  }
                  data-testid="toggle-favorite-btn"
                >
                  <Star
                    className="w-5 h-5"
                    fill={isFavorite ? "currentColor" : "none"}
                  />
                </button>
                <button
                  type="button"
                  onClick={toggleDictation}
                  disabled={transcribing}
                  className={`btn-ghost border-2 flex-shrink-0 ${
                    listening
                      ? "border-[#ff2a6d] text-[#ff2a6d] animate-pulse"
                      : "border-white/20 hover:border-[#05d9e8] hover:text-[#05d9e8]"
                  }`}
                  title={
                    listening
                      ? "Arrêter la dictée"
                      : "Dicter le message à la voix"
                  }
                  data-testid="dictate-btn"
                >
                  {transcribing ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : listening ? (
                    <MicOff className="w-5 h-5" />
                  ) : (
                    <Mic className="w-5 h-5" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="btn-ghost border-2 border-white/20 hover:border-[#ffd700] hover:text-[#ffd700] flex-shrink-0"
                  title="Joindre un fichier (image, PDF, texte, code...)"
                  data-testid="attach-image-btn"
                >
                  <Paperclip className="w-5 h-5" />
                </button>
              </div>
              {/* Ligne 2 sur mobile : saisie + envoi. */}
              <div className="flex items-end gap-2 min-w-0 sm:contents">
                <textarea
                  ref={textareaRef}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={onKeyDown}
                  rows={1}
                  placeholder="Forge a message..."
                  className="flex-1 min-w-0 bg-transparent border-none outline-none text-white text-base placeholder:text-gray-600 resize-none max-h-40 py-2"
                  style={{ minHeight: "2.5rem" }}
                  data-testid="chat-text-input"
                />
                {sending ? (
                  <button
                    type="button"
                    onClick={abortRequest}
                    className="flex-shrink-0 flex items-center gap-2 px-3 sm:px-4 py-2 border-2 border-black bg-[#ff2a6d] text-white font-heading font-black uppercase tracking-wider shadow-[4px_4px_0_0_#000] hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-[2px_2px_0_0_#000] transition-all"
                    title="Arrêter la génération"
                    data-testid="stop-message-btn"
                  >
                    <Square className="w-4 h-4 fill-current" />
                    <span className="hidden sm:inline">Stop</span>
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!text.trim() && !attachments.length}
                    className="btn-primary flex-shrink-0 flex items-center gap-2"
                    data-testid="send-message-btn"
                  >
                    <Send className="w-4 h-4" />
                    <span className="hidden sm:inline">Send</span>
                  </button>
                )}
              </div>
            </form>
            <div className="hidden sm:block text-center text-[10px] uppercase tracking-[0.3em] text-gray-600 mt-3 font-mono">
              claude_unchained_zerodollar_forge // raw output, verify before trusting
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function UsageBadge({ usage }) {
  const windows = [
    { key: "rolling", label: "5H" },
    { key: "weekly", label: "SEM" },
    { key: "monthly", label: "MOIS" },
  ].filter((w) => usage?.[w.key]);
  if (!windows.length) return null;

  const color = (p) =>
    p >= 90 ? "#ff2a6d" : p >= 70 ? "#ffd700" : "#05d9e8";

  return (
    <div
      className="hidden md:flex items-center gap-2 border-2 border-white/20 bg-black/40 px-2 py-1"
      title={
        "Forfait OpenCode Go — " +
        windows
          .map(
            (w) =>
              `${w.label} : ${Math.round(usage[w.key].percent)}% utilisé (reset ${new Date(
                usage[w.key].resetsAt
              ).toLocaleString("fr-FR")})`
          )
          .join(" · ")
      }
      data-testid="usage-badge"
    >
      <Gauge className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
      {windows.map((w) => {
        const pct = Math.min(100, Math.max(0, usage[w.key].percent || 0));
        return (
          <div key={w.key} className="flex items-center gap-1">
            <span className="text-[9px] font-mono text-gray-500">{w.label}</span>
            <div className="w-10 h-1.5 bg-white/10">
              <div
                className="h-full transition-all"
                style={{ width: `${pct}%`, backgroundColor: color(pct) }}
              />
            </div>
            <span
              className="text-[9px] font-mono"
              style={{ color: color(pct) }}
              data-testid={`usage-${w.key}`}
            >
              {Math.round(pct)}%
            </span>
          </div>
        );
      })}
    </div>
  );
}

function EmptyState({ onStart }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-20">
      <img src="/icon-192.png" alt="" className="w-24 h-24 mb-6 border-2 border-white/20" />
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
