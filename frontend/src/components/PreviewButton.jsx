/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 * Logiciel libre sous GNU GPL v3 — voir LICENSE.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import {
  ExternalLink,
  Play,
  Square,
  RotateCw,
  ScrollText,
  X,
  Loader2,
  Pencil,
} from "lucide-react";

const PHASES = {
  stopped: { label: "arrêtée", color: "#6b7280" },
  installing: { label: "installation…", color: "#ffd700" },
  starting: { label: "démarrage…", color: "#ffd700" },
  running: { label: "en ligne", color: "#05d9e8" },
  error: { label: "erreur", color: "#ff2a6d" },
};

/**
 * Bouton Preview : pilote reellement le serveur de dev du projet (start/stop/
 * restart + logs) et ouvre l'URL publique une fois le port ouvert.
 */
export const PreviewButton = ({ project, previewUrl, onSaved }) => {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showLogs, setShowLogs] = useState(false);
  const [logs, setLogs] = useState("");
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState(previewUrl || "");
  const openWhenReady = useRef(false);

  const phase = status?.phase || "stopped";
  const meta = PHASES[phase] || PHASES.stopped;

  const refresh = useCallback(async () => {
    if (!project) return null;
    try {
      const { data } = await api.get(`/workspace/projects/${project}/preview/status`);
      setStatus(data);
      return data;
    } catch (e) {
      setError(formatApiError(e));
      return null;
    }
  }, [project]);

  useEffect(() => {
    setStatus(null);
    setError("");
    setUrl(previewUrl || "");
    refresh();
  }, [project, previewUrl, refresh]);

  // Sondage tant que ca installe / demarre.
  useEffect(() => {
    if (phase !== "installing" && phase !== "starting") return undefined;
    const t = setInterval(async () => {
      const s = await refresh();
      if (s?.phase === "running" && openWhenReady.current) {
        openWhenReady.current = false;
        window.open(s.preview_url || previewUrl, "_blank", "noreferrer");
      }
    }, 2000);
    return () => clearInterval(t);
  }, [phase, refresh, previewUrl]);

  const call = async (action) => {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post(
        `/workspace/projects/${project}/preview/${action}`
      );
      setStatus(data);
      return data;
    } catch (e) {
      setError(formatApiError(e));
      return null;
    } finally {
      setBusy(false);
    }
  };

  const openOrStart = async () => {
    const target = status?.preview_url || previewUrl;
    if (phase === "running") {
      window.open(target, "_blank", "noreferrer");
      return;
    }
    openWhenReady.current = true;
    const s = await call("start");
    if (s?.phase === "running") {
      openWhenReady.current = false;
      window.open(s.preview_url || target, "_blank", "noreferrer");
    } else if (s?.phase === "error") {
      openWhenReady.current = false;
      setShowLogs(true);
      loadLogs();
    }
  };

  const loadLogs = async () => {
    try {
      const { data } = await api.get(`/workspace/projects/${project}/preview/logs`);
      setLogs(data.logs || "(journal vide)");
    } catch (e) {
      setLogs(formatApiError(e));
    }
  };

  const saveUrl = async () => {
    setBusy(true);
    try {
      const { data } = await api.put(`/workspace/projects/${project}`, {
        preview_url: url.trim(),
      });
      onSaved?.(data.preview_url);
      setEditing(false);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  if (!project) return null;

  return (
    <>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={openOrStart}
          disabled={busy}
          title={
            phase === "running"
              ? `Ouvrir ${status?.preview_url || previewUrl}`
              : `Démarrer la preview de ${project}`
          }
          className="flex items-center gap-1.5 border-2 border-white/20 px-2 py-1 text-[10px] font-mono uppercase tracking-[0.15em] text-gray-400 hover:border-[#05d9e8] hover:text-[#05d9e8] transition-colors disabled:opacity-40"
          data-testid="preview-btn"
        >
          {busy || phase === "installing" || phase === "starting" ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : phase === "running" ? (
            <ExternalLink className="w-3.5 h-3.5" />
          ) : (
            <Play className="w-3.5 h-3.5" />
          )}
          <span className="hidden sm:inline">Preview</span>
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: meta.color }}
            title={meta.label}
            data-testid="preview-phase-dot"
          />
        </button>

        {phase === "running" && (
          <>
            <button
              type="button"
              onClick={() => call("restart")}
              title="Redémarrer le serveur de dev"
              className="p-1 text-gray-600 hover:text-[#ffd700]"
              data-testid="preview-restart-btn"
            >
              <RotateCw className="w-3 h-3" />
            </button>
            <button
              type="button"
              onClick={() => call("stop")}
              title="Arrêter le serveur de dev"
              className="p-1 text-gray-600 hover:text-[#ff2a6d]"
              data-testid="preview-stop-btn"
            >
              <Square className="w-3 h-3" />
            </button>
          </>
        )}

        <button
          type="button"
          onClick={() => {
            setShowLogs(true);
            loadLogs();
          }}
          title="Journal de la preview"
          className="p-1 text-gray-600 hover:text-[#05d9e8]"
          data-testid="preview-logs-btn"
        >
          <ScrollText className="w-3 h-3" />
        </button>
        <button
          type="button"
          onClick={() => setEditing(true)}
          title="Modifier l'URL de preview"
          className="p-1 text-gray-600 hover:text-[#ffd700]"
          data-testid="preview-edit-btn"
        >
          <Pencil className="w-3 h-3" />
        </button>
      </div>

      {showLogs && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
          data-testid="preview-logs-dialog"
        >
          <div className="w-full max-w-3xl border-2 border-white/20 bg-[#0a0a0a] shadow-[8px_8px_0_0_#05d9e8]">
            <div className="flex items-center justify-between border-b-2 border-white/10 px-4 py-3">
              <span className="font-heading font-black uppercase tracking-tighter text-sm">
                Preview — {project}
                <span className="ml-2 font-mono text-[10px]" style={{ color: meta.color }}>
                  {meta.label}
                </span>
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={loadLogs}
                  className="text-[10px] font-mono uppercase text-gray-500 hover:text-[#05d9e8]"
                  data-testid="preview-logs-refresh"
                >
                  rafraîchir
                </button>
                <button
                  onClick={() => setShowLogs(false)}
                  className="text-gray-500 hover:text-[#ff2a6d]"
                  data-testid="preview-logs-close"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>
            <div className="px-4 py-2 border-b-2 border-white/10 text-[10px] font-mono text-gray-500 space-y-0.5">
              <div>type : {status?.kind || "—"} · port {status?.port || "—"}</div>
              {status?.command && <div className="truncate">$ {status.command}</div>}
              {status?.message && (
                <div className="text-[#ff8bb0]" data-testid="preview-status-message">
                  {status.message}
                </div>
              )}
            </div>
            <pre
              className="max-h-[55vh] overflow-auto bg-black/60 p-4 text-[11px] font-mono text-gray-300 whitespace-pre-wrap"
              data-testid="preview-logs-content"
            >
              {logs || "(journal vide)"}
            </pre>
            <div className="flex justify-end gap-2 border-t-2 border-white/10 px-4 py-3">
              <button
                onClick={() => call("start")}
                className="btn-ghost border-2 border-white/20 text-xs flex items-center gap-1.5"
                data-testid="preview-logs-start"
              >
                <Play className="w-3 h-3" /> Démarrer
              </button>
              <button
                onClick={() => call("restart")}
                className="btn-ghost border-2 border-white/20 text-xs flex items-center gap-1.5"
                data-testid="preview-logs-restart"
              >
                <RotateCw className="w-3 h-3" /> Redémarrer
              </button>
              <button
                onClick={() => call("stop")}
                className="btn-ghost border-2 border-white/20 text-xs flex items-center gap-1.5"
                data-testid="preview-logs-stop"
              >
                <Square className="w-3 h-3" /> Arrêter
              </button>
            </div>
          </div>
        </div>
      )}

      {editing && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
          data-testid="preview-dialog"
        >
          <div className="w-full max-w-md border-2 border-white/20 bg-[#0a0a0a] shadow-[8px_8px_0_0_#05d9e8]">
            <div className="flex items-center justify-between border-b-2 border-white/10 px-4 py-3">
              <span className="font-heading font-black uppercase tracking-tighter text-sm">
                URL de preview — {project}
              </span>
              <button
                onClick={() => setEditing(false)}
                className="text-gray-500 hover:text-[#ff2a6d]"
                data-testid="preview-dialog-close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 space-y-3">
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && saveUrl()}
                placeholder="https://projet.preview.quentin-astro.fr"
                className="w-full bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs"
                data-testid="preview-url-input"
              />
              <p className="text-[10px] font-mono text-gray-500">
                Vide = URL automatique du projet.
              </p>
              {error && (
                <div
                  className="border-2 border-[#ff2a6d]/60 bg-[#ff2a6d]/10 px-3 py-2 text-xs font-mono text-[#ff8bb0]"
                  data-testid="preview-error"
                >
                  {error}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 border-t-2 border-white/10 px-4 py-3">
              <button
                onClick={() => setEditing(false)}
                className="btn-ghost border-2 border-white/20 text-xs"
              >
                Annuler
              </button>
              <button
                onClick={saveUrl}
                disabled={busy}
                className="btn-primary text-xs flex items-center gap-2 disabled:opacity-40"
                data-testid="preview-save-btn"
              >
                {busy && <Loader2 className="w-3 h-3 animate-spin" />}
                Enregistrer
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default PreviewButton;
