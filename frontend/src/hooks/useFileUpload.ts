import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { uploadKnowledgeBaseFile } from "@/lib/api";
import { generateId, isValidFileType } from "@/lib/utils";
import type { UploadTask, UploadTaskStatus } from "@/types/kb";

const MAX_CONCURRENT = 3;

export function useFileUpload() {
  const qc = useQueryClient();
  const [tasks, setTasks] = useState<UploadTask[]>([]);
  const activeCount = useRef(0);
  const queue = useRef<string[]>([]);

  const update = useCallback(
    (id: string, patch: Partial<UploadTask>) => {
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, ...patch } : t)),
      );
    },
    [],
  );

  const runTask = useCallback(
    async (id: string, file: File) => {
      activeCount.current++;
      update(id, { status: "uploading", progress: 0 });
      try {
        for await (const ev of uploadKnowledgeBaseFile(file)) {
          if (ev.type === "chunk_progress") {
            update(id, { progress: ev.progress, status: "indexing" });
          } else if (ev.type === "file_resolved") {
            // File is indexed and ready.
          }
        }
        update(id, { status: "done", progress: 100 });
        qc.invalidateQueries({ queryKey: ["kb-files"] });
      } catch (err) {
        update(id, {
          status: "error",
          error: err instanceof Error ? err.message : "Upload failed",
        });
      } finally {
        activeCount.current--;
        pump();
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [qc, update],
  );

  const pump = useCallback(() => {
    while (activeCount.current < MAX_CONCURRENT && queue.current.length > 0) {
      const id = queue.current.shift()!;
      setTasks((prev) => {
        const task = prev.find((t) => t.id === id);
        if (task && task.status === "queued") void runTask(id, task.file);
        return prev;
      });
    }
  }, [runTask]);

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const incoming = Array.from(files);
      const newTasks: UploadTask[] = incoming.map((file) => {
        const valid = isValidFileType(file.name);
        const status: UploadTaskStatus = valid ? "queued" : "error";
        return {
          id: generateId(),
          file,
          progress: 0,
          status,
          error: valid ? undefined : "Unsupported file type",
        };
      });
      setTasks((prev) => [...prev, ...newTasks]);
      for (const t of newTasks) {
        if (t.status === "queued") queue.current.push(t.id);
      }
      pump();
    },
    [pump],
  );

  const cancelTask = useCallback((id: string) => {
    queue.current = queue.current.filter((q) => q !== id);
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const removeTask = cancelTask;

  const retryTask = useCallback(
    (id: string) => {
      setTasks((prev) =>
        prev.map((t) =>
          t.id === id
            ? { ...t, status: "queued", progress: 0, error: undefined }
            : t,
        ),
      );
      queue.current.push(id);
      pump();
    },
    [pump],
  );

  return { addFiles, tasks, cancelTask, retryTask, removeTask };
}
