import { useState } from "react";
import { ChevronLeft, Loader2, RefreshCw, Star } from "lucide-react";
import api, { formatApiError } from "@/lib/api";
import { useTickets } from "@/hooks/useTickets";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import MarkdownBody from "@/components/MarkdownBody";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

const COLUMNS = [
  { key: "prevision", label: "À prévoir", color: "#ffd700" },
  { key: "en_cours", label: "En cours", color: "#05d9e8" },
  { key: "finis", label: "Terminés", color: "#39ff14" },
];

const LABELS = Object.fromEntries(COLUMNS.map((c) => [c.key, c.label]));

// Board kanban. Etat = dossier de PROJETS_AMELIORATIONS/. Drag & drop natif + confirmation (R4).
export default function TicketsBoard({ open, onOpenChange }) {
  const { tickets, loading, error, reload, moveTicket } = useTickets(open);
  const [detail, setDetail] = useState(null); // { ticket, content, loading, error }
  const [drag, setDrag] = useState(null); // { slug, from }
  const [overCol, setOverCol] = useState(null);
  const [pending, setPending] = useState(null); // { ticket, from, to }
  const [moving, setMoving] = useState(false);

  const onDrop = (to) => {
    setOverCol(null);
    const d = drag;
    setDrag(null);
    if (!d || d.from === to) return;
    const ticket = (tickets[d.from] || []).find((t) => t.slug === d.slug);
    if (ticket) setPending({ ticket, from: d.from, to });
  };

  const confirmMove = async () => {
    if (!pending) return;
    setMoving(true);
    await moveTicket(pending.ticket.slug, pending.from, pending.to);
    setMoving(false);
    setPending(null);
  };

  const openTicket = async (ticket) => {
    setDetail({ ticket, content: "", loading: true, error: null });
    try {
      const { data } = await api.get(
        `/tickets/${ticket.etat}/${encodeURIComponent(ticket.slug)}/content`
      );
      setDetail({ ticket, content: data.content, loading: false, error: null });
    } catch (err) {
      setDetail({ ticket, content: "", loading: false, error: formatApiError(err) });
    }
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o) setDetail(null);
        onOpenChange(o);
      }}
    >
      <SheetContent
        side="right"
        className="w-full sm:max-w-5xl bg-[#0a0a0f] border-l-2 border-white/10 text-white overflow-y-auto"
        data-testid="tickets-board"
      >
        <SheetHeader>
          <SheetTitle className="font-heading font-black tracking-tighter text-white flex items-center gap-2">
            Chantiers
            <button
              type="button"
              onClick={reload}
              className="btn-ghost"
              title="Rafraîchir"
              aria-label="Rafraîchir les tickets"
              data-testid="tickets-reload-btn"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </SheetTitle>
          <SheetDescription className="text-xs font-mono text-gray-500">
            PROJETS_AMELIORATIONS/ — glisser une carte pour changer d&apos;état
          </SheetDescription>
        </SheetHeader>

        {error && (
          <div className="mt-4 text-sm text-[#ff2a6d] font-mono" role="alert">
            {error}
          </div>
        )}

        {detail ? (
          <div className="mt-4" data-testid="ticket-detail">
            <button
              type="button"
              onClick={() => setDetail(null)}
              className="btn-ghost flex items-center gap-1 text-xs font-mono mb-3"
              data-testid="ticket-detail-back"
            >
              <ChevronLeft className="w-4 h-4" /> retour
            </button>
            <div className="text-[10px] font-mono text-gray-500 mb-2">
              {detail.ticket.etat} / {detail.ticket.slug}
            </div>
            {detail.loading ? (
              <Loader2 className="w-5 h-5 animate-spin text-gray-500" />
            ) : detail.error ? (
              <div className="text-sm text-[#ff2a6d] font-mono" role="alert">
                {detail.error}
              </div>
            ) : (
              <div className="md-body text-sm">
                <MarkdownBody>{detail.content}</MarkdownBody>
              </div>
            )}
          </div>
        ) : (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
            {COLUMNS.map((col) => {
              const list = tickets[col.key] || [];
              return (
                <section
                  key={col.key}
                  data-testid={`tickets-col-${col.key}`}
                  onDragOver={(e) => {
                    if (!drag) return;
                    e.preventDefault();
                    setOverCol(col.key);
                  }}
                  onDragLeave={() => setOverCol((c) => (c === col.key ? null : c))}
                  onDrop={(e) => {
                    e.preventDefault();
                    onDrop(col.key);
                  }}
                  className={`p-1 transition-colors ${
                    overCol === col.key && drag?.from !== col.key
                      ? "bg-white/[0.06] outline-dashed outline-2"
                      : ""
                  }`}
                  style={overCol === col.key ? { outlineColor: col.color } : undefined}
                >
                  <h3
                    className="text-[10px] uppercase tracking-[0.3em] font-bold mb-2 flex items-center justify-between border-b-2 pb-1"
                    style={{ color: col.color, borderColor: col.color }}
                  >
                    <span>{col.label}</span>
                    <span>{list.length}</span>
                  </h3>
                  <div className="flex flex-col gap-2">
                    {list.length === 0 && !loading && (
                      <div className="text-xs font-mono text-gray-600">vide</div>
                    )}
                    {list.map((t) => (
                      <button
                        key={t.slug}
                        type="button"
                        draggable
                        onDragStart={() => setDrag({ slug: t.slug, from: col.key })}
                        onDragEnd={() => {
                          setDrag(null);
                          setOverCol(null);
                        }}
                        onClick={() => openTicket(t)}
                        className="text-left border-2 border-white/10 hover:border-white/40 bg-white/[0.02] p-3 transition-colors"
                        data-testid={`ticket-card-${t.slug}`}
                      >
                        <div className="font-bold text-sm leading-snug">{t.titre}</div>
                        <div className="text-[10px] font-mono text-gray-500 mt-1 flex items-center gap-2">
                          <span className="truncate">{t.slug}</span>
                          {t.has_ameliorations && (
                            <span className="flex items-center gap-0.5 text-[#ffd700] flex-shrink-0">
                              <Star className="w-3 h-3" /> améliorations
                            </span>
                          )}
                        </div>
                      </button>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </SheetContent>

      <AlertDialog open={!!pending} onOpenChange={(o) => !o && setPending(null)}>
        <AlertDialogContent data-testid="ticket-move-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>Déplacer ce chantier ?</AlertDialogTitle>
            <AlertDialogDescription className="font-mono text-xs">
              {pending && (
                <>
                  « {pending.ticket.titre} »<br />
                  {LABELS[pending.from]} → {LABELS[pending.to]}
                  <br />
                  Le dossier sera déplacé sur le disque.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={moving}>Annuler</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                confirmMove();
              }}
              disabled={moving}
              data-testid="ticket-move-confirm-btn"
            >
              {moving ? "Déplacement…" : "Déplacer"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sheet>
  );
}
