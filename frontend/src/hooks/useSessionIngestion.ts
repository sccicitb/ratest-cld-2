import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { uploadChatAttachments } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { Attachment } from "@/types/chat";

export interface IngestTask {
  fileName: string;
  progress: number;
  chunkCount: number;
  total: number;
}

/**
 * Drives session-scoped ingestion of heavy chat attachments.  Consumes the
 * multipart→SSE `uploadChatAttachments` stream (mock or real), surfaces live
 * per-file chunking progress into the chat feed, and returns the resolved
 * Attachment records once the stream closes.
 */
export function useSessionIngestion(sessionId: string) {
  const qc = useQueryClient();
  const [tasks, setTasks] = useState<Record<string, IngestTask>>({});

  const upload = useCallback(
    async (files: File[]): Promise<Attachment[]> => {
      const resolved: Attachment[] = [];
      for await (const ev of uploadChatAttachments(sessionId, files)) {
        switch (ev.type) {
          case "chunk_progress":
            setTasks((prev) => ({
              ...prev,
              [ev.fileName]: {
                fileName: ev.fileName,
                progress: ev.progress,
                chunkCount: ev.chunkCount,
                total: ev.total,
              },
            }));
            break;
          case "attachment_resolved":
            resolved.push(ev.attachment);
            // Clear the progress row for this file after a brief pause.
            const { fileName } = ev.attachment;
            setTimeout(() => {
              setTasks((prev) => {
                const copy = { ...prev };
                delete copy[fileName];
                return copy;
              });
            }, 1600);
            break;
          case "error":
            break;
          // done – stream closed; resolved array is complete
        }
      }
      qc.invalidateQueries({ queryKey: queryKeys.sessionFiles(sessionId) });
      return resolved;
    },
    [sessionId, qc],
  );

  return { upload, tasks: Object.values(tasks) };
}
