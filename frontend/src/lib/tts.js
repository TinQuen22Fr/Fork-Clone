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

/** Lit un texte via /api/tts. Renvoie une erreur si la synthese echoue. */
export const speak = async (text, { id = "adhoc", voice, onEnd } = {}) => {
  stopSpeech();
  if (!text) return;

  currentId = id;
  onStopCb = onEnd || null;
  controller = new AbortController();

  let res;
  try {
    res = await api.post(
      "/tts",
      { text, voice: voice || getVoice() },
      { responseType: "blob", signal: controller.signal }
    );
  } catch (err) {
    controller = null;
    throw await explainTtsError(err);
  }
  controller = null;

  if (currentId !== id) return; // arret demande pendant la synthese

  objectUrl = URL.createObjectURL(res.data);
  if (!audio) audio = new Audio();
  audio.src = objectUrl;
  audio.onended = () => stopSpeech();
  audio.onerror = () => stopSpeech();
  await audio.play();
};
