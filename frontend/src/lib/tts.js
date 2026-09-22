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

/** Lit un texte via /api/tts. Renvoie une erreur si la synthese echoue. */
export const speak = async (text, { id = "adhoc", voice, onEnd } = {}) => {
  stopSpeech();
  if (!text) return;

  currentId = id;
  onStopCb = onEnd || null;
  controller = new AbortController();

  const res = await api.post(
    "/tts",
    { text, voice: voice || getVoice() },
    { responseType: "blob", signal: controller.signal }
  );
  controller = null;

  if (currentId !== id) return; // arret demande pendant la synthese

  objectUrl = URL.createObjectURL(res.data);
  if (!audio) audio = new Audio();
  audio.src = objectUrl;
  audio.onended = () => stopSpeech();
  audio.onerror = () => stopSpeech();
  await audio.play();
};
