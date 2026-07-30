import { useCallback, useRef, useState } from "react";

import { transcribeAudio } from "@/lib/api";

/**
 * Record with MediaRecorder, transcribe on our own backend.
 *
 * Replaces the previous Web Speech API implementation, which streamed audio to
 * Google's servers and therefore could not work on the air-gapped deployment.
 * `stopRecording()` resolves with the transcript so the caller decides what to
 * do with it -- we never auto-send (WER on real speech is 5-15%).
 */
export function useVoiceInput() {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const isSupported =
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";

  const startRecording = useCallback(async () => {
    if (!isSupported || isRecording) return;
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.start();
      recorderRef.current = recorder;
      setIsRecording(true);
    } catch {
      // Permission denied, or no input device.
      setError("Microphone blocked — allow access in your browser settings.");
    }
  }, [isSupported, isRecording]);

  const stopRecording = useCallback(async (): Promise<string> => {
    const recorder = recorderRef.current;
    if (!recorder) return "";

    const blob = await new Promise<Blob>((resolve) => {
      recorder.onstop = () =>
        resolve(new Blob(chunksRef.current, { type: recorder.mimeType }));
      recorder.stop();
    });
    // Release the mic indicator in the browser chrome.
    recorder.stream.getTracks().forEach((t) => t.stop());
    recorderRef.current = null;
    setIsRecording(false);

    if (blob.size === 0) return "";
    setIsTranscribing(true);
    try {
      const { text } = await transcribeAudio(blob);
      return text;
    } catch {
      setError("Transcription failed — try again.");
      return "";
    } finally {
      setIsTranscribing(false);
    }
  }, []);

  return {
    isRecording,
    isTranscribing,
    isSupported,
    error,
    startRecording,
    stopRecording,
  };
}
