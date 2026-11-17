import { useCallback, useEffect, useState } from "react";
import api, { formatApiError } from "@/lib/api";

const EMPTY = { prevision: [], en_cours: [], finis: [] };

// Board tickets : lecture de PROJETS_AMELIORATIONS/ via GET /api/tickets.
// enabled=false : aucun fetch (panneau fermé).
export function useTickets(enabled = true) {
  const [tickets, setTickets] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/tickets");
      setTickets({ ...EMPTY, ...data });
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (enabled) reload();
  }, [enabled, reload]);

  // La route renvoie la liste relue sur le FS après le move (R6) : pas de refetch.
  const moveTicket = useCallback(async (slug, fromState, toState) => {
    setError(null);
    try {
      const { data } = await api.post(`/tickets/${encodeURIComponent(slug)}/move`, {
        from_state: fromState,
        to_state: toState,
      });
      setTickets({ ...EMPTY, ...data });
      return true;
    } catch (err) {
      setError(formatApiError(err));
      return false;
    }
  }, []);

  return { tickets, loading, error, reload, moveTicket };
}
