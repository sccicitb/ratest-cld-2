import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, streamChat, streamResume } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type {
  ArtifactSummary,
  Attachment,
  PipelineStep,
  StepStatus,
  StreamEvent,
} from "@/types/chat";

export interface StepState {
  id?: string;
  step: PipelineStep;
  status: StepStatus;
  toolName?: string;
  toolArgs?: Record<string, unknown>;
}

export function useStreamChat(sessionId: string) {
  const qc = useQueryClient();
  const [isStreaming, setIsStreaming] = useState(false);
  const [steps, setSteps] = useState<StepState[]>([]);
  const [streamedContent, setStreamedContent] = useState("");
  const [streamedReasoning, setStreamedReasoning] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [currentArtifact, setCurrentArtifact] = useState<ArtifactSummary | null>(null);

  // Every consume pass is a "run", identified by a monotonic id and owning an
  // AbortController. A run may only touch state while it is still the current
  // run: switching rooms, aborting, or starting a new turn supersedes it. This
  // is what keeps a turn left running in another room from painting into this
  // one — the component instance is REUSED across /chat/:sessionId changes, so
  // hook state alone is not room-scoped.
  const runIdRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    setSteps([]);
    setStreamedContent("");
    setStreamedReasoning("");
    setError(null);
    setCurrentArtifact(null);
  }, []);

  // Supersede the current run (if any) and close its SSE connection, so it
  // stops immediately rather than at whenever the next event happens to land.
  const cancelRun = useCallback(() => {
    runIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  const startRun = useCallback(() => {
    cancelRun();
    const controller = new AbortController();
    controllerRef.current = controller;
    return { run: runIdRef.current, signal: controller.signal };
  }, [cancelRun]);

  const abort = useCallback(() => {
    cancelRun();
    setIsStreaming(false);
  }, [cancelRun]);

  const consumeStream = useCallback(
    async (run: number, events: AsyncIterable<StreamEvent>) => {
      let announced = false;
      try {
        for await (const event of events) {
          if (runIdRef.current !== run) break;
          // The first event proves the turn is spawned and registered, so a
          // refetch now observes `activeTurn: true` — which lights the
          // sidebar dot and bootstraps the 2.5s poll that sustains it.
          // Invalidating at send time would be too early: the stream
          // generator is lazy, so the POST hasn't been issued yet.
          if (!announced) {
            announced = true;
            qc.invalidateQueries({ queryKey: queryKeys.sessions });
          }
          switch (event.type) {
            case "step": {
              // A tool call starting means the model's interim "thinking out
              // loud" text is suppressed server-side — clear the live bubble so
              // only the final answer streams in (matches what gets persisted).
              if (event.step === "calling_tool" && event.status === "active") {
                setStreamedContent("");
              }
              // Steps with an id (repeatable tool calls) are keyed by id;
              // single-occurrence pipeline steps are keyed by their name.
              const key = event.id ?? event.step;
              setSteps((prev) => {
                const idx = prev.findIndex((s) => (s.id ?? s.step) === key);
                const next: StepState = {
                  id: event.id,
                  step: event.step,
                  status: event.status,
                  toolName: event.toolName,
                  toolArgs: event.toolArgs,
                };
                if (idx === -1) return [...prev, next];
                const copy = [...prev];
                copy[idx] = next;
                return copy;
              });
              break;
            }
            case "token":
              setStreamedContent((prev) => prev + event.content);
              break;
            case "reasoning":
              setStreamedReasoning((prev) => prev + event.content);
              break;
            case "error":
              setError(event.message);
              break;
            case "artifact":
              setCurrentArtifact({
                id: event.artifactId,
                title: event.title,
                latestVersion: event.version,
                createdAt: new Date().toISOString(),
              });
              break;
            case "done":
              break;
            default:
              break;
          }
        }
      } catch (err) {
        const aborted = (err as { name?: string } | null)?.name === "AbortError";
        if (runIdRef.current !== run || aborted) {
          // Superseded (room switch / abort / new turn) — not a failure, and
          // the state belongs to whoever replaced us now.
        } else if (err instanceof ApiError && err.status === 409) {
          // 409 means a turn is already running for this room (e.g. it was
          // started elsewhere, or resume() and sendMessage() raced). That's
          // not a failure — the `activeTurn` poll + resume-on-entry effect
          // will reattach to the real turn, so stay quiet rather than
          // surfacing a scary error.
        } else {
          setError(err instanceof Error ? err.message : "Streaming failed");
        }
      } finally {
        if (runIdRef.current === run) {
          setIsStreaming(false);
          controllerRef.current = null;
        }
        // Refresh persisted messages and session ordering/title. `sessionId` is
        // this run's room (captured at creation), so a superseded run still
        // refreshes the room it actually belonged to.
        qc.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
        qc.invalidateQueries({ queryKey: queryKeys.sessions });
      }
    },
    [sessionId, qc],
  );

  const sendMessage = useCallback(
    async (message: string, attachments?: Attachment[]) => {
      const { run, signal } = startRun();
      reset();
      setIsStreaming(true);

      // Reflect the new user message immediately.
      qc.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });

      await consumeStream(run, streamChat(sessionId, message, attachments, signal));
    },
    [sessionId, qc, reset, startRun, consumeStream],
  );

  // Reattach to a turn that's still running (or replay+tail one that just
  // finished) after navigating away and back. Guards against double-consume
  // via the live controller rather than `isStreaming`, because the flag is
  // still stale-true on the first render after a room switch.
  const resume = useCallback(async () => {
    if (controllerRef.current) return;
    const { run, signal } = startRun();
    reset();
    setIsStreaming(true);
    await consumeStream(run, streamResume(sessionId, signal));
  }, [sessionId, reset, startRun, consumeStream]);

  // Leaving the room (or unmounting) tears down its run, so a turn that keeps
  // going server-side can never write into the room we navigated to. Coming
  // back re-attaches through resume-on-entry, replaying the log from index 0.
  useEffect(() => {
    return () => {
      cancelRun();
      setIsStreaming(false);
    };
  }, [sessionId, cancelRun]);

  return {
    sendMessage,
    resume,
    isStreaming,
    steps,
    streamedContent,
    streamedReasoning,
    error,
    currentArtifact,
    abort,
    reset,
  };
}
