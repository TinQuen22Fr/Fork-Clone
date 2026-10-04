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
  Download,
  Upload,
  X,
  MoreVertical,
  Trash2,
  RefreshCw,
  Server,
} from "lucide-react";

/* Couleurs de teinte pour le fallback visuel (projets sans URL de preview). */
const FALLBACK_HUES = ["#ff2a6d", "#ffd700", "#05d9e8", "#a855f7", "#22c55e", "#f97316"];

/* Genere un visuel de repli deterministe a partir du nom du projet : monogramme
   + degrade teinte. Utile pour les projets non-web (Arduino, ESP32, API locale,
   logiciel installable sur l'ordi...) qui n'ont pas d'URL de preview. */
const fallbackVisual = (name) => {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const hue = FALLBACK_HUES[h % FALLBACK_HUES.length];
  const mono = (name.replace(/[^a-zA-Z0-9]/g, "").slice(0, 2) || "??").toUpperCase();
  return { hue, mono };
};

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
  const [confirmDelete, setConfirmDelete] = useState(null); // projet en attente de confirmation
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(""); // nom du projet en cours d'export
  const [importing, setImporting] = useState(false);
  const [openMenu, setOpenMenu] = useState(null); // nom du projet dont le menu "..." est ouvert
  const fileInputRef = React.useRef(null);

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

  const deleteProject = async (p) => {
    setDeleting(true);
    setError("");
    try {
      await api.delete(`/workspace/projects/${p.name}`);
      setProjects((prev) => prev.filter((x) => x.name !== p.name));
      setConfirmDelete(null);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setDeleting(false);
    }
  };

  const exportProject = async (p) => {
    setExporting(p.name);
    setError("");
    try {
      const { data } = await api.get(
        `/workspace/projects/${p.name}/export`,
        { responseType: "blob" }
      );
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${p.name}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setExporting("");
    }
  };

  const triggerImport = () => {
    if (importing) return;
    fileInputRef.current?.click();
  };

  const handleImportFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // permet de reselectionner le meme fichier ensuite
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setError("Seules les archives .zip sont acceptées pour l'import.");
      return;
    }
    setImporting(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      if (newName.trim()) form.append("name", newName.trim());
      const { data } = await api.post("/workspace/projects/import", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setProjects((prev) => [data, ...prev.filter((p) => p.name !== data.name)]);
      setProject(data.name);
      setNewName("");
    } catch (e2) {
      setError(formatApiError(e2));
    } finally {
      setImporting(false);
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
      className="hub-canvas relative flex-1 overflow-y-auto"
      data-testid="project-hub"
    >
      {/* Halo decoratif haut (equivalent visuel du radial-gradient d'Emergent) */}
      <div className="hub-halo" aria-hidden="true" />

      <div className="relative mx-auto w-full max-w-3xl px-6 sm:px-10 lg:px-16">
        <div className="hub-hero">
          <div className="text-[10px] font-mono uppercase tracking-[0.35em] text-gray-600">
            // hub
          </div>
          <h1 className="mt-2 font-heading text-4xl sm:text-5xl font-black uppercase tracking-tighter leading-[0.95]">
            Qu'est-ce qu'on
            <br />
            <span className="text-[#ff2a6d]">construit</span> aujourd'hui ?
          </h1>
        </div>

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
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip,application/zip"
                onChange={handleImportFile}
                className="hidden"
                data-testid="hub-import-file-input"
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
              <button
                type="button"
                onClick={triggerImport}
                disabled={importing}
                title="Importer un projet depuis une archive .zip (GitHub, backup...)"
                className="btn-ghost border-2 border-white/20 hover:border-[#05d9e8] hover:text-[#05d9e8] disabled:opacity-40"
                data-testid="hub-import-project-btn"
              >
                {importing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Upload className="w-4 h-4" />
                )}
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
            <div className="mt-4 hub-plist">
              {projects.map((p) => {
                const phase = PHASES[p.preview_phase] || PHASES.stopped;
                const run = p.preview_phase === "running";
                const vis = fallbackVisual(p.name);
                const thumbSrc = p.preview_url
                  ? `https://image.thum.io/get/width/400/crop/260/${p.preview_url}`
                  : null;
                return (
                  <div
                    key={p.name}
                    className="hub-prow"
                    data-testid={`hub-project-${p.name}`}
                  >
                    {/* Vignette : capture live de la preview, sinon fallback visuel */}
                    <div className="hub-pthumb" title={p.preview_url || p.name}>
                      {thumbSrc ? (
                        <img
                          src={thumbSrc}
                          alt={`Aperçu de ${p.name}`}
                          loading="lazy"
                          onError={(e) => {
                            e.currentTarget.style.display = "none";
                            const fb = e.currentTarget.nextSibling;
                            if (fb) fb.style.display = "flex";
                          }}
                        />
                      ) : null}
                      <div
                        className="hub-pthumb-fallback"
                        style={{
                          display: thumbSrc ? "none" : "flex",
                          background: `linear-gradient(135deg, ${vis.hue}33, ${vis.hue}0d)`,
                        }}
                      >
                        {vis.mono}
                      </div>
                      <span
                        className="hub-pthumb-phase"
                        style={{ backgroundColor: phase.color }}
                      />
                    </div>

                    {/* Nom + meta */}
                    <button
                      type="button"
                      onClick={() => onOpenProject(p)}
                      className="hub-pmain"
                      data-testid={`hub-open-${p.name}`}
                    >
                      <span className="hub-pname">{p.name}</span>
                      <span className="hub-pmeta">
                        <span style={{ color: phase.color }}>{phase.label}</span>
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
                      </span>
                    </button>

                    {/* Menu d'actions "..." */}
                    <div className="hub-pactions">
                      <button
                        type="button"
                        onClick={() =>
                          setOpenMenu((cur) => (cur === p.name ? null : p.name))
                        }
                        className={`hub-pmenu-btn${openMenu === p.name ? " open" : ""}`}
                        title="Actions du projet"
                        data-testid={`hub-menu-${p.name}`}
                      >
                        <MoreVertical className="w-4 h-4" />
                      </button>

                      {openMenu === p.name && (
                        <>
                          {/* voile pour fermer au clic exterieur */}
                          <div
                            className="fixed inset-0 z-30"
                            onClick={() => setOpenMenu(null)}
                          />
                          <div className="hub-pmenu" data-testid={`hub-menu-panel-${p.name}`}>
                            {p.preview_url && (
                              <button
                                type="button"
                                onClick={() => {
                                  setOpenMenu(null);
                                  run
                                    ? window.open(p.preview_url, "_blank", "noreferrer")
                                    : previewAction(p, "start");
                                }}
                                disabled={acting.startsWith(`${p.name}:`)}
                              >
                                {acting.startsWith(`${p.name}:`) ? (
                                  <Loader2 className="w-4 h-4 animate-spin" />
                                ) : run ? (
                                  <ExternalLink className="w-4 h-4" />
                                ) : (
                                  <Play className="w-4 h-4" />
                                )}
                                {run ? "Ouvrir la preview" : "Démarrer la preview"}
                              </button>
                            )}
                            {p.preview_url && run && (
                              <button
                                type="button"
                                onClick={() => {
                                  setOpenMenu(null);
                                  previewAction(p, "stop");
                                }}
                              >
                                <Square className="w-4 h-4" />
                                Arrêter la preview
                              </button>
                            )}
                            {p.preview_url && run && (
                              <button
                                type="button"
                                onClick={() => {
                                  setOpenMenu(null);
                                  previewAction(p, "restart");
                                }}
                              >
                                <RefreshCw className="w-4 h-4" />
                                Redémarrer
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => {
                                setOpenMenu(null);
                                exportProject(p);
                              }}
                              disabled={exporting === p.name}
                            >
                              {exporting === p.name ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                              ) : (
                                <Download className="w-4 h-4" />
                              )}
                              Exporter en .zip
                            </button>
                            <div className="hub-pmenu-sep" />
                            <button
                              type="button"
                              className="danger"
                              onClick={() => {
                                setOpenMenu(null);
                                setConfirmDelete(p);
                              }}
                              data-testid={`hub-delete-${p.name}`}
                            >
                              <Trash2 className="w-4 h-4" />
                              Supprimer
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {confirmDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 px-4"
          data-testid="hub-delete-confirm-overlay"
          onClick={() => !deleting && setConfirmDelete(null)}
        >
          <div
            className="w-full max-w-sm border-2 border-[#ff2a6d]/60 bg-[var(--bg-dock)] p-5"
            onClick={(e) => e.stopPropagation()}
            data-testid="hub-delete-confirm-dialog"
          >
            <div className="text-[10px] font-mono uppercase tracking-[0.3em] text-[#ff2a6d]">
              // suppression
            </div>
            <h2 className="mt-2 font-heading text-lg font-black uppercase tracking-tight">
              Supprimer « {confirmDelete.name} » ?
            </h2>
            <p className="mt-2 text-xs text-gray-400">
              Cette action est <span className="text-[#ff2a6d] font-semibold">irréversible</span> :
              le dossier du projet, ses conversations et ses données seront
              définitivement effacés. Confirmez-vous ?
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                disabled={deleting}
                className="btn-ghost border-2 border-white/20 hover:border-white/40 text-xs px-4 py-2 disabled:opacity-40"
                data-testid="hub-delete-confirm-no"
              >
                Non
              </button>
              <button
                type="button"
                onClick={() => deleteProject(confirmDelete)}
                disabled={deleting}
                className="border-2 border-[#ff2a6d] bg-[#ff2a6d]/10 hover:bg-[#ff2a6d]/20 text-[#ff2a6d] text-xs font-semibold px-4 py-2 flex items-center gap-2 disabled:opacity-40"
                data-testid="hub-delete-confirm-yes"
              >
                {deleting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Oui, supprimer
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ProjectHub;
