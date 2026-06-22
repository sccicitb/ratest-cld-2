import { Database, FileText } from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useKnowledgeBaseFiles } from "@/lib/queries";
import { formatFileSize } from "@/lib/utils";

export function SourcesDrawer({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: files } = useKnowledgeBaseFiles();
  const ready = (files ?? []).filter((f) => f.status === "ready");

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-b border-border p-6">
          <SheetTitle className="flex items-center gap-2">
            <Database className="size-5 text-brand-blue" />
            Sources
          </SheetTitle>
          <SheetDescription>
            Knowledge base files available for retrieval in this conversation.
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="h-[calc(100dvh-8rem)]">
          <div className="space-y-2 p-4">
            {ready.length === 0 && (
              <p className="px-2 py-8 text-center text-sm text-muted-foreground">
                No indexed sources yet.
              </p>
            )}
            {ready.map((file) => (
              <div
                key={file.id}
                className="rounded-lg border border-border p-3"
              >
                <div className="flex items-center gap-2">
                  <FileText className="size-4 shrink-0 text-brand-blue" />
                  <span className="truncate text-sm font-medium">
                    {file.name}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {file.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="text-xs">
                      {tag}
                    </Badge>
                  ))}
                  <span className="ml-auto text-xs text-muted-foreground">
                    {file.chunkCount} chunks · {formatFileSize(file.size)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
