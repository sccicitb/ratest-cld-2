import { useCallback, useEffect, useRef, useState } from "react";

import { speakText } from "@/lib/api";

/**
 * Read an answer aloud using our own TTS sidecar.
 *
 * Replaces the previous `window.speechSynthesis` implementation, which could
 * not work on the air-gapped deployment and gave every user whatever voice
 * their OS happened to ship. There is deliberately no browser fallback:
 * falling back would reintroduce exactly the inconsistency this removes.
 *
 * The exported shape is unchanged, so MessageBubble needs no structural edit.
 */
export function useVoiceSynthesis() {
  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  // Bumped on every stop/new request so a slow synthesis that resolves after
  // the user moved on does not start playing over the top.
  const runRef = useRef(0);

  const isSupported = typeof window !== "undefined" && typeof Audio !== "undefined";

  const stop = useCallback(() => {
    runRef.current += 1;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setIsSpeaking(false);
  }, []);

  const speak = useCallback(
    async (text: string) => {
      if (!isSupported || !text.trim()) return;
      stop();
      const run = runRef.current;
      // Strip markdown so the engine reads prose, not syntax.
      const clean = text
        .replace(/```[\s\S]*?```/g, " code block ")
        .replace(/[*_`#>|]/g, "")
        .replace(/\[(.*?)\]\(.*?\)/g, "$1");
      setIsSpeaking(true);
      try {
        const blob = await speakText(clean);
        if (runRef.current !== run) return; // stopped while synthesizing
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => {
          if (runRef.current === run) stop();
        };
        audio.onerror = () => {
          if (runRef.current === run) stop();
        };
        await audio.play();
      } catch {
        if (runRef.current === run) stop();
      }
    },
    [isSupported, stop],
  );

  useEffect(() => stop, [stop]);

  return { isSpeaking, speak, stop, isSupported };
}
