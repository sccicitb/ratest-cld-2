import { useCallback, useRef, useState } from "react";
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
  const abortRef = useRef(false);

  const reset = useCallback(() => {
    setSteps([]);
    setStreamedContent("");
    setStreamedReasoning("");
    setError(null);
    setCurrentArtifact(null);
  }, []);

  const abort = useCallback(() => {
    abortRef.current = true;
    setIsStreaming(false);
  }, []);

  const consumeStream = useCallback(
    async (events: AsyncIterable<StreamEvent>) => {
      try {
        for await (const event of events) {
          if (abortRef.current) break;
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
        // 409 means a turn is already running for this room (e.g. it was
        // started elsewhere, or resume() and sendMessage() raced). That's
        // not a failure — the `activeTurn` poll + resume-on-entry effect
        // will reattach to the real turn, so stay quiet rather than
        // surfacing a scary error.
        if (err instanceof ApiError && err.status === 409) {
          // no-op
        } else {
          setError(err instanceof Error ? err.message : "Streaming failed");
        }
      } finally {
        setIsStreaming(false);
        // Refresh persisted messages and session ordering/title.
        qc.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
        qc.invalidateQueries({ queryKey: queryKeys.sessions });
      }
    },
    [sessionId, qc],
  );

  const sendMessage = useCallback(
    async (message: string, attachments?: Attachment[]) => {
      reset();
      abortRef.current = false;
      setIsStreaming(true);

      // Reflect the new user message immediately.
      qc.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });

      await consumeStream(streamChat(sessionId, message, attachments));
    },
    [sessionId, qc, reset, consumeStream],
  );

  // Reattach to a turn that's still running (or replay+tail one that just
  // finished) after navigating away and back. Guards against double-consume
  // if a stream is already being read.
  const resume = useCallback(async () => {
    if (isStreaming) return;
    reset();
    abortRef.current = false;
    setIsStreaming(true);
    await consumeStream(streamResume(sessionId));
  }, [sessionId, isStreaming, reset, consumeStream]);

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
