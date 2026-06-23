import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { mockIngestChatFile } from "@/lib/mock";
import { queryKeys } from "@/lib/queries";
import type { IngestFile } from "@/components/chat/InputBar";

export interface IngestTask {
  fileName: string;
  progress: number;
  chunkCount: number;
  total: number;
}

/**
 * Drives session-scoped ingestion of heavy chat attachments. Exposes live
 * per-file chunking progress (rendered inline in the chat feed) and refreshes
 * the session-files query when each file finishes indexing.
 */
export function useSessionIngestion(sessionId: string) {
  const qc = useQueryClient();
  const [tasks, setTasks] = useState<Record<string, IngestTask>>({});

  const ingest = useCallback(
    async (files: IngestFile[]) => {
      await Promise.all(
        files.map(async (file) => {
          for await (const event of mockIngestChatFile(sessionId, file)) {
            setTasks((prev) => ({
              ...prev,
              [file.name]: {
                fileName: event.fileName,
                progress: event.progress,
                chunkCount: event.chunkCount,
                total: event.total,
              },
            }));
          }
          qc.invalidateQueries({
            queryKey: queryKeys.sessionFiles(sessionId),
          });
          // Leave the completed row up briefly, then clear it.
          setTimeout(() => {
            setTasks((prev) => {
              const copy = { ...prev };
              delete copy[file.name];
              return copy;
            });
          }, 1600);
        }),
      );
    },
    [sessionId, qc],
  );

  return { ingest, tasks: Object.values(tasks) };
}
