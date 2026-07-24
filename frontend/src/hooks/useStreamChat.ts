import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { streamChat } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type {
  ArtifactSummary,
  Attachment,
  PipelineStep,
  StepStatus,
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

  const sendMessage = useCallback(
    async (message: string, attachments?: Attachment[]) => {
      reset();
      abortRef.current = false;
      setIsStreaming(true);

      // Reflect the new user message immediately.
      qc.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });

      try {
        for await (const event of streamChat(sessionId, message, attachments)) {
          if (abortRef.current) break;
          switch (event.type) {
            case "step": {
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
        setError(err instanceof Error ? err.message : "Streaming failed");
      } finally {
        setIsStreaming(false);
        // Refresh persisted messages and session ordering/title.
        qc.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
        qc.invalidateQueries({ queryKey: queryKeys.sessions });
      }
    },
    [sessionId, qc, reset],
  );

  return {
    sendMessage,
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
