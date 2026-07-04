import { useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  Globe,
  Loader2,
  MoreVertical,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { TagEditor } from "@/components/kb/TagEditor";
import {
  useDeleteKBFile,
  useReindexKBFile,
  useUpdateFileTags,
} from "@/lib/queries";
import { formatFileSize, formatRelativeTime } from "@/lib/utils";
import type { FileStatus, KnowledgeBaseFile } from "@/types/kb";

const STATUS_META: Record<
  FileStatus,
  {
    label: string;
    variant: "success" | "warning" | "destructive";
    icon: typeof CheckCircle2;
    spin?: boolean;
  }
> = {
  ready: { label: "Ready", variant: "success", icon: CheckCircle2 },
  indexing: { label: "Indexing", variant: "warning", icon: Loader2, spin: true },
  error: { label: "Error", variant: "destructive", icon: AlertCircle },
};

export function FileCard({
  file,
  groupName,
}: {
  file: KnowledgeBaseFile;
  groupName?: string;
}) {
  const deleteFile = useDeleteKBFile();
  const reindexFile = useReindexKBFile();
  const updateTags = useUpdateFileTags();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const status = STATUS_META[file.status];
  const StatusIcon = status.icon;

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
          <FileText className="size-5 text-brand-blue" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium" title={file.name}>
            {file.name}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatFileSize(file.size)} · {formatRelativeTime(file.uploadDate)}
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8 shrink-0">
              <MoreVertical className="size-4" />
              <span className="sr-only">File actions</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() => reindexFile.mutate(file.id)}
              disabled={file.status === "indexing"}
            >
              <RefreshCw />
              Re-index
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => setConfirmOpen(true)}
            >
              <Trash2 />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={status.variant} className="gap-1">
          <StatusIcon className={status.spin ? "size-3 animate-spin" : "size-3"} />
          {status.label}
        </Badge>
        {groupName != null && (
          <Badge variant="outline" className="text-xs font-normal">
            {groupName}
          </Badge>
        )}
        {file.isPublic && (
          <Badge variant="secondary" className="gap-1 text-xs font-normal">
            <Globe className="size-3" />
            Public
          </Badge>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {file.status === "ready"
            ? `${file.chunkCount} chunks`
            : file.status === "indexing"
              ? "Processing…"
              : "Failed to index"}
        </span>
      </div>

      <TagEditor
        tags={file.tags}
        onChange={(tags) => updateTags.mutate({ id: file.id, tags })}
      />

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete file?</AlertDialogTitle>
            <AlertDialogDescription>
              "{file.name}" and its {file.chunkCount} indexed chunks will be
              permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteFile.mutate(file.id)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
