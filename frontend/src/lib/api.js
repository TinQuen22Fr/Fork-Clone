import axios from "axios";

// Vide par défaut : les appels partent en relatif sur /api (proxy Vite en dev,
// Nginx en prod — même origine dans les deux cas). Ne renseigner
// VITE_BACKEND_URL que si l'API vit sur un domaine séparé.
//
// Cas dogfooding/self-hosting : quand cette appli tourne en preview en tant
// que sous-projet de la Forge, le runtime de preview injecte lui-même
// VITE_BACKEND_URL="/api" (ou une URL qui se termine déjà par /api). Dans ce
// cas il ne faut PAS re-concatener /api, sinon on obtient /api/api (404) et
// l'auth + les conversations ne chargent plus.
const RAW_BACKEND_URL = (import.meta.env.VITE_BACKEND_URL || "").trim();
const BACKEND_URL = RAW_BACKEND_URL.replace(/\/+$/, ""); // sans slash final
const ALREADY_HAS_API = /\/api$/i.test(BACKEND_URL);
export const API = ALREADY_HAS_API ? BACKEND_URL : `${BACKEND_URL}/api`;

// Nom de la cle localStorage du token, paramétrable pour isoler les sessions
// entre une instance hote et une instance sandboxee qui partageraient le
// meme domaine (voir aussi COOKIE_NAME cote backend).
export const AUTH_TOKEN_KEY = import.meta.env.VITE_AUTH_TOKEN_KEY || "auth_token";

// Retire les doubles slashes eventuels (hors "://") introduits par la
// concatenation base + chemin.
function cleanDoubleSlashes(url) {
  return url.replace(/([^:]\/)\/+/g, "$1");
}

const api = axios.create({
  baseURL: API,
  withCredentials: true,
  // Les donnees sont toujours fraiches cote serveur : on interdit tout cache
  // navigateur/proxy qui renverrait un corps tronque ou perime.
  headers: { "Cache-Control": "no-store" },
});

// Also attach Bearer token if stored (fallback for cookie issues)
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  if (config.url) {
    config.url = cleanDoubleSlashes(config.url);
  }
  return config;
});

// Retry automatique : au rechargement force (Ctrl+Shift+R) ou lors d'un
// reveil d'onglet, le navigateur annule parfois les requetes en vol, ce qui
// produit un "Network Error" axios alors que le serveur va tres bien. On
// rejoue UNE fois, uniquement les requetes idempotentes (GET), apres un court
// delai. Aucun retry sur les POST/PATCH/DELETE : on ne veut jamais risquer un
// doublon (envoi de message, suppression...).
api.interceptors.response.use(
  (response) => response,
  async (err) => {
    const cfg = err?.config || {};
    const isNetwork =
      err?.code === "ERR_NETWORK" ||
      err?.message === "Network Error" ||
      (typeof err?.message === "string" && err.message.includes("Failed to fetch"));
    const method = (cfg.method || "get").toLowerCase();
    const retriable = isNetwork && method === "get" && !cfg.__forgeRetried;
    // Une requete annulee volontairement (AbortController) ne doit pas etre rejouee.
    if (retriable && err?.name !== "CanceledError" && err?.code !== "ERR_CANCELED") {
      cfg.__forgeRetried = true;
      await new Promise((r) => setTimeout(r, 600));
      return api(cfg);
    }
    return Promise.reject(err);
  }
);

export default api;

// Erreur dediee : le flux SSE a ete coupe (proxy, reseau, onglet en veille).
// Les appelants peuvent la reconnaitre pour ne PAS traiter ca comme un echec
// metier (le backend a deja persiste ce qu'il a produit).
export const STREAM_INTERRUPTED = "STREAM_INTERRUPTED";

// Delai max sans aucun paquet (evenement ou ping) avant de couper le flux.
const SSE_WATCHDOG_MS = 10000;

/**
 * Consomme un flux SSE renvoye par un POST (EventSource ne gere pas POST).
 * Appelle onEvent({event, data}) pour chaque evenement recu.
 *
 * Robustesse : si le corps du flux est tronque en plein vol (ce qui produit
 * cote navigateur le message brut "Error in input stream"), on leve une erreur
 * STREAM_INTERRUPTED identifiable, apres avoir quand meme traite tous les
 * evenements deja recus.
 */
export async function postSSE(path, formData, { signal, onEvent }) {
  const headers = {};
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(cleanDoubleSlashes(`${API}${path}`), {
    method: "POST",
    body: formData,
    credentials: "include",
    headers,
    signal,
    // Empeche tout cache intermediaire de servir un flux tronque.
    cache: "no-store",
  });

  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`;
    try {
      const j = await resp.json();
      if (j?.detail) detail = typeof j.detail === "string" ? j.detail : detail;
    } catch (_) {
      // reponse non JSON : on garde le code HTTP
    }
    throw new Error(detail);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushBlock = (block) => {
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return;
    try {
      onEvent({ event, data: JSON.parse(dataLines.join("\n")) });
    } catch (_) {
      // fragment illisible : on l'ignore plutot que de casser le flux
    }
  };

  // Watchdog : le backend emet un ping SSE toutes les 4 s. Si plus aucun octet
  // (evenement OU ping) n'arrive pendant SSE_WATCHDOG_MS, la connexion est
  // consideree comme coupee : on annule la lecture et on leve STREAM_INTERRUPTED.
  let timedOut = false;
  let watchdog = null;
  const armWatchdog = () => {
    clearTimeout(watchdog);
    watchdog = setTimeout(() => {
      timedOut = true;
      reader.cancel().catch(() => {});
    }, SSE_WATCHDOG_MS);
  };

  try {
    armWatchdog();
    while (true) {
      const { value, done } = await reader.read();
      if (timedOut) throw new Error("SSE watchdog: aucun paquet depuis 10 s");
      if (done) break;
      armWatchdog();
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        flushBlock(block);
      }
    }
    // Dernier bloc eventuel non termine par un double saut de ligne.
    if (buffer.trim()) flushBlock(buffer);
  } catch (streamErr) {
    clearTimeout(watchdog);
    // Flux coupe en plein vol : on remonte une erreur identifiable pour que
    // l'appelant affiche un message lisible et resynchronise, au lieu de
    // laisser remonter le "Error in input stream" brut du navigateur.
    const e = new Error(STREAM_INTERRUPTED);
    e.cause = streamErr;
    e.isStreamInterrupted = true;
    e.watchdog = timedOut;
    throw e;
  } finally {
    clearTimeout(watchdog);
  }
}

export function formatApiError(err) {
  const detail = err?.response?.data?.detail;
  if (detail != null) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail
        .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
        .filter(Boolean)
        .join(" ");
    if (detail && typeof detail.msg === "string") return detail.msg;
    return String(detail);
  }
  // Erreurs reseau brutes (axios "Network Error", fetch "Failed to fetch") :
  // message explicite en francais plutot que l'anglicisme cryptique.
  if (
    err?.message === "Network Error" ||
    err?.code === "ERR_NETWORK" ||
    err?.message === "Failed to fetch"
  ) {
    return "Réseau indisponible — le serveur ne répond pas. Vérifie ta connexion puis réessaie.";
  }
  return err?.message || "Une erreur est survenue.";
}
