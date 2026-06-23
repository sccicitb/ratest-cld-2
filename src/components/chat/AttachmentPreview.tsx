import { FileStack, FileText, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatFileSize } from "@/lib/utils";
import type { AttachmentRoute } from "@/lib/utils";

export interface PendingAttachment {
  id: string;
  file: File;
  route: AttachmentRoute["route"];
  /** Note (for `ingest`) or reason (for `reject`); undefined for `inline`. */
  message?: string;
}

export function AttachmentPreview({
  attachments,
  onRemove,
}: {
  attachments: PendingAttachment[];
  onRemove: (id: string) => void;
}) {
  if (attachments.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 px-1 pb-2">
      {attachments.map((a) => {
        const isReject = a.route === "reject";
        const isIngest = a.route === "ingest";
        const Icon = isIngest ? FileStack : FileText;
        const iconClass = isReject
          ? "size-4 text-destructive"
          : isIngest
            ? "size-4 text-amber-600 dark:text-amber-400"
            : "size-4 text-brand-blue";
        const subtitle = isReject
          ? a.message
          : isIngest
            ? (a.message ?? "Indexed for this chat")
            : formatFileSize(a.file.size);
        const subtitleClass = isReject
          ? "max-w-52 truncate text-xs text-destructive"
          : isIngest
            ? "max-w-52 truncate text-xs text-amber-600 dark:text-amber-400"
            : "text-xs text-muted-foreground";

        return (
          <div
            key={a.id}
            className="group flex items-center gap-2 rounded-lg border border-border bg-muted/50 py-1.5 pl-2.5 pr-1.5 text-sm"
          >
            <Icon className={iconClass} />
            <div className="flex flex-col leading-tight">
              <span className="max-w-44 truncate font-medium">
                {a.file.name}
              </span>
              <span className={subtitleClass}>{subtitle}</span>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              onClick={() => onRemove(a.id)}
              aria-label={`Remove ${a.file.name}`}
            >
              <X className="size-3.5" />
            </Button>
          </div>
        );
      })}
    </div>
  );
}
