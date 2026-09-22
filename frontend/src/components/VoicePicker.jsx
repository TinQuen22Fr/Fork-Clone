/*
 * Claude Unchained Forge — Copyright (C) 2026 Quentin Dumont
 * Logiciel libre sous GNU GPL v3 — voir LICENSE.
 */
import React, { useEffect, useState } from "react";
import api from "@/lib/api";
import { speak, stopSpeech, getVoice, setVoice as persistVoice } from "@/lib/tts";
import { Volume2, Loader2 } from "lucide-react";

// Voix francaises GRATUITES de FreeTTS (les « Signature » comme Celeste sont
// PRO et renvoient un 402).
const PINNED = [
  "fr-FR-DeniseNeural",
  "fr-FR-HenriNeural",
  "fr-FR-VivienneMultilingualNeural",
  "fr-FR-RemyMultilingualNeural",
  "fr-CA-SylvieNeural",
  "fr-CA-AntoineNeural",
  "fr-BE-CharlineNeural",
  "fr-CH-ArianeNeural",
];

export const VoicePicker = () => {
  const [open, setOpen] = useState(false);
  const [voices, setVoices] = useState([]);
  const [provider, setProvider] = useState("");
  const [voice, setVoice] = useState(() => getVoice() || "");
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (!open || voices.length) return;
    setLoading(true);
    api
      .get("/tts/voices", { params: { locale: "fr" } })
      .then(({ data }) => {
        const names = (data.voices || []).map((v) => v.short_name);
        const merged = [...new Set([...PINNED, ...names])];
        setVoices(merged);
        setProvider(data.provider || "");
        if (!voice) setVoice(data.default || merged[0] || "");
      })
      .catch(() => setVoices(PINNED))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const pick = (v) => {
    setVoice(v);
    persistVoice(v);
    stopSpeech();
  };

  const test = async () => {
    setTesting(true);
    try {
      await speak("Bonjour, je suis la voix de la Forge.", {
        id: "preview",
        voice: voice || undefined,
        onEnd: () => setTesting(false),
      });
    } catch (_) {
      setTesting(false);
    }
  };

  return (
    <div className="relative flex-shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`btn-ghost border-2 ${
          open
            ? "border-[#05d9e8] text-[#05d9e8]"
            : "border-white/20 hover:border-[#05d9e8] hover:text-[#05d9e8]"
        }`}
        title={`Voix de lecture : ${voice || "par défaut"}`}
        data-testid="voice-picker-btn"
      >
        <Volume2 className="w-5 h-5" />
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-30"
            onClick={() => setOpen(false)}
            data-testid="voice-picker-backdrop"
          />
          <div
            className="absolute z-40 bottom-full mb-2 left-0 w-72 border-2 border-white/20 bg-[#0a0a0a] shadow-[6px_6px_0_0_#05d9e8] p-3 space-y-2"
            data-testid="voice-picker-menu"
          >
            <div className="text-[10px] uppercase tracking-[0.2em] text-gray-500 font-bold">
              Voix de lecture {provider && `· ${provider}`}
            </div>
            {loading ? (
              <div className="flex items-center gap-2 text-xs text-gray-500 font-mono">
                <Loader2 className="w-3 h-3 animate-spin" /> chargement…
              </div>
            ) : (
              <select
                value={voice}
                onChange={(e) => pick(e.target.value)}
                className="w-full bg-black/50 border-2 border-white/20 focus:border-[#05d9e8] outline-none px-2 py-2 font-mono text-xs"
                data-testid="voice-select"
              >
                {voices.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              onClick={test}
              disabled={testing}
              className="w-full btn-ghost border-2 border-white/20 hover:border-[#ffd700] hover:text-[#ffd700] text-xs flex items-center justify-center gap-2"
              data-testid="voice-test-btn"
            >
              {testing ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Volume2 className="w-3 h-3" />
              )}
              Écouter un extrait
            </button>
          </div>
        </>
      )}
    </div>
  );
};

export default VoicePicker;
