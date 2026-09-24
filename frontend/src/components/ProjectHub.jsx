/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 * Logiciel libre sous GNU GPL v3 — voir LICENSE.
 */
import React, { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import {
  FolderGit2,
  Plus,
  ArrowRight,
  ExternalLink,
  Loader2,
  MessageSquare,
  Play,
  Square,
} from "lucide-react";

/** Ecran d'accueil : prompt central + projets du workspace. */
export const ProjectHub = ({ onStart, onOpenProject }) => {
  const [projects, setProjects] = useState([]);
  const [root, setRoot] = useState("");
  const [prompt, setPrompt] = useState("");
  const [project, setProject] = useState("");
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [acting, setActing] = useState("");

  const PHASES = {
    stopped: { label: "arrêtée", color: "#6b7280" },
    installing: { label: "installation…", color: "#ffd700" },
    starting: { label: "démarrage…", color: "#ffd700" },
    running: { label: "en ligne", color: "#05d9e8" },
    error: { label: "erreur", color: "#ff2a6d" },
  };

  const previewAction = async (p, action) => {
    setActing(`${p.name}:${action}`);
    try {
      const { data } = await api.post(
        `/workspace/projects/${p.name}/preview/${action}`
      );
      setProjects((prev) =>
        prev.map((x) =>
          x.name === p.name
            ? { ...x, preview_phase: data.phase, preview_message: data.message }
            : x
        )
      );
      if (data.phase === "running") {
        window.open(data.preview_url || p.preview_url, "_blank", "noreferrer");
      }
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setActing("");
    }
  };

  const load = async () => {
    try {
      const { data } = await api.get("/workspace/projects");
      setProjects(data.projects || []);
      setRoot(data.root || "");
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const createProject = async () => {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post("/workspace/projects", { name });
      setProjects((prev) => [data, ...prev.filter((p) => p.name !== data.name)]);
      setProject(data.name);
      setNewName("");
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const start = async (e) => {
    e?.preventDefault();
    const p = prompt.trim();
    if (!p || busy) return;
    setBusy(true);
    try {
      await onStart(p, project || null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="flex-1 overflow-y-auto px-6 py-10 sm:px-10 lg:px-16"
      data-testid="project-hub"
    >
      <div className="max-w-3xl">
        <div className="text-[10px] font-mono uppercase tracking-[0.35em] text-gray-600">
          // hub
        </div>
        <h1 className="mt-2 font-heading text-4xl sm:text-5xl font-black uppercase tracking-tighter leading-[0.95]">
          Qu'est-ce qu'on
          <br />
          <span className="text-[#ff2a6d]">construit</span> aujourd'hui ?
        </h1>

        {/* Prompt central */}
        <form onSubmit={start} className="mt-8 space-y-3">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) start(e);
            }}
            rows={3}
            placeholder="Décris la tâche : « Crée un scraper météo avec alertes SMS »…"
            className="w-full resize-none bg-black/50 border-2 border-white/20 focus:border-[#ff2a6d] outline-none px-4 py-3 text-sm placeholder:text-gray-600"
            data-testid="hub-prompt-input"
          />
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
              className="bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs"
              data-testid="hub-project-select"
            >
              <option value="">— sans projet —</option>
              {projects.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-1">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="nouveau-projet"
                className="w-40 bg-black/50 border-2 border-white/20 focus:border-[#ffd700] outline-none px-3 py-2 font-mono text-xs"
                data-testid="hub-new-project-input"
              />
              <button
                type="button"
                onClick={createProject}
                disabled={busy || !newName.trim()}
                title="Créer le dossier du projet dans le workspace"
                className="btn-ghost border-2 border-white/20 hover:border-[#ffd700] hover:text-[#ffd700] disabled:opacity-40"
                data-testid="hub-create-project-btn"
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>
            <button
              type="submit"
              disabled={busy || !prompt.trim()}
              className="btn-primary ml-auto flex items-center gap-2 text-xs disabled:opacity-40"
              data-testid="hub-start-btn"
            >
              {busy ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <ArrowRight className="w-4 h-4" />
              )}
              Démarrer
            </button>
          </div>
        </form>

        {error && (
          <div
            className="mt-4 border-2 border-[#ff2a6d]/60 bg-[#ff2a6d]/10 px-3 py-2 text-xs font-mono text-[#ff8bb0]"
            data-testid="hub-error"
          >
            {error}
          </div>
        )}

        {/* Projets existants */}
        <div className="mt-12">
          <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.25em] text-gray-600">
            <FolderGit2 className="w-3.5 h-3.5" />
            Projets {root && <span className="normal-case">· {root}</span>}
          </div>

          {loading ? (
            <div className="mt-4 flex items-center gap-2 text-xs font-mono text-gray-500">
              <Loader2 className="w-3 h-3 animate-spin" /> chargement…
            </div>
          ) : projects.length === 0 ? (
            <p className="mt-4 text-sm text-gray-500">
              Aucun projet pour l'instant. Crée-en un ci-dessus, son dossier sera
              créé dans le workspace.
            </p>
          ) : (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {projects.map((p) => (
                <div
                  key={p.name}
                  className="group border-2 border-white/15 bg-black/40 p-4 hover:border-[#05d9e8] transition-colors"
                  data-testid={`hub-project-${p.name}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => onOpenProject(p)}
                      className="text-left font-heading font-black uppercase tracking-tight text-sm hover:text-[#05d9e8]"
                      data-testid={`hub-open-${p.name}`}
                    >
                      {p.name}
                    </button>
                    {p.preview_url && (
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() =>
                            p.preview_phase === "running"
                              ? window.open(p.preview_url, "_blank", "noreferrer")
                              : previewAction(p, "start")
                          }
                          disabled={acting.startsWith(`${p.name}:`)}
                          title={
                            p.preview_phase === "running"
                              ? `Ouvrir ${p.preview_url}`
                              : `Démarrer la preview de ${p.name}`
                          }
                          className="text-gray-600 hover:text-[#ffd700] disabled:opacity-40"
                          data-testid={`hub-preview-${p.name}`}
                        >
                          {acting.startsWith(`${p.name}:`) ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : p.preview_phase === "running" ? (
                            <ExternalLink className="w-4 h-4" />
                          ) : (
                            <Play className="w-4 h-4" />
                          )}
                        </button>
                        {p.preview_phase === "running" && (
                          <button
                            type="button"
                            onClick={() => previewAction(p, "stop")}
                            title="Arrêter la preview"
                            className="text-gray-600 hover:text-[#ff2a6d]"
                            data-testid={`hub-preview-stop-${p.name}`}
                          >
                            <Square className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="mt-2 flex items-center gap-3 text-[10px] font-mono text-gray-500">
                    <span
                      className="flex items-center gap-1"
                      style={{ color: (PHASES[p.preview_phase] || PHASES.stopped).color }}
                      data-testid={`hub-preview-phase-${p.name}`}
                    >
                      <span
                        className="w-1.5 h-1.5 rounded-full"
                        style={{
                          backgroundColor: (PHASES[p.preview_phase] || PHASES.stopped)
                            .color,
                        }}
                      />
                      {(PHASES[p.preview_phase] || PHASES.stopped).label}
                    </span>
                    <span className="flex items-center gap-1">
                      <MessageSquare className="w-3 h-3" />
                      {p.conversations ?? 0}
                    </span>
                    {p.is_git_repo && <span className="text-[#ffd700]">git</span>}
                    {p.preview_port && <span>:{p.preview_port}</span>}
                    {p.preview_url && (
                      <span className="truncate text-[#05d9e8]">
                        {p.preview_url.replace(/^https?:\/\//, "")}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ProjectHub;
