import axios from "axios";

// Vide par défaut : les appels partent en relatif sur /api (proxy Vite en dev,
// Nginx en prod — même origine dans les deux cas). Ne renseigner
// VITE_BACKEND_URL que si l'API vit sur un domaine séparé.
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "";
export const API = `${BACKEND_URL}/api`;

const api = axios.create({
  baseURL: API,
  withCredentials: true,
});

// Also attach Bearer token if stored (fallback for cookie issues)
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("auth_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export default api;

/**
 * Consomme un flux SSE renvoye par un POST (EventSource ne gere pas POST).
 * Appelle onEvent({event, data}) pour chaque evenement recu.
 */
export async function postSSE(path, formData, { signal, onEvent }) {
  const headers = {};
  const token = localStorage.getItem("auth_token");
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(`${API}${path}`, {
    method: "POST",
    body: formData,
    credentials: "include",
    headers,
    signal,
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
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try {
        onEvent({ event, data: JSON.parse(dataLines.join("\n")) });
      } catch (_) {
        // fragment illisible : on l'ignore plutot que de casser le flux
      }
    }
  }
}

export function formatApiError(err) {
  const detail = err?.response?.data?.detail;
  if (detail == null) return err?.message || "Something went wrong.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}
