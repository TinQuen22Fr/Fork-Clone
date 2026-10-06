/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 *
 * Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
 * modifier selon les termes de la GNU General Public License telle que publiée
 * par la Free Software Foundation, soit la version 3, soit (à votre choix)
 * toute version ultérieure. Il est distribué SANS AUCUNE GARANTIE.
 * Voir le fichier LICENSE ou <https://www.gnu.org/licenses/>.
 */

import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import api, { formatApiError, postSSE } from "@/lib/api";
import ChatMessage from "@/components/ChatMessage";
import StreamingBubble from "@/components/StreamingBubble";
import {
  resetStream,
  setStreamText,
  setStreamInfo,
  addStreamTool,
  startStreamStep,
  finishStreamStep,
  getStreamHasContent,
  subscribe as subscribeStream,
} from "@/lib/streamStore";
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
  Github,
  GitFork,
  ExternalLink,
  ShieldCheck,
  Sun,
  Moon,
  FileDown,
  Brain,
} from "lucide-react";
import GithubSaveDialog from "@/components/GithubSaveDialog";
import VoicePicker from "@/components/VoicePicker";
import { useTheme } from "@/hooks/useTheme";
import ProjectHub from "@/components/ProjectHub";
import PreviewButton from "@/components/PreviewButton";
import ThemeToggle from "@/components/chat/ThemeToggle";
import UsageBadge from "@/components/chat/UsageBadge";
import EmptyChat from "@/components/chat/EmptyChat";
import MessagesSkeleton from "@/components/chat/MessagesSkeleton";
import Sidebar from "@/components/chat/Sidebar"
import ChatHeader from "@/components/chat/ChatHeader";

const MAX_ATTACHMENTS = 10;
const MAX_TOTAL_BYTES = 16 * 1024 * 1024;
const MAX_FAVORITES = 6;
const SECURITY_AUDIT_PROMPT = `Réalise un audit de sécurité défensif complet du code du workspace et des modifications récentes. Analyse les points suivants :
- Injection et validation des entrées (SQL, commandes, path traversal)
- Exposition de données sensibles (clés d'API, tokens, secrets hardcodés)
- Failles web courantes (XSS, CORS, gestion des sessions)
- Permissions et gestion des erreurs.

RÈGLE DE PERSISTANCE OBLIGATOIRE : le fichier AUDIT_SECURITE.md à la racine du projet est la base de référence exclusive de tout audit. Lis-le intégralement avant toute analyse. N'émets JAMAIS de nouvelle alerte sur un point déjà classé « ✅ Vulnérabilités résolues » ou « ⚪ Risque accepté par design (Machine dédiée / Mono-utilisateur) » dans ce fichier : ces points sont clos et ne doivent pas être ré-audités, sauf si tu détectes une régression technique explicite (le correctif a été supprimé, contourné ou cassé par une modification ultérieure du code). Concentre exclusivement ton analyse sur le code ajouté ou modifié depuis le 01/10/2026. Si une régression est détectée sur un point déjà clos, signale-la explicitement en la reliant à son identifiant d'origine (ex. « Régression sur C2 »). Mets à jour AUDIT_SECURITE.md en conséquence (nouvelle entrée ou réouverture argumentée d'un point, jamais une suppression silencieuse de l'historique).

Donne un rapport clair listant les risques identifiés par niveau de criticité et les correctifs concrets à appliquer.`;

export default function Chat() {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [conversations, setConversations] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState([]); // [{file, preview}]
  const [sending, setSending] = useState(false);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  // Pagination : le serveur ne renvoie que les MESSAGES_PAGE derniers messages ;
  // les plus anciens restent en base et se chargent a la demande.
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const olderLoadedRef = useRef(null); // id de la conv dont on a remonte l'historique
  const prevScrollHeightRef = useRef(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Repli de la barre laterale (desktop) - memorise entre les sessions
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem("forge_sidebar_collapsed") === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("forge_sidebar_collapsed", sidebarCollapsed ? "1" : "0");
    } catch {
      /* stockage indisponible */
    }
  }, [sidebarCollapsed]);
  // Largeur max du contenu : pleine largeur quand la barre est repliee
  const wrapW = sidebarCollapsed ? "max-w-none" : "max-w-3xl";
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
  const [reasoningEffort, setReasoningEffort] = useState(
    () => localStorage.getItem("forge_reasoning_effort") || "auto"
  );
  // Abonnement minimal : ne change qu'a la transition vide -> non vide.
  const streamingHasContent = useSyncExternalStore(
    subscribeStream,
    getStreamHasContent,
    getStreamHasContent
  );
  const [favorites, setFavorites] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("forge_favorites") || "[]");
    } catch (_) {
      return [];
    }
  });
  const [usage, setUsage] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [plusOpen, setPlusOpen] = useState(false);
  const [githubOpen, setGithubOpen] = useState(false);
  const [forking, setForking] = useState(false);
  const [projectUrls, setProjectUrls] = useState({});
  // Memoire de contexte du projet actif (Fetcher) — bandeau discret.
  const [projectContext, setProjectContext] = useState(null);
  const [contextOpen, setContextOpen] = useState(false);
  const fileInputRef = useRef(null);
  const abortRef = useRef(null);
  const recognitionRef = useRef(null);
  const recorderRef = useRef(null);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const messagesContainerRef = useRef(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [statusTurns, setStatusTurns] = useState([]);
  const [runningTurns, setRunningTurns] = useState([]);

  const activeConv = Array.isArray(conversations) ? conversations.find((c) => c.id === activeId) : null;

  // Initial load - fetch conversations
  useEffect(() => {
    if (user && user !== false && user !== null) {
      fetchConversations();
      fetchProjectUrls();
      api
        .get("/models")
        .then(({ data }) => {
          setModels(data.providers || []);
          setAutoChain(data.auto?.chain || []);
        })
        .catch(() => {});
      refreshUsage();
      fetchStatusTurns();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  const refreshUsage = () => {
    api
      .get("/opencode/usage")
      .then(({ data }) => setUsage(data?.available ? data.usage : null))
      .catch(() => setUsage(null));
  };

  // Cle localStorage pour ne JAMAIS reafficher en boucle un tour deja notifie
  // (fermeture de la banniere = memorisation definitive, meme apres reload).
  const SEEN_TURNS_KEY = "forge_seen_status_turns";
  const loadSeenTurns = () => {
    try {
      const raw = window.localStorage.getItem(SEEN_TURNS_KEY);
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch {
      return new Set();
    }
  };
  const saveSeenTurns = (set) => {
    try {
      // On borne la taille pour ne pas accumuler indefiniment.
      const arr = Array.from(set).slice(-200);
      window.localStorage.setItem(SEEN_TURNS_KEY, JSON.stringify(arr));
    } catch {
      // Stockage indisponible (navigation privee, quota) : on ignore.
    }
  };
  const lastStatusFetchRef = useRef(0);
  const STATUS_MIN_INTERVAL_MS = 15000; // cooldown strict anti-spam reseau

  const fetchStatusTurns = (force = false) => {
    const now = Date.now();
    if (!force && now - lastStatusFetchRef.current < STATUS_MIN_INTERVAL_MS) {
      return;
    }
    lastStatusFetchRef.current = now;
    api
      .get("/chat/status")
      .then(({ data }) => {
        const seen = loadSeenTurns();
        const turns = data?.turns || [];
        const running = data?.running || [];
        // On ne garde que les tours jamais notifies auparavant (par id de
        // conversation + horodatage de fin) pour eviter toute reapparition.
        const freshTurns = turns.filter((t) => {
          const key = `${t.conversation_id}:${t.created_at || ""}`;
          return !seen.has(key);
        });
        setStatusTurns(freshTurns);
        setRunningTurns(running);
      })
      .catch(() => {
        setStatusTurns([]);
        setRunningTurns([]);
      });
  };

  const dismissStatusBanner = () => {
    // Marque definitivement les tours affiches comme "deja vus" avant de
    // vider l'etat local, afin qu'un futur resync ne les re-propose jamais.
    const seen = loadSeenTurns();
    statusTurns.forEach((t) => {
      seen.add(`${t.conversation_id}:${t.created_at || ""}`);
    });
    saveSeenTurns(seen);
    setStatusTurns([]);
    setRunningTurns([]);
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
    olderLoadedRef.current = null;
    setHasMore(false);
    if (activeId) loadMessages(activeId);
    else setMessages([]);
  }, [activeId]);

  // Mode autonome : on resynchronise avec le serveur UNIQUEMENT lors d'un
  // vrai retour d'absence (onglet reellement cache puis revisible pendant au
  // moins ABSENCE_MIN_MS), jamais sur un simple clic/changement de focus
  // interne a la page. Un cooldown reseau strict (fetchStatusTurns) protege
  // en plus contre tout spam de requetes.
  const hiddenAtRef = useRef(null);
  const ABSENCE_MIN_MS = 20000; // il faut au moins 20s d'onglet cache pour compter comme une "absence"

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        hiddenAtRef.current = Date.now();
        return;
      }
      // Retour a "visible" : on ne resynchronise que si on etait reellement
      // parti assez longtemps (evite tout resync sur un simple changement
      // de focus de fenetre ou un clic furtif hors onglet).
      const hiddenAt = hiddenAtRef.current;
      hiddenAtRef.current = null;
      if (!hiddenAt) return;
      const absenceDuration = Date.now() - hiddenAt;
      if (absenceDuration < ABSENCE_MIN_MS) return;
      if (activeId) loadMessages(activeId);
      fetchStatusTurns();
      fetchConversations();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  // Tant qu'une generation tourne cote serveur, on re-sonde son etat toutes les
  // 8 secondes (au lieu de 3) pour basculer automatiquement vers le message
  // final a la fin, sans matraquer le reseau ni le navigateur.
  useEffect(() => {
    if (!runningTurns.length) return undefined;
    const iv = setInterval(() => {
      fetchStatusTurns();
      if (activeId) loadMessages(activeId);
    }, 8000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningTurns.length, activeId]);

  // Auto-scroll "intelligent" : on ne ramene en bas QUE si l'utilisateur y est
  // deja (ou vient d'envoyer un message) et qu'il n'est pas en train de
  // selectionner du texte. Sinon la vue lui echappe pendant qu'il copie.
  const nearBottomRef = useRef(true);
  const prevSendingRef = useRef(false);
  useEffect(() => {
    const justSent = sending && !prevSendingRef.current;
    prevSendingRef.current = sending;
    const sel = typeof window !== "undefined" ? window.getSelection?.() : null;
    const container = messagesContainerRef.current;
    const selecting =
      sel && !sel.isCollapsed && container && sel.anchorNode && container.contains(sel.anchorNode);
    if (selecting && !justSent) return;
    if (!justSent && !nearBottomRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: sending ? "auto" : "smooth" });
  }, [messages, sending]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    setShowScrollBottom(false);
  };

  const handleMessagesScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    nearBottomRef.current = distanceFromBottom <= 120;
    setShowScrollBottom(distanceFromBottom > 120);
  };

  useEffect(() => {
    if (activeId) {
      messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
      setShowScrollBottom(false);
    }
  }, [activeId]);

  // redirect if logged out
  if (user === false) return <Navigate to="/login" replace />;
  if (user === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg-main)]">
        <div className="typing-dots">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    );
  }

  const fetchProjectUrls = async () => {
    try {
      const { data } = await api.get("/workspace/projects");
      const map = {};
      (data.projects || []).forEach((p) => {
        if (p.preview_url) map[p.name] = p.preview_url;
      });
      setProjectUrls(map);
    } catch (_) {
      /* non bloquant */
    }
  };

  const fetchConversations = async () => {
    try {
      const { data } = await api.get("/conversations");
      const list = Array.isArray(data) ? data : (data?.conversations || []);
      setConversations(list);
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  // Reconciliateur : reconstruit la liste en conservant les REFERENCES des
  // messages inchanges, pour que React.memo(ChatMessage) court-circuite leur
  // re-rendu. Le backend renvoie des documents neufs a chaque poll (8s), donc
  // sans ceci chaque poll re-rendait toute la conversation (jusqu'a 2000 msg).
  const reconcileMessages = useCallback((prev, next) => {
    if (!Array.isArray(next)) return prev;
    if (!prev.length) return next;
    const prevById = new Map();
    for (const m of prev) prevById.set(m.id, m);
    let changed = prev.length !== next.length;
    const merged = next.map((m, i) => {
      const old = prevById.get(m.id);
      if (!old) {
        changed = true;
        return m;
      }
      // Champs dont un changement doit forcer le re-rendu du message.
      const same =
        old.content === m.content &&
        old.tool_steps === m.tool_steps &&
        old.steps === m.steps &&
        old.feedback === m.feedback &&
        old.role === m.role &&
        old.updated_at === m.updated_at;
      if (same) {
        if (prev[i] !== old) changed = true;
        return old; // reference conservee -> memo efficace
      }
      changed = true;
      return m;
    });
    // Aucun changement ET meme ordre -> on garde le tableau precedent (ref stable).
    if (!changed) return prev;
    return merged;
  }, []);

  const MESSAGES_PAGE = 150;

  const loadMessages = async (cid) => {
    setLoadingMsgs(true);
    try {
      const { data } = await api.get(`/conversations/${cid}/messages`, {
        params: { limit: MESSAGES_PAGE },
      });
      const list = Array.isArray(data) ? data : data?.messages || [];
      if (olderLoadedRef.current !== cid) {
        setHasMore(list.length >= MESSAGES_PAGE);
      }
      setMessages((prev) => {
        // On conserve les anciens messages deja remontes par l'utilisateur :
        // le poll ne ramene que la derniere page et ne doit pas les effacer.
        const firstTs = list[0]?.created_at;
        const older =
          firstTs && olderLoadedRef.current === cid
            ? prev.filter(
                (m) =>
                  m.conversation_id === cid &&
                  !String(m.id).startsWith("tmp-") &&
                  m.created_at < firstTs
              )
            : [];
        return reconcileMessages(prev, older.length ? [...older, ...list] : list);
      });
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoadingMsgs(false);
    }
  };

  const loadOlderMessages = async () => {
    if (!activeId || loadingOlder) return;
    const first = messages.find((m) => !String(m.id).startsWith("tmp-"));
    if (!first?.created_at) return;
    setLoadingOlder(true);
    try {
      const { data } = await api.get(`/conversations/${activeId}/messages`, {
        params: { limit: MESSAGES_PAGE, before: first.created_at },
      });
      const list = Array.isArray(data) ? data : data?.messages || [];
      setHasMore(list.length >= MESSAGES_PAGE);
      if (!list.length) return;
      olderLoadedRef.current = activeId;
      const el = messagesContainerRef.current;
      prevScrollHeightRef.current = el ? el.scrollHeight : null;
      setMessages((prev) => {
        const ids = new Set(prev.map((m) => m.id));
        return [...list.filter((m) => !ids.has(m.id)), ...prev];
      });
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoadingOlder(false);
    }
  };

  // Apres l'ajout d'anciens messages en haut, on garde la vue ou elle etait
  // (sinon le contenu "saute" vers le haut de la liste).
  useLayoutEffect(() => {
    const prevH = prevScrollHeightRef.current;
    if (prevH == null) return;
    prevScrollHeightRef.current = null;
    const el = messagesContainerRef.current;
    if (el) el.scrollTop += el.scrollHeight - prevH;
  }, [messages]);

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

  // Fetcher : récupère la mémoire de contexte du projet actif (snapshot + faits
  // mémorisés) dès qu'on ouvre une conversation rattachée à un projet.
  useEffect(() => {
    const pid = activeConv?.project;
    if (!pid) {
      setProjectContext(null);
      return;
    }
    let cancelled = false;
    api
      .get(`/context/${encodeURIComponent(pid)}`)
      .then(({ data }) => {
        if (!cancelled) setProjectContext(data);
      })
      .catch(() => {
        if (!cancelled) setProjectContext(null);
      });
    return () => {
      cancelled = true;
    };
  }, [activeConv?.project]);

  const startTask = async (prompt, project) => {
    try {
      const { data } = await api.post("/conversations", {
        title: prompt.slice(0, 60),
        project: project || null,
      });
      setConversations((prev) => [data, ...prev]);
      setActiveId(data.id);
      setMessages([]);
      await sendMessage(null, { text: prompt, convId: data.id });
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const openProject = async (project) => {
    if (project?.preview_url) {
      setProjectUrls((prev) => ({ ...prev, [project.name]: project.preview_url }));
    }
    // Fermeture immediate du hub : on ne bloque pas l'UI sur l'aller-retour
    // reseau de creation/reouverture de conversation.
    setSidebarOpen(false);

    // Chemin rapide : la conversation est deja connue en memoire (chargee au
    // montage via fetchConversations). Aucun appel reseau n'est necessaire,
    // le fil s'ouvre instantanement.
    const existing = conversations.find((c) => c.project === project.name);
    if (existing) {
      setMessages([]);
      setActiveId(existing.id);
      return;
    }

    // Sinon on cree directement la conversation attachee au projet. On evite
    // volontairement un GET /conversations bloquant au prealable : c'est cet
    // aller-retour qui differait l'affichage du fil. fetchConversations()
    // reste responsable de la reconciliation de la liste en arriere-plan.
    try {
      const { data } = await api.post("/conversations", {
        title: project.name,
        project: project.name,
      });
      setConversations((prev) => [data, ...prev]);
      setMessages([]);
      setActiveId(data.id);
      // Reconciliation de fond (non bloquante) pour recuperer une eventuelle
      // conversation existante creee dans une autre session.
      fetchConversations();
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const forkConversation = async () => {
    if (!activeId) return;
    setForking(true);
    setError("");
    try {
      const { data } = await api.post(`/conversations/${activeId}/fork`);
      setConversations((prev) => [data, ...prev]);
      setActiveId(data.id);
      setSidebarOpen(false);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setForking(false);
      setPlusOpen(false);
    }
  };

  const deleteMessage = async (messageId) => {
    if (!activeId) return;
    if (!window.confirm("Supprimer définitivement ce message ?")) return;
    try {
      await api.delete(`/conversations/${activeId}/messages/${messageId}`);
      setMessages((prev) => prev.filter((m) => m.id !== messageId));
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
        reasoning_effort:
          reasoningEffort && reasoningEffort !== "auto" ? reasoningEffort : null,
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

  const handleRollbackStep = async (messageId) => {
    const project = activeConv?.project;
    if (!project) {
      setError("Aucun projet actif pour le rollback.");
      return;
    }
    setNotice("Restauration du workspace en cours...");
    try {
      await api.post(`/workspace/projects/${encodeURIComponent(project)}/rollback`);
      setNotice("Workspace restaure au dernier snapshot. Rechargement des messages...");
      if (activeId) await loadMessages(activeId);
      else setMessages((prev) => [...prev]);
    } catch (e) {
      setError(formatApiError(e));
      setNotice("");
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

  const sendMessage = async (e, overrides = {}) => {
    e?.preventDefault();
    if (sending) return;
    const trimmed = (overrides.text ?? text).trim();
    if (!trimmed && !attachments.length) return;
    setError("");

    let convId = overrides.convId ?? activeId;
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
    resetStream();
    try {
      const form = new FormData();
      form.append("conversation_id", convId);
      form.append("text", sentText);
      sentFiles.forEach((f) => form.append("files", f));
      form.append("provider", provider);
      if (modelOverride) form.append("model", modelOverride);
      if (reasoningEffort && reasoningEffort !== "auto")
        form.append("reasoning_effort", reasoningEffort);

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
            addStreamTool(data);
          } else if (event === "step_start") {
            // Nouvelle etape : cadre distinct cote rendu.
            startStreamStep(data.index);
          } else if (event === "step_done") {
            // Etape validee et deja persistee cote serveur : on fige le cadre.
            finishStreamStep(data.step);
          } else if (event === "error") {
            setError(
              typeof data.detail === "string" && data.detail.trim()
                ? data.detail
                : "Erreur du serveur pendant la génération."
            );
          } else if (event === "done") {
            setMessages((prev) => [...prev, data]);
            resetStream();
          }
        },
      });
      fetchConversations();
      refreshUsage();
    } catch (err) {
      const aborted = err?.name === "AbortError";
      const interrupted =
        err?.isStreamInterrupted || err?.message === "STREAM_INTERRUPTED";
      if (aborted) {
        // Mode autonome : la génération continue côté serveur. On informe
        // l'utilisateur sans crier à l'erreur, puis on resynchronisera.
        setNotice(
          "Connexion fermée — la génération continue en tâche de fond."
        );
        fetchStatusTurns();
        await new Promise((r) => setTimeout(r, 700));
      } else if (interrupted) {
        // Le flux SSE a été coupé (proxy, mise en veille, réseau). Ce n'est
        // PAS un échec : le backend a déjà persisté ce qu'il a produit.
        // On NE supprime PAS le message de l'utilisateur (bug historique qui
        // faisait disparaitre toute la conversation jusqu'au rechargement).
        // On laisse un court instant au serveur, on resynchronise, et si rien
        // n'est encore arrivé on informe calmement.
        setNotice(
          "Connexion au flux interrompue — resynchronisation avec le serveur…"
        );
        await new Promise((r) => setTimeout(r, 1200));
        try {
          const { data } = await api.get(`/conversations/${convId}/messages`);
          const list = Array.isArray(data) ? data : data?.messages || [];
          // Si le serveur a bien enregistré le message utilisateur, on
          // remplace l'optimiste par la version serveur ; sinon on le garde
          // quand même à l'écran (jamais de disparition).
          const hasUser = list.some((m) => m.role === "user");
          setMessages((prev) => {
            const cleaned = prev.filter(
              (m) => !(m.id === optimisticUser.id && hasUser)
            );
            return reconcileMessages(cleaned, list.length ? list : cleaned);
          });
          if (!list.length) {
            setNotice("");
            setError(
              "La connexion a été coupée avant tout enregistrement. Réessaie ton message."
            );
          }
        } catch (_) {
          // Resynchro impossible (réseau toujours coupé) : on garde le message
          // visible et on prévient.
          setNotice("");
          setError(
            "Réseau instable — impossible de resynchroniser. Recharge la page pour revoir la conversation."
          );
        }
      } else {
        // Vraie erreur métier : message lisible, et on GARDE le message
        // utilisateur à l'écran (il a bien été envoyé).
        setError(formatApiError(err) || "Erreur inconnue");
      }
      // Le serveur conserve ce qui a déjà été généré : on resynchronise.
      if (!interrupted) {
        loadMessages(convId);
        fetchConversations();
      }
    } finally {
      abortRef.current = null;
      resetStream();
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

  const handleReasoningChange = (value) => {
    setReasoningEffort(value);
    localStorage.setItem("forge_reasoning_effort", value);
  };

  // Saisie multi-lignes : Entree insere un retour a la ligne (PAS d'envoi).
  // L'envoi se fait via le bouton, ou via la touche de commande + Entree.
  const onKeyDown = (e) => {
    if (e.key === "Enter" && (e.getModifierState?.("Control") || e.getModifierState?.("Meta"))) {
      e.preventDefault();
      sendMessage();
    }
  };

  const runSecurityAudit = () => {
    if (sending) return;
    sendMessage(null, { text: SECURITY_AUDIT_PROMPT });
  };

  const [exportingAuditPdf, setExportingAuditPdf] = useState(false);
  const exportAuditPdf = async () => {
    if (exportingAuditPdf) return;
    setExportingAuditPdf(true);
    try {
      const { data } = await api.get("/audit/export-pdf", { responseType: "blob" });
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = "AUDIT_SECURITE.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setExportingAuditPdf(false);
    }
  };

  const lastMsg = messages[messages.length - 1];
  const lastAssistantId = lastMsg && lastMsg.role === "assistant" ? lastMsg.id : null;
  const activeModel = models.find((m) => m.id === provider);
  const catalog = Array.isArray(activeModel?.models) ? activeModel.models : [];
  return (
    <div className="h-full w-full flex bg-[var(--bg-main)] text-white overflow-hidden">
      {(runningTurns.length > 0 || statusTurns.length > 0) && (
        <div
          className="fixed top-3 right-3 z-50 max-w-md w-[calc(100vw-1.5rem)] sm:w-auto bg-[var(--bg-sidebar)] border-2 border-amber-400/60 text-white rounded-lg shadow-xl p-4 space-y-2"
          data-testid="resume-banner"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="font-heading font-bold text-sm tracking-tight">
              {runningTurns.length > 0
                ? "Mode autonome — génération en cours côté serveur"
                : "Reprise — travail pendant ton absence"}
            </div>
            <button
              type="button"
              onClick={dismissStatusBanner}
              className="text-gray-400 hover:text-white text-lg leading-none"
              aria-label="Fermer le résumé"
            >
              ×
            </button>
          </div>

          {runningTurns.length > 0 && (
            <ul className="space-y-1 text-[12px] text-gray-200">
              {runningTurns.slice(0, 5).map((r) => (
                <li
                  key={`run-${r.conversation_id}`}
                  className="flex items-start gap-2"
                >
                  <span className="inline-block w-2 h-2 mt-1 flex-shrink-0 rounded-full bg-sky-400 animate-pulse" />
                  <span>
                    <span className="text-white font-medium">En cours</span>
                    {" — "}
                    {r.tool_count > 0
                      ? `${r.tool_count} outil(s) exécuté(s)…`
                      : "réflexion…"}
                    {" (déconnecté, ça continue toute seule)"}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {runningTurns.length === 0 && (
            <ul className="space-y-1 text-[12px] text-gray-300">
              {statusTurns.slice(0, 5).map((t) => (
                <li key={t.conversation_id} className="flex items-start gap-2">
                  <span
                    className={`inline-block w-2 h-2 mt-1 flex-shrink-0 rounded-full ${
                      t.stopped ? "bg-amber-400" : "bg-emerald-400"
                    }`}
                  />
                  <span>
                    <span className="text-white font-medium">{t.title}</span>
                    {" — "}
                    {t.summary}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Sidebar (extrait -> components/chat/Sidebar.jsx) */}
      <Sidebar
        sidebarOpen={sidebarOpen}
        collapsed={sidebarCollapsed}
        onCollapse={() => setSidebarCollapsed(true)}
        onClose={() => setSidebarOpen(false)}
        theme={theme}
        onToggleTheme={toggleTheme}
        conversations={conversations}
        activeId={activeId}
        onSelectConversation={(id) => {
          setActiveId(id);
          setSidebarOpen(false);
        }}
        onNewConversation={newConversation}
        editingId={editingId}
        editingTitle={editingTitle}
        onEditingTitleChange={setEditingTitle}
        onStartRename={startRename}
        onCancelRename={cancelRename}
        onSubmitRename={submitRename}
        onDeleteConversation={deleteConversation}
        user={user}
        onLogout={logout}
      />


      {githubOpen && (
        <GithubSaveDialog
          conversationId={activeId}
          onClose={() => setGithubOpen(false)}
        />
      )}

      {/* Superposition glisser-déposer */}      {dragging && (
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
        <ChatHeader
          sidebarCollapsed={sidebarCollapsed}
          onOpenSidebar={() => {
            if (window.innerWidth >= 1024) setSidebarCollapsed(false);
            else setSidebarOpen(true);
          }}
          activeId={activeId}
          onCloseSession={() => setActiveId(null)}
          onAbortRequest={abortRequest}
          activeConv={activeConv}
          projectUrls={projectUrls}
          onProjectUrlSaved={(project, url) =>
            setProjectUrls((prev) => ({ ...prev, [project]: url }))
          }
          usage={usage}
          provider={provider}
          autoChain={autoChain}
          models={models}
          activeModel={activeModel}
          modelOverride={modelOverride}
        />

        {/* Bandeau discret : mémoire de contexte (Fetcher) du projet actif */}
        {activeConv?.project && projectContext?.exists && (
          <div className="border-b border-white/5 bg-white/[0.02] px-3 sm:px-4 lg:px-8 py-1.5">
            <div className={`${wrapW} mx-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] font-mono text-gray-500`}>
              <button
                type="button"
                onClick={() => setContextOpen((o) => !o)}
                className="flex items-center gap-1.5 py-1.5 sm:py-0 text-gray-500 hover:text-[#05d9e8] transition-colors bg-transparent border-0 p-0 touch-manipulation"
                title="Mémoire de contexte du projet (Fetcher)"
                data-testid="context-badge"
              >
                <Brain className="w-3 h-3" />
                <span>contexte</span>
                <span className="text-[#05d9e8]">{projectContext.count || 0}</span>
                <span className="text-gray-600">
                  fait(s)
                  {(projectContext.facts || []).length <
                    (projectContext.count || 0) &&
                    ` · ${(projectContext.facts || []).length} affichés`}
                </span>
              </button>
              {(projectContext.snapshot?.stack || []).length > 0 && (
                <span className="truncate max-w-full sm:max-w-[40%]">
                  {projectContext.snapshot.stack.join(" · ")}
                </span>
              )}
              {projectContext.snapshot?.git?.last_commit && (
                <span className="truncate max-w-full sm:max-w-[35%] text-gray-600">
                  ⎇ {projectContext.snapshot.git.branch || "?"} ·{" "}
                  {projectContext.snapshot.git.last_commit}
                </span>
              )}
            </div>
            {/* Détail dépliable : les faits mémorisés, classés par priorité */}
            {contextOpen && (projectContext.facts || []).length > 0 && (
              <div className={`${wrapW} mx-auto mt-1.5 mb-1 border border-white/5 rounded-sm divide-y divide-white/5 max-h-[40vh] overflow-y-auto`}>
                {(Array.isArray(projectContext.facts) ? projectContext.facts : []).map((f, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-2 px-2 py-2 sm:py-1 text-[10px] font-mono"
                  >
                    <span
                      className={
                        "flex-shrink-0 px-1 rounded-sm " +
                        (f.priority >= 4
                          ? "text-[#ff2a6d]"
                          : f.priority === 3
                          ? "text-[#ffd700]"
                          : f.priority === 2
                          ? "text-[#05d9e8]"
                          : "text-gray-600")
                      }
                      title={`priorité ${f.priority}`}
                    >
                      [{f.kind}]
                    </span>
                    <span className="text-gray-400 break-words">{f.text}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Hub d'accueil tant qu'aucune session n'est ouverte */}
        {!activeId && (
          <ProjectHub onStart={startTask} onOpenProject={openProject} />
        )}

        {/* Messages */}
        {activeId && (
        <div ref={messagesContainerRef} onScroll={handleMessagesScroll} className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-3 sm:px-4 lg:px-8 py-4 sm:py-6">
          <div className={`${wrapW} mx-auto`} data-testid="messages-container">
            {activeId && messages.length === 0 && loadingMsgs && (
              <MessagesSkeleton />
            )}
            {activeId && messages.length === 0 && !loadingMsgs && (
              <EmptyChat />
            )}
            {hasMore && messages.length > 0 && (
              <div className="flex justify-center pb-4">
                <button
                  type="button"
                  onClick={loadOlderMessages}
                  disabled={loadingOlder}
                  className="text-[11px] font-mono uppercase tracking-wider text-gray-400 hover:text-white border border-white/20 hover:border-white/50 px-3 py-1.5 transition-colors disabled:opacity-50"
                  data-testid="load-older-btn"
                >
                  {loadingOlder ? "chargement…" : "↑ messages plus anciens"}
                </button>
              </div>
            )}
            {messages.map((m) => (
              <ChatMessage
                key={m.id}
                message={m}
                isLast={m.id === lastAssistantId}
                onRegenerate={regenerate}
                onFeedback={submitFeedback}
                onDelete={deleteMessage}
                regenerating={regenerating}
                project={activeConv?.project}
                onRollbackStep={handleRollbackStep}
              />
            ))}
            {lastAssistantId && !sending && (
              <div className="mt-4 mb-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={runSecurityAudit}
                  className="security-audit-btn"
                  data-testid="security-audit-btn"
                  title="Lancer un audit de sécurité du workspace"
                >
                  <ShieldCheck className="w-4 h-4" />
                  Audit de sécurité
                </button>
                <button
                  type="button"
                  onClick={exportAuditPdf}
                  disabled={exportingAuditPdf}
                  className="security-audit-btn"
                  data-testid="export-audit-pdf-btn"
                  title="Télécharger le rapport d'audit de sécurité au format PDF"
                >
                  {exportingAuditPdf ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <FileDown className="w-4 h-4" />
                  )}
                  Télécharger le rapport PDF
                </button>
              </div>
            )}
            {sending && (
              <>
                <StreamingBubble
                  provider={provider}
                  model={modelOverride || activeModel?.model}
                  project={activeConv?.project}
                  onRollbackStep={handleRollbackStep}
                />
                {!streamingHasContent && (
                  <div className="flex gap-4 mb-6">
                    <div className="w-10 h-10 border-2 border-white/30 bg-[var(--bg-dock)] flex items-center justify-center flex-shrink-0">
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
        )}

        {/* Notice banner (mode autonome) */}
        {notice && (
          <div className="px-3 sm:px-4 lg:px-8 pb-2 flex-shrink-0">
            <div
              className={`${wrapW} mx-auto border-2 border-sky-400 bg-sky-400/10 text-sky-200 p-3 text-sm font-mono flex items-center justify-between`}
              data-testid="chat-notice"
            >
              <span>{notice}</span>
              <button onClick={() => setNotice("")} className="ml-2">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div className="px-3 sm:px-4 lg:px-8 pb-2 flex-shrink-0">
            <div
              className={`${wrapW} mx-auto border-2 border-[#ff2a6d] bg-[#ff2a6d]/10 text-[#ff2a6d] p-3 text-sm font-mono flex items-center justify-between`}
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
          <div className={`${wrapW} mx-auto relative`}>
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
            <button
              type="button"
              onClick={scrollToBottom}
              aria-label="Faire défiler vers le bas"
              className={`scroll-bottom-btn ${showScrollBottom ? "is-visible" : ""}`}
              data-testid="scroll-to-bottom-btn"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 5v14" />
                <path d="M19 12l-7 7-7-7" />
              </svg>
            </button>
            <form
              onSubmit={sendMessage}
              className="chat-dock flex flex-col"
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
              {/* Bandeau de statut discret */}
              <div className="chat-status-bar" data-testid="chat-status-bar">
                <span
                  className={`chat-status-dot ${sending ? "is-busy" : ""}`}
                  data-testid="chat-status-dot"
                />
                <span className="chat-status-text" data-testid="chat-status-text">
                  {sending ? "Agent en cours..." : "Agent attend..."}
                </span>
              </div>
              {/* Saisie pleine largeur, multi-lignes, au-dessus de la barre d'outils */}
              <textarea
                ref={textareaRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={onKeyDown}
                rows={2}
                placeholder="Forge a message... (Entree = nouvelle ligne)"
                className="chat-textarea w-full outline-none resize-none px-3 sm:px-4"
                data-testid="chat-text-input"
              />
              {/* Barre d'outils : outils/dictée à gauche, providers au centre, envoi à droite */}
              <div className="chat-toolbar flex flex-wrap items-center gap-x-2 gap-y-2 min-w-0 px-2 sm:px-3 py-2">
                <div className="flex items-center gap-2 flex-shrink-0">
                  <div className="relative flex-shrink-0">
                    <button
                      type="button"
                      onClick={() => setPlusOpen((v) => !v)}
                      className={`btn-ghost border-2 ${
                        plusOpen
                          ? "border-[#ffd700] text-[#ffd700]"
                          : "border-white/20 hover:border-[#ffd700] hover:text-[#ffd700]"
                      }`}
                      title="Outils : fichier, GitHub, fork"
                      data-testid="plus-menu-btn"
                    >
                      <Plus className="w-5 h-5" />
                    </button>
                    {plusOpen && (
                      <>
                        <div
                          className="fixed inset-0 z-30"
                          onClick={() => setPlusOpen(false)}
                          data-testid="plus-menu-backdrop"
                        />
                        <div
                          className="absolute z-40 bottom-full mb-2 left-0 w-60 border-2 border-white/20 bg-[var(--bg-dock)] shadow-[6px_6px_0_0_#05d9e8]"
                          data-testid="plus-menu"
                        >
                          <button
                            type="button"
                            onClick={() => {
                              setPlusOpen(false);
                              fileInputRef.current?.click();
                            }}
                            className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-xs font-mono uppercase tracking-wider text-gray-300 hover:bg-white/5 hover:text-[#ffd700] transition-colors"
                            data-testid="menu-attach-file"
                          >
                            <Paperclip className="w-4 h-4" /> Joindre un fichier
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setPlusOpen(false);
                              setGithubOpen(true);
                            }}
                            className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-xs font-mono uppercase tracking-wider text-gray-300 hover:bg-white/5 hover:text-[#05d9e8] transition-colors border-t border-white/10"
                            data-testid="menu-save-github"
                          >
                            <Github className="w-4 h-4" /> Enregistrer sur GitHub
                          </button>
                          <button
                            type="button"
                            onClick={forkConversation}
                            disabled={!activeId || forking}
                            className="w-full flex items-center gap-3 px-3 py-2.5 text-left text-xs font-mono uppercase tracking-wider text-gray-300 hover:bg-white/5 hover:text-[#ff2a6d] transition-colors border-t border-white/10 disabled:opacity-40"
                            data-testid="menu-fork-chat"
                          >
                            {forking ? (
                              <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <GitFork className="w-4 h-4" />
                            )}
                            Forker ce chat
                          </button>
                        </div>
                      </>
                    )}
                  </div>
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
                  <VoicePicker />
                </div>
                <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0 justify-center sm:justify-end">
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
                      <option value="auto" className="bg-[var(--bg-dock)] text-white">
                        Auto (meilleur dispo)
                      </option>
                      {models.map((m) => (
                        <option
                          key={m.id}
                          value={m.id}
                          className="bg-[var(--bg-dock)] text-white"
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
                        <option value="" className="bg-[var(--bg-dock)] text-white">
                          défaut ({activeModel?.model})
                        </option>
                        {catalog.map((m) => (
                          <option key={m} value={m} className="bg-[var(--bg-dock)] text-white">
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
                  <div
                    className="flex items-center gap-px border-2 border-white/20 bg-black/40 px-1 py-1 flex-shrink-0 sm:ml-2"
                    title="Effort de raisonnement (modeles compatibles uniquement)"
                  >
                    <Brain className="w-4 h-4 text-gray-500 flex-shrink-0 mr-0.5" />
                    {[
                      { v: "auto", label: "A", full: "auto" },
                      { v: "low", label: "L", full: "faible" },
                      { v: "medium", label: "M", full: "moyen" },
                      { v: "high", label: "H", full: "eleve" },
                    ].map((opt) => {
                      const active = reasoningEffort === opt.v;
                      return (
                        <button
                          key={opt.v}
                          type="button"
                          onClick={() => handleReasoningChange(opt.v)}
                          className={`px-1.5 py-0.5 text-[10px] font-mono font-bold uppercase leading-none border ${
                            active
                              ? "bg-[#05d9e8] text-black border-[#05d9e8]"
                              : "bg-transparent text-gray-400 border-white/20 hover:border-[#05d9e8]/60 hover:text-[#05d9e8]"
                          }`}
                          title={`Reasoning: ${opt.full}`}
                          data-testid={`reasoning-${opt.v}`}
                        >
                          {opt.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
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
