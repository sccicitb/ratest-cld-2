import { useCallback, useEffect, useState } from "react";

export function useVoiceSynthesis() {
  const isSupported =
    typeof window !== "undefined" && "speechSynthesis" in window;
  const [isSpeaking, setIsSpeaking] = useState(false);

  const stop = useCallback(() => {
    if (!isSupported) return;
    window.speechSynthesis.cancel();
    setIsSpeaking(false);
  }, [isSupported]);

  const speak = useCallback(
    (text: string) => {
      if (!isSupported || !text.trim()) return;
      window.speechSynthesis.cancel();
      // Strip markdown so the TTS reads clean prose.
      const clean = text
        .replace(/```[\s\S]*?```/g, " code block ")
        .replace(/[*_`#>|]/g, "")
        .replace(/\[(.*?)\]\(.*?\)/g, "$1");
      const utterance = new SpeechSynthesisUtterance(clean);
      utterance.onend = () => setIsSpeaking(false);
      utterance.onerror = () => setIsSpeaking(false);
      setIsSpeaking(true);
      window.speechSynthesis.speak(utterance);
    },
    [isSupported],
  );

  useEffect(() => {
    return () => {
      if (isSupported) window.speechSynthesis.cancel();
    };
  }, [isSupported]);

  return { isSpeaking, speak, stop, isSupported };
}
