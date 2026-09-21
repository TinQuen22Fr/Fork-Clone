/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 * Logiciel libre sous GNU GPL v3 — voir LICENSE.
 */
import React, { useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";
import {
  Github,
  Loader2,
  X,
  CheckCircle2,
  AlertTriangle,
  GitBranch,
  FolderGit2,
  UploadCloud,
} from "lucide-react";

export const GithubSaveDialog = ({ onClose, conversationId }) => {
  const [status, setStatus] = useState(null);
  const [token, setToken] = useState("");
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState("");
  const [repos, setRepos] = useState([]);
  const [repo, setRepo] = useState("");
  const [branches, setBranches] = useState([]);
  const [branch, setBranch] = useState("");
  const [newBranch, setNewBranch] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const loadStatus = async (proj) => {
    try {
      const { data } = await api.get("/github/status", {
        params: {
          ...(proj ? { project: proj } : {}),
          ...(conversationId ? { conversation_id: conversationId } : {}),
        },
      });
      setStatus(data);
      setProjects(data.projects || []);
      if (data.project) setProject(data.project);
      if (data.configured && !repos.length) loadRepos();
    } catch (e) {
      setError(formatApiError(e));
    }
  };

  const loadRepos = async () => {
    setBusy(true);
    setError("");
    try {
      const { data } = await api.get("/github/repos");
      setRepos(data);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pickRepo = async (full) => {
    setRepo(full);
    setBranch("");
    setBranches([]);
    if (!full) return;
    setBusy(true);
    try {
      const { data } = await api.get("/github/branches", { params: { repo: full } });
      setBranches(data);
      const def = repos.find((r) => r.full_name === full)?.default_branch;
      setBranch(data.includes(def) ? def : data[0] || "");
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const saveToken = async () => {
    if (!token.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.post("/github/token", { token: token.trim() });
      setToken("");
      await loadStatus();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const push = async () => {
    const target = newBranch.trim() || branch;
    if (!repo || !target) {
      setError("Choisis un dépôt et une branche.");
      return;
    }
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const { data } = await api.post("/github/push", {
        repo,
        branch: target,
        message: message.trim(),
        project,
        conversation_id: conversationId || null,
      });
      setResult(data);
      loadStatus();
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      data-testid="github-dialog"
    >
      <div className="w-full max-w-lg border-2 border-white/20 bg-[#0a0a0a] shadow-[8px_8px_0_0_#05d9e8] max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b-2 border-white/10 px-4 py-3">
          <div className="flex items-center gap-2 font-heading font-black uppercase tracking-tighter">
            <Github className="w-5 h-5 text-[#05d9e8]" />
            Enregistrer sur GitHub
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-[#ff2a6d]"
            data-testid="github-dialog-close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-4 text-sm">
          {status && (
            <div className="font-mono text-[11px] text-gray-500 space-y-1">
              <div data-testid="github-workspace">
                dossier cible :{" "}
                <span className="text-gray-300">
                  {status.workspace || "à choisir"}
                </span>
              </div>
              <div>
                jeton :{" "}
                <span className="text-gray-300">
                  {status.configured
                    ? `${status.login || "ok"} (${status.source === "ui" ? "interface" : ".env"})`
                    : "absent"}
                </span>
                {" · "}
                modifications locales :{" "}
                <span className="text-[#ffd700]">{status.changes}</span>
              </div>
            </div>
          )}

          {projects.length > 0 && (
            <label className="block space-y-1">
              <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-bold flex items-center gap-1">
                <FolderGit2 className="w-3 h-3" /> Projet ({status?.root})
              </span>
              <select
                value={project}
                onChange={(e) => {
                  setProject(e.target.value);
                  setResult(null);
                  setError("");
                  loadStatus(e.target.value);
                }}
                className="w-full bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs"
                data-testid="github-project-select"
              >
                <option value="">— choisir le dossier projet —</option>
                {projects.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
          )}

          {status && !status.configured && (
            <div className="space-y-2">
              <div className="flex items-start gap-2 text-xs text-[#ffd700]">
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>
                  Aucun jeton valide. Colle un token GitHub (portée <b>repo</b>) —
                  il est stocké côté serveur, jamais renvoyé au navigateur.
                </span>
              </div>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="ghp_..."
                  className="flex-1 bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs"
                  data-testid="github-token-input"
                />
                <button
                  onClick={saveToken}
                  disabled={busy || !token.trim()}
                  className="btn-primary px-3"
                  data-testid="github-token-save"
                >
                  OK
                </button>
              </div>
            </div>
          )}

          {status?.configured && (
            <>
              <label className="block space-y-1">
                <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-bold">
                  Dépôt
                </span>
                <select
                  value={repo}
                  onChange={(e) => pickRepo(e.target.value)}
                  className="w-full bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs"
                  data-testid="github-repo-select"
                >
                  <option value="">— choisir —</option>
                  {repos.map((r) => (
                    <option key={r.full_name} value={r.full_name}>
                      {r.full_name}
                      {r.private ? " (privé)" : ""}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block space-y-1">
                <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-bold">
                  Branche
                </span>
                <select
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  disabled={!branches.length}
                  className="w-full bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs disabled:opacity-40"
                  data-testid="github-branch-select"
                >
                  {branches.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block space-y-1">
                <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-bold flex items-center gap-1">
                  <GitBranch className="w-3 h-3" /> Nouvelle branche (optionnel)
                </span>
                <input
                  value={newBranch}
                  onChange={(e) => setNewBranch(e.target.value)}
                  placeholder="ex. forge-2026-09-21"
                  className="w-full bg-black/50 border-2 border-white/20 focus:border-[#ffd700] outline-none px-3 py-2 font-mono text-xs"
                  data-testid="github-new-branch-input"
                />
              </label>

              <label className="block space-y-1">
                <span className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-bold">
                  Message de commit
                </span>
                <input
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="Sauvegarde depuis la Forge"
                  className="w-full bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-3 py-2 font-mono text-xs"
                  data-testid="github-commit-message"
                />
              </label>
            </>
          )}

          {error && (
            <div
              className="border-2 border-[#ff2a6d]/60 bg-[#ff2a6d]/10 px-3 py-2 text-xs text-[#ff8bb0] font-mono break-words"
              data-testid="github-error"
            >
              {error}
            </div>
          )}

          {result && (
            <div
              className="border-2 border-[#05d9e8]/60 bg-[#05d9e8]/10 px-3 py-2 text-xs font-mono space-y-1"
              data-testid="github-result"
            >
              <div className="flex items-center gap-2 text-[#05d9e8]">
                <CheckCircle2 className="w-4 h-4" /> Poussé sur {result.repo} @{" "}
                {result.branch}
              </div>
              <div className="text-gray-400">
                {result.files_committed} fichier(s) · commit {result.commit || "—"}
              </div>
              <a
                href={result.url}
                target="_blank"
                rel="noreferrer"
                className="text-[#ffd700] hover:underline"
                data-testid="github-result-link"
              >
                Voir sur GitHub →
              </a>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t-2 border-white/10 px-4 py-3">
          <button
            onClick={onClose}
            className="btn-ghost border-2 border-white/20 text-xs"
            data-testid="github-cancel-btn"
          >
            Fermer
          </button>
          <button
            onClick={push}
            disabled={busy || !status?.configured || !repo || !status?.workspace}
            className="btn-primary flex items-center gap-2 text-xs disabled:opacity-40"
            data-testid="github-push-btn"
          >
            {busy ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <UploadCloud className="w-4 h-4" />
            )}
            Pousser
          </button>
        </div>
      </div>
    </div>
  );
};

export default GithubSaveDialog;
