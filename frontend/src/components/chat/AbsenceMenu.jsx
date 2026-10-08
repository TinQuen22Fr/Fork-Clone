import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

/**
 * AbsenceMenu - menu deroulant discret (header) listant les actions executees
 * par le mode autonome pendant l'absence de l'utilisateur. Remplace l'ancienne
 * banniere bloquante. Rien ne s'ouvre tout seul : chevron pour afficher/masquer.
 */
export default function AbsenceMenu({ runningTurns = [], statusTurns = [], onDismiss }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const running = runningTurns.length > 0;
  const count = running ? runningTurns.length : statusTurns.length;

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("touchstart", onDoc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("touchstart", onDoc);
    };
  }, [open]);

  if (count === 0) return null;

  return (
    <div className="relative flex-shrink-0" ref={ref} data-testid="resume-banner">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 px-2 py-1 text-[11px] font-mono text-gray-400 hover:text-[#05d9e8] bg-transparent border border-white/10 rounded touch-manipulation"
        aria-expanded={open}
        aria-label="Actions du mode autonome pendant l'absence"
        data-testid="absence-menu-toggle"
      >
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            running ? "bg-sky-400 animate-pulse" : "bg-emerald-400"
          }`}
        />
        <span className="hidden sm:inline">{running ? "En cours" : "Absence"}</span>
        <span className="text-[#05d9e8]">{count}</span>
        <ChevronDown className={`w-3 h-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 w-[min(24rem,calc(100vw-1.5rem))] bg-[var(--bg-sidebar)] border border-white/15 rounded-lg shadow-xl p-3 space-y-2">
          <div className="flex items-center justify-between gap-3">
            <div className="font-heading font-bold text-xs tracking-tight">
              {running
                ? "Mode autonome — génération en cours côté serveur"
                : "Reprise — travail pendant ton absence"}
            </div>
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                onDismiss && onDismiss();
              }}
              className="text-[10px] text-gray-400 hover:text-white underline"
            >
              Marquer comme vu
            </button>
          </div>

          {running ? (
            <ul className="space-y-1 text-[12px] text-gray-200">
              {runningTurns.slice(0, 5).map((r) => (
                <li key={`run-${r.conversation_id}`} className="flex items-start gap-2">
                  <span className="inline-block w-2 h-2 mt-1 flex-shrink-0 rounded-full bg-sky-400 animate-pulse" />
                  <span>
                    <span className="text-white font-medium">En cours</span>
                    {" — "}
                    {r.tool_count > 0 ? `${r.tool_count} outil(s) exécuté(s)…` : "réflexion…"}
                    {" (déconnecté, ça continue toute seule)"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
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
    </div>
  );
}
