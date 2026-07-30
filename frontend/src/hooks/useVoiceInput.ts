import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, transcribeAudio } from "@/lib/api";

/**
 * Record with MediaRecorder, transcribe on our own backend.
 *
 * Replaces the previous Web Speech API implementation, which streamed audio to
 * Google's servers and therefore could not work on the air-gapped deployment.
 * `stopRecording()` resolves with the transcript so the caller decides what to
 * do with it -- we never auto-send (WER on real speech is 5-15%).
 */

/**
 * Hard stop for a single recording, in seconds.
 *
 * Mirrors the sidecar's `STT_MAX_AUDIO_SECONDS` default (spec §4.1), which is
 * the authority: over-run is rejected there with `audio_too_long` whatever the
 * client does. Stopping here as well is not redundant, it is the difference
 * between "the mic stops at two minutes" and "the mic runs until you notice,
 * then uploads megabytes to be told no". Kept as a constant rather than
 * negotiated over the wire so the timer works before the first request; if an
 * operator lowers the sidecar's limit, the server-side rejection still holds.
 */
export const MAX_RECORDING_SECONDS = 120;

// Sequenced synchronously (not via React state, which only commits after a
// render) so a second click fired before the first async step resolves reads
// the *already-updated* value and no-ops instead of racing a second
// getUserMedia/MediaRecorder or a second recorder.stop().
type RecorderPhase = "idle" | "starting" | "recording" | "stopping";

export function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function messageForTranscribeError(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.code) {
      case "audio_too_long":
        // The client auto-stops at the cap, so reaching this means the sidecar's
        // limit is lower than ours -- say the real limit, not "try again".
        return `Recording is too long — the limit is ${formatElapsed(
          MAX_RECORDING_SECONDS,
        )}.`;
      case "audio_too_large":
        return "Recording is too large — try a shorter clip.";
      case "audio_undecodable":
        return "That recording could not be read — try again.";
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

/**
 * Was this a permission refusal, or something else that happens to have thrown?
 *
 * Checked by `DOMException.name` rather than `instanceof DOMException` — the
 * relevant browsers all set the name, and a bare instanceof would still lump
 * NotFoundError / NotSupportedError in with a genuine denial.
 */
function isPermissionError(e: unknown): boolean {
  const name = (e as { name?: unknown } | null)?.name;
  return name === "NotAllowedError" || name === "SecurityError";
}

interface UseVoiceInputOptions {
  /**
   * Where a transcript goes when the recording ended *without* the caller
   * asking -- i.e. the auto-stop at MAX_RECORDING_SECONDS. The manual path
   * keeps returning the text from `stopRecording()`, because the caller there
   * has a reason to decide for itself (InputBar drops the transcript when the
   * stop was incidental to pressing Send).
   */
  onTranscript?: (text: string) => void;
}

export function useVoiceInput({ onTranscript }: UseVoiceInputOptions = {}) {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Seconds since recording began (spec §6). Driven off a wall-clock timestamp
  // rather than a tick count, so a throttled background tab reports the real
  // elapsed time and the auto-stop still fires at the right place.
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  // Sticky for the session once a getUserMedia call rejects (permission
  // denied, no device, etc.) -- distinct from `error`, which also carries
  // transient transcription failures that should NOT disable the button.
  const [permissionDenied, setPermissionDenied] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const phaseRef = useRef<RecorderPhase>("idle");
  const startedAtRef = useRef(0);
  // Read through a ref so a caller passing an inline arrow does not invalidate
  // startRecording/stopRecording on every render.
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;

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
    // Tracked outside the try so the catch can release a stream that was
    // successfully acquired before a *later* step threw. Without this, a
    // MediaRecorder constructor or start() failure leaks the mic for the life
    // of the page -- the tab's recording indicator stays lit and nothing in
    // the UI can turn it off again.
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.start();
      recorderRef.current = recorder;
      startedAtRef.current = Date.now();
      phaseRef.current = "recording";
      setIsRecording(true);
    } catch (e) {
      stream?.getTracks().forEach((t) => t.stop());
      phaseRef.current = "idle";
      if (isPermissionError(e)) {
        // Per spec (§6): show "microphone blocked" with a hint, and keep the
        // control disabled for the rest of the session -- there is nothing to
        // retry without a browser-level permission change, which needs a
        // reload to re-check.
        setPermissionDenied(true);
        setError(
          "Microphone blocked — allow access in your browser settings, then reload to try again.",
        );
      } else {
        // Everything else -- NotFoundError (no input device),
        // NotSupportedError / NotReadableError (browser or codec can't
        // record) -- is NOT a permission problem. Telling the user to change a
        // permission they already granted sends them somewhere that cannot
        // help, and latching permissionDenied would disable the button for the
        // session over what may be a transient device error.
        setError("Could not start recording — check your microphone and try again.");
      }
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

  // The elapsed timer, and the auto-stop it exists to make possible. Without
  // the cap, a mic left on runs until the 10 MB byte ceiling -- roughly 40
  // minutes of Opus -- and is then rejected server-side, which is the worst of
  // both: the user waited, uploaded, and got nothing.
  useEffect(() => {
    if (!isRecording) {
      setRecordingSeconds(0);
      return;
    }
    setRecordingSeconds(0);
    // 250 ms rather than 1 s: the displayed second stays honest and the
    // auto-stop lands within a quarter second of the cap.
    const id = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - startedAtRef.current) / 1000);
      setRecordingSeconds(elapsed);
      if (elapsed >= MAX_RECORDING_SECONDS) {
        // stopRecording() flips the phase synchronously before its first
        // await, so the next tick (if any) no-ops rather than double-stopping.
        void (async () => {
          const text = await stopRecording();
          if (text) onTranscriptRef.current?.(text);
        })();
      }
    }, 250);
    return () => window.clearInterval(id);
  }, [isRecording, stopRecording]);

  // Leaving the page mid-recording must not leave the mic open. React will not
  // run stopRecording for us, and the recorder holds the only reference to the
  // stream at this point.
  useEffect(
    () => () => {
      recorderRef.current?.stream.getTracks().forEach((t) => t.stop());
    },
    [],
  );

  return {
    isRecording,
    isTranscribing,
    isSupported,
    error,
    permissionDenied,
    recordingSeconds,
    maxRecordingSeconds: MAX_RECORDING_SECONDS,
    startRecording,
    stopRecording,
  };
}
