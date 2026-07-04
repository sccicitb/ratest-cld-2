import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { uploadKnowledgeBaseFile, type KBUploadOpts } from "@/lib/api";
import { generateId, isValidFileType } from "@/lib/utils";
import type { UploadTask, UploadTaskStatus } from "@/types/kb";

const MAX_CONCURRENT = 3;

export function useFileUpload() {
  const qc = useQueryClient();
  const [tasks, setTasks] = useState<UploadTask[]>([]);
  const activeCount = useRef(0);
  const queue = useRef<string[]>([]);
  // Files by task id, kept in a ref so `pump` can start an upload without
  // reading React state inside a setState updater (see `pump` below).
  const files = useRef<Map<string, File>>(new Map());
  // Filing opts per task id — set at addFiles time, read at runTask time.
  const filingOpts = useRef<Map<string, KBUploadOpts>>(new Map());

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
      const opts = filingOpts.current.get(id);
      try {
        for await (const ev of uploadKnowledgeBaseFile(file, opts)) {
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
        files.current.delete(id);
        filingOpts.current.delete(id);
        pump();
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [qc, update],
  );

  // Start queued tasks up to the concurrency cap. This MUST NOT mutate state:
  // a side effect inside a setState updater gets invoked twice by React Strict
  // Mode, which previously fired every upload twice. The queue + `files` ref
  // give us everything we need without touching `tasks`.
  const pump = useCallback(() => {
    while (activeCount.current < MAX_CONCURRENT && queue.current.length > 0) {
      const id = queue.current.shift()!;
      const file = files.current.get(id);
      if (file) void runTask(id, file);
    }
  }, [runTask]);

  const addFiles = useCallback(
    (incoming: FileList | File[], opts?: KBUploadOpts) => {
      const newTasks: UploadTask[] = Array.from(incoming).map((file) => {
        const valid = isValidFileType(file.name);
        const status: UploadTaskStatus = valid ? "queued" : "error";
        const id = generateId();
        if (valid) {
          files.current.set(id, file);
          if (opts) filingOpts.current.set(id, opts);
        }
        return {
          id,
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
    files.current.delete(id);
    filingOpts.current.delete(id);
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const removeTask = cancelTask;

  const retryTask = useCallback(
    (id: string) => {
      const task = tasks.find((t) => t.id === id);
      if (!task) return;
      files.current.set(id, task.file);
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
    [pump, tasks],
  );

  return { addFiles, tasks, cancelTask, retryTask, removeTask };
}
