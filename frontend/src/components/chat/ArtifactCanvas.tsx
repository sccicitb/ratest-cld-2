import { useCallback, useEffect, useRef, useState } from "react";
import {
  FileCode,
  Loader2,
  Printer,
  AlertTriangle,
} from "lucide-react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { fetchArtifactHtml } from "@/lib/api";
import type { ArtifactSummary } from "@/types/chat";

type LoadState =
  | { tag: "idle" }
  | { tag: "loading" }
  | { tag: "ready"; html: string }
  | { tag: "error"; message: string };

export function ArtifactCanvas({
  sessionId,
  artifact,
  open,
  onOpenChange,
}: {
  sessionId: string;
  artifact: ArtifactSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [state, setState] = useState<LoadState>({ tag: "idle" });
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const load = useCallback(
    async (a: ArtifactSummary) => {
      setState({ tag: "loading" });
      try {
        const html = await fetchArtifactHtml(sessionId, a.id);
        setState({ tag: "ready", html });
      } catch (err) {
        setState({
          tag: "error",
          message: err instanceof Error ? err.message : "Failed to load artifact",
        });
      }
    },
    [sessionId],
  );

  // Load when the artifact changes (or panel opens for the first time with an artifact).
  useEffect(() => {
    if (artifact && open) {
      load(artifact);
    } else if (!open) {
      setState({ tag: "idle" });
    }
  }, [artifact, open, load]);

  const handlePrint = () => {
    iframeRef.current?.contentWindow?.print();
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-2xl">
        {/* Header */}
        <SheetHeader className="shrink-0 border-b border-border p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 min-w-0">
              <FileCode className="size-5 shrink-0 text-brand-blue" />
              <SheetTitle className="truncate">
                {artifact?.title ?? "Artifact"}
              </SheetTitle>
              {artifact && (
                <Badge variant="secondary" className="shrink-0 text-xs">
                  v{artifact.latestVersion}
                </Badge>
              )}
            </div>
            {state.tag === "ready" && (
              <Button
                variant="outline"
                size="sm"
                onClick={handlePrint}
                className="ml-2 shrink-0 gap-1.5"
              >
                <Printer className="size-4" />
                Export PDF
              </Button>
            )}
          </div>
          <SheetDescription>
            Sandboxed HTML report — interactive JS runs inside an isolated origin.
          </SheetDescription>
        </SheetHeader>

        {/* Body */}
        <ScrollArea className="flex-1">
          <div className="h-full p-1">
            {state.tag === "idle" && (
              <div className="flex h-64 items-center justify-center text-sm text-muted-foreground">
                Select an artifact to view.
              </div>
            )}

            {state.tag === "loading" && (
              <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                Loading artifact…
              </div>
            )}

            {state.tag === "error" && (
              <div className="flex h-64 flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
                <AlertTriangle className="size-5 text-amber-500" />
                <p>{state.message}</p>
              </div>
            )}

            {state.tag === "ready" && (
              <iframe
                ref={iframeRef}
                srcDoc={state.html}
                sandbox="allow-scripts allow-modals"
                title={artifact?.title ?? "Artifact"}
                className="h-full min-h-[60vh] w-full border-0"
              />
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
