import {
  AlertCircle,
  CheckCircle2,
  FileUp,
  RefreshCw,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { formatFileSize } from "@/lib/utils";
import type { UploadTask, UploadTaskStatus } from "@/types/kb";

const STATUS_LABEL: Record<UploadTaskStatus, string> = {
  queued: "Queued",
  uploading: "Uploading…",
  processing: "Processing…",
  indexing: "Indexing…",
  done: "Done",
  error: "Failed",
};

export function UploadTaskCard({
  task,
  onCancel,
  onRetry,
  onRemove,
}: {
  task: UploadTask;
  onCancel: (id: string) => void;
  onRetry: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const isError = task.status === "error";
  const isDone = task.status === "done";
  const inFlight = !isError && !isDone;

  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-card p-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted">
        {isError ? (
          <AlertCircle className="size-4 text-destructive" />
        ) : isDone ? (
          <CheckCircle2 className="size-4 text-emerald-500" />
        ) : (
          <FileUp className="size-4 text-brand-blue" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">
            {task.file.name}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {formatFileSize(task.file.size)}
          </span>
        </div>
        {inFlight && <Progress value={task.progress} className="mt-1.5 h-1.5" />}
        <p
          className={
            isError
              ? "mt-1 text-xs text-destructive"
              : "mt-1 text-xs text-muted-foreground"
          }
        >
          {isError ? task.error ?? "Failed" : STATUS_LABEL[task.status]}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {isError && (
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => onRetry(task.id)}
            aria-label="Retry upload"
          >
            <RefreshCw className="size-4" />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          onClick={() => (inFlight ? onCancel(task.id) : onRemove(task.id))}
          aria-label={inFlight ? "Cancel upload" : "Dismiss"}
        >
          <X className="size-4" />
        </Button>
      </div>
    </div>
  );
}
