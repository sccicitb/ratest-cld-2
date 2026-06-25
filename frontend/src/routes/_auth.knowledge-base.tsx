import { useMemo, useState } from "react";
import { Database, Search } from "lucide-react";

import { FileCard } from "@/components/kb/FileCard";
import { UploadDropzone } from "@/components/kb/UploadDropzone";
import { UploadTaskCard } from "@/components/kb/UploadTaskCard";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useFileUpload } from "@/hooks/useFileUpload";
import { useKnowledgeBaseFiles } from "@/lib/queries";
import { cn } from "@/lib/utils";
import type { FileStatus } from "@/types/kb";

const STATUS_FILTERS: { label: string; value: FileStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Ready", value: "ready" },
  { label: "Indexing", value: "indexing" },
  { label: "Error", value: "error" },
];

export default function KnowledgeBaseRoute() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<FileStatus | "all">("all");
  const { data: files, isLoading } = useKnowledgeBaseFiles();
  const { addFiles, tasks, cancelTask, retryTask, removeTask } =
    useFileUpload();

  const filtered = useMemo(() => {
    if (!files) return [];
    return files.filter((f) => {
      const matchesSearch = f.name
        .toLowerCase()
        .includes(search.toLowerCase());
      const matchesStatus = status === "all" || f.status === status;
      return matchesSearch && matchesStatus;
    });
  }, [files, search, status]);

  const activeTasks = tasks.filter((t) => t.status !== "done");

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4">
        <Database className="size-5 text-brand-blue" />
        <h1 className="font-medium">Knowledge Base</h1>
        {files && (
          <Badge variant="secondary" className="ml-1">
            {files.length}
          </Badge>
        )}
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl space-y-6 p-6">
          <UploadDropzone onFiles={addFiles} />

          {activeTasks.length > 0 && (
            <div className="space-y-2">
              <h2 className="text-sm font-medium text-muted-foreground">
                Uploads
              </h2>
              {activeTasks.map((task) => (
                <UploadTaskCard
                  key={task.id}
                  task={task}
                  onCancel={cancelTask}
                  onRetry={retryTask}
                  onRemove={removeTask}
                />
              ))}
            </div>
          )}

          {/* Filters */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="relative max-w-xs flex-1">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search files…"
                className="pl-8"
              />
            </div>
            <div className="flex items-center gap-1">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  onClick={() => setStatus(f.value)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    status === f.value
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* File grid */}
          {isLoading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-40 w-full" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border py-16 text-center text-sm text-muted-foreground">
              No files match your filters.
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((file) => (
                <FileCard key={file.id} file={file} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
