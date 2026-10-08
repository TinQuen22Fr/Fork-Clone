/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 * Logiciel libre sous GNU GPL v3 — voir LICENSE.
 *
 * Lecteur audio unique pour la synthese vocale serveur (/api/tts).
 * Un seul flux a la fois, arret immediat garanti.
 */
import api from "@/lib/api";

const VOICE_KEY = "forge_tts_voice";

let audio = null;
let objectUrl = null;
let controller = null;
let currentId = null;
let onStopCb = null;

const cleanupUrl = () => {
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl);
    objectUrl = null;
  }
};

export const stopSpeech = () => {
  if (controller) {
    controller.abort();
    controller = null;
  }
  if (audio) {
    audio.pause();
    try {
      audio.currentTime = 0;
    } catch (_) {
      /* certains navigateurs refusent avant chargement */
    }
    audio.removeAttribute("src");
    audio.load();
  }
  cleanupUrl();
  currentId = null;
  if (onStopCb) {
    const cb = onStopCb;
    onStopCb = null;
    cb();
  }
};

export const getSpeakingId = () => currentId;

export const getVoice = () => localStorage.getItem(VOICE_KEY) || undefined;
export const setVoice = (v) => localStorage.setItem(VOICE_KEY, v);

/**
 * Avec responseType "blob", le detail d'erreur du serveur arrive sous forme
 * de Blob : on le relit pour afficher la vraie cause (module absent, reseau...).
 */
const explainTtsError = async (err) => {
  if (err?.name === "CanceledError" || err?.code === "ERR_CANCELED") return err;
  let detail = "";
  const data = err?.response?.data;
  try {
    if (data instanceof Blob) {
      const txt = await data.text();
      try {
        const j = JSON.parse(txt);
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
      } catch (_) {
        detail = txt.slice(0, 200);
      }
    } else if (data && typeof data.detail === "string") {
      detail = data.detail;
    }
  } catch (_) {
    /* lecture impossible : on garde le message generique */
  }
  const status = err?.response?.status;
  let msg;
  if (!err?.response) msg = "serveur injoignable (réseau)";
  else if (status === 401) msg = "session expirée, reconnecte-toi";
  else msg = detail || `erreur ${status}`;
  const e = new Error(msg);
  e.ttsDetail = msg;
  return e;
};

/**
 * Lecture progressive : `input` est un texte ou une liste de morceaux
 * (paragraphes). Le 1er morceau est synthetise et joue tout de suite ;
 * les 2 suivants sont precharges en tache de fond pendant la lecture.
 * speak() se termine des que le 1er morceau demarre. Les erreurs des
 * morceaux suivants sont remontees via onError (la lecture s'arrete).
 */
export const speak = async (input, { id = "adhoc", voice, onEnd, onError } = {}) => {
  stopSpeech();
  const chunks = (Array.isArray(input) ? input : [input]).filter(
    (c) => typeof c === "string" && c.trim()
  );
  if (!chunks.length) return;

  currentId = id;
  onStopCb = onEnd || null;
  controller = new AbortController();
  const signal = controller.signal;
  const v = voice || getVoice();

  const pending = new Map();
  const prefetch = (i) => {
    if (i >= chunks.length || pending.has(i)) return;
    const p = api
      .post("/tts", { text: chunks[i], voice: v }, { responseType: "blob", signal })
      .then((r) => r.data);
    p.catch(() => {}); // evite les rejets non geres si on arrete avant usage
    pending.set(i, p);
  };

  if (!audio) audio = new Audio();
  const a = audio;

  const playChunk = async (i, first) => {
    prefetch(i);
    let blob;
    try {
      blob = await pending.get(i);
    } catch (err) {
      const e = await explainTtsError(err);
      if (currentId !== id) return; // arret volontaire
      if (first) {
        controller = null;
        throw e;
      }
      stopSpeech();
      if (onError) onError(e);
      return;
    }
    pending.delete(i);
    if (currentId !== id) return; // arret demande pendant la synthese

    cleanupUrl();
    objectUrl = URL.createObjectURL(blob);
    a.src = objectUrl;
    a.onended = () => {
      if (currentId !== id) return;
      if (i + 1 < chunks.length) {
        playChunk(i + 1, false).catch((err) => {
          if (currentId !== id) return;
          stopSpeech();
          if (onError) onError(err);
        });
      } else stopSpeech();
    };
    a.onerror = () => {
      if (currentId !== id) return;
      stopSpeech();
    };
    prefetch(i + 1);
    prefetch(i + 2);
    await a.play();
  };

  await playChunk(0, true);
};
