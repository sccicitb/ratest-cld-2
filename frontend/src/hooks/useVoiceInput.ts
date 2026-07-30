import { useCallback, useRef, useState } from "react";

import { ApiError, transcribeAudio } from "@/lib/api";

/**
 * Record with MediaRecorder, transcribe on our own backend.
 *
 * Replaces the previous Web Speech API implementation, which streamed audio to
 * Google's servers and therefore could not work on the air-gapped deployment.
 * `stopRecording()` resolves with the transcript so the caller decides what to
 * do with it -- we never auto-send (WER on real speech is 5-15%).
 */

// Sequenced synchronously (not via React state, which only commits after a
// render) so a second click fired before the first async step resolves reads
// the *already-updated* value and no-ops instead of racing a second
// getUserMedia/MediaRecorder or a second recorder.stop().
type RecorderPhase = "idle" | "starting" | "recording" | "stopping";

function messageForTranscribeError(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.code) {
      case "audio_too_large":
        return "Recording is too long — try a shorter clip.";
      case "stt_unavailable":
        return "Speech-to-text is unavailable right now.";
      case "stt_timeout":
        return "Transcription timed out — try again.";
      case "stt_failed":
      default:
        return "Transcription failed — try again.";
    }
  }
  return "Transcription failed — try again.";
}

export function useVoiceInput() {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Sticky for the session once a getUserMedia call rejects (permission
  // denied, no device, etc.) -- distinct from `error`, which also carries
  // transient transcription failures that should NOT disable the button.
  const [permissionDenied, setPermissionDenied] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const phaseRef = useRef<RecorderPhase>("idle");

  const isSupported =
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";

  const startRecording = useCallback(async () => {
    if (!isSupported || permissionDenied || phaseRef.current !== "idle") return;
    // Claim the slot synchronously, before the first `await`, so a second
    // click that lands while getUserMedia is still pending sees "starting"
    // (not "idle") and returns immediately instead of opening a second stream.
    phaseRef.current = "starting";
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
      phaseRef.current = "recording";
      setIsRecording(true);
    } catch {
      // Permission denied, or no input device. Per spec (§6): show
      // "microphone blocked" with a hint, and keep the control disabled for
      // the rest of the session -- there is nothing to retry without a
      // browser-level permission change, which needs a reload to re-check.
      phaseRef.current = "idle";
      setPermissionDenied(true);
      setError(
        "Microphone blocked — allow access in your browser settings, then reload to try again.",
      );
    }
  }, [isSupported, permissionDenied]);

  const stopRecording = useCallback(async (): Promise<string> => {
    // Only a call that finds "recording" gets to stop it. A second click
    // fired while the first stop() is already in flight sees "stopping" (set
    // synchronously below, before any await) and no-ops instead of calling
    // .stop() on an already-stopping recorder, which throws synchronously.
    if (phaseRef.current !== "recording") return "";
    phaseRef.current = "stopping";

    const recorder = recorderRef.current;
    if (!recorder) {
      phaseRef.current = "idle";
      setIsRecording(false);
      return "";
    }

    let blob: Blob;
    try {
      blob = await new Promise<Blob>((resolve, reject) => {
        recorder.onstop = () =>
          resolve(new Blob(chunksRef.current, { type: recorder.mimeType }));
        try {
          recorder.stop();
        } catch (e) {
          // Recorder was already inactive -- nothing to transcribe, but the
          // outer `finally` below still releases the mic tracks.
          reject(e as Error);
        }
      });
    } catch {
      blob = new Blob([]);
    } finally {
      // Release the mic indicator in the browser chrome, no matter which
      // branch above ran -- a stream left open is worse than a lost clip.
      recorder.stream.getTracks().forEach((t) => t.stop());
      recorderRef.current = null;
      setIsRecording(false);
      phaseRef.current = "idle";
    }

    if (blob.size === 0) return "";
    setIsTranscribing(true);
    try {
      const { text } = await transcribeAudio(blob);
      return text;
    } catch (e) {
      setError(messageForTranscribeError(e));
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
    permissionDenied,
    startRecording,
    stopRecording,
  };
}
