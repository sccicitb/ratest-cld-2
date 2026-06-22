import { FileText, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatFileSize } from "@/lib/utils";

export interface PendingAttachment {
  id: string;
  file: File;
  valid: boolean;
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
      {attachments.map((a) => (
        <div
          key={a.id}
          className="group flex items-center gap-2 rounded-lg border border-border bg-muted/50 py-1.5 pl-2.5 pr-1.5 text-sm"
        >
          <FileText
            className={
              a.valid ? "size-4 text-brand-blue" : "size-4 text-destructive"
            }
          />
          <div className="flex flex-col leading-tight">
            <span className="max-w-40 truncate font-medium">{a.file.name}</span>
            <span className="text-xs text-muted-foreground">
              {a.valid ? formatFileSize(a.file.size) : "Unsupported type"}
            </span>
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
      ))}
    </div>
  );
}
