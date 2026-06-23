import { Database, FileStack, FileText } from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useKnowledgeBaseFiles, useSessionFiles } from "@/lib/queries";
import { formatFileSize } from "@/lib/utils";
import type { KnowledgeBaseFile } from "@/types/kb";

function SourceItem({ file }: { file: KnowledgeBaseFile }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="flex items-center gap-2">
        <FileText className="size-4 shrink-0 text-brand-blue" />
        <span className="truncate text-sm font-medium">{file.name}</span>
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
  );
}

export function SourcesDrawer({
  sessionId,
  open,
  onOpenChange,
}: {
  sessionId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: kbFiles } = useKnowledgeBaseFiles();
  const { data: sessionFiles } = useSessionFiles(sessionId);
  const kbReady = (kbFiles ?? []).filter((f) => f.status === "ready");
  const chatFiles = sessionFiles ?? [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-b border-border p-6">
          <SheetTitle className="flex items-center gap-2">
            <Database className="size-5 text-brand-blue" />
            Sources
          </SheetTitle>
          <SheetDescription>
            What the assistant can retrieve in this conversation.
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="h-[calc(100dvh-8rem)]">
          <div className="space-y-5 p-4">
            {/* Session-scoped files — ingested from chat attachments */}
            <section>
              <div className="mb-2 flex items-center gap-2 px-1">
                <FileStack className="size-4 text-amber-600 dark:text-amber-400" />
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  This chat&rsquo;s files
                </h3>
                {chatFiles.length > 0 && (
                  <Badge variant="secondary" className="text-xs">
                    {chatFiles.length}
                  </Badge>
                )}
              </div>
              {chatFiles.length === 0 ? (
                <p className="px-1 text-xs text-muted-foreground">
                  Large files you attach here are indexed for this chat only.
                </p>
              ) : (
                <div className="space-y-2">
                  {chatFiles.map((file) => (
                    <SourceItem key={file.id} file={file} />
                  ))}
                </div>
              )}
            </section>

            {/* Global knowledge base */}
            <section>
              <div className="mb-2 flex items-center gap-2 px-1">
                <Database className="size-4 text-brand-blue" />
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Knowledge base
                </h3>
              </div>
              {kbReady.length === 0 ? (
                <p className="px-1 text-xs text-muted-foreground">
                  No indexed sources yet.
                </p>
              ) : (
                <div className="space-y-2">
                  {kbReady.map((file) => (
                    <SourceItem key={file.id} file={file} />
                  ))}
                </div>
              )}
            </section>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
