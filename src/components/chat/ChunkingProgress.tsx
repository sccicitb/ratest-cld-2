import { Check, FileStack } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

interface ChunkingProgressProps {
  fileName: string;
  progress: number;
  chunkCount: number;
  total: number;
}

export function ChunkingProgress({
  fileName,
  progress,
  chunkCount,
  total,
}: ChunkingProgressProps) {
  const done = progress >= 100;
  return (
    <div className="mx-auto w-full max-w-2xl rounded-lg border border-border bg-card px-4 py-3">
      <div className="flex items-center gap-2 text-sm">
        <span
          className={cn(
            "flex size-6 items-center justify-center rounded-md",
            done ? "bg-emerald-100 dark:bg-emerald-900/40" : "bg-muted",
          )}
        >
          {done ? (
            <Check className="size-4 text-emerald-600 dark:text-emerald-400" />
          ) : (
            <FileStack className="size-4 text-brand-blue" />
          )}
        </span>
        <span className="truncate font-medium">{fileName}</span>
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">
          {done ? `${total} chunks indexed` : `${chunkCount}/${total} chunks`}
        </span>
      </div>
      {!done && <Progress value={progress} className="mt-2 h-1.5" />}
    </div>
  );
}
