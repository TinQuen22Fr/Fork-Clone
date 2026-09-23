/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 * Logiciel libre sous GNU GPL v3 — voir LICENSE.
 */
import React, { useState } from "react";
import api, { formatApiError } from "@/lib/api";
import { ExternalLink, Pencil, X, Loader2 } from "lucide-react";

/**
 * Bouton Preview de l'en-tete : ouvre l'application du projet en cours de dev
 * (URL propre au projet). Si l'URL n'est pas definie, propose de la saisir.
 */
export const PreviewButton = ({ project, previewUrl, onSaved }) => {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(previewUrl || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!project) return null;

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.put(`/workspace/projects/${project}`, {
        preview_url: url.trim(),
      });
      onSaved?.(data.preview_url);
      setOpen(false);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="flex items-center">
        {previewUrl ? (
          <a
            href={previewUrl}
            target="_blank"
            rel="noreferrer"
            title={`Ouvrir ${project} — ${previewUrl}`}
            className="flex items-center gap-1.5 border-2 border-white/20 px-2 py-1 text-[10px] font-mono uppercase tracking-[0.15em] text-gray-400 hover:border-[#05d9e8] hover:text-[#05d9e8] transition-colors"
            data-testid="preview-btn"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Preview</span>
          </a>
        ) : (
          <button
            type="button"
            onClick={() => setOpen(true)}
            title={`Definir l'URL de preview de ${project}`}
            className="flex items-center gap-1.5 border-2 border-dashed border-white/20 px-2 py-1 text-[10px] font-mono uppercase tracking-[0.15em] text-gray-500 hover:border-[#ffd700] hover:text-[#ffd700] transition-colors"
            data-testid="preview-setup-btn"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Preview</span>
          </button>
        )}
        {previewUrl && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            title="Modifier l'URL de preview"
            className="ml-1 p-1 text-gray-600 hover:text-[#ffd700]"
            data-testid="preview-edit-btn"
          >
            <Pencil className="w-3 h-3" />
          </button>
        )}
      </div>

      {open && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
          data-testid="preview-dialog"
        >
          <div className="w-full max-w-md border-2 border-white/20 bg-[#0a0a0a] shadow-[8px_8px_0_0_#05d9e8]">
            <div className="flex items-center justify-between border-b-2 border-white/10 px-4 py-3">
              <span className="font-heading font-black uppercase tracking-tighter text-sm">
                Preview — {project}
              </span>
              <button
                onClick={() => setOpen(false)}
                className="text-gray-500 hover:text-[#ff2a6d]"
                data-testid="preview-dialog-close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 space-y-3">
              <label className="block space-y-1">
                <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-bold">
                  URL de l'application en dev
                </span>
                <input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && save()}
                  placeholder="http://localhost:5173 ou https://projet.domaine.fr"
                  className="w-full bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs"
                  data-testid="preview-url-input"
                />
              </label>
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
                onClick={() => setOpen(false)}
                className="btn-ghost border-2 border-white/20 text-xs"
              >
                Annuler
              </button>
              <button
                onClick={save}
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
