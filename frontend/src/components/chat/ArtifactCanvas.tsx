import { useCallback, useEffect, useRef, useState } from "react";
import {
  ExternalLink,
  FileCode,
  Loader2,
  Maximize2,
  Minimize2,
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
  | { tag: "ready"; html: string; blobUrl: string }
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
  const [expanded, setExpanded] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const seqRef = useRef(0);
  const blobUrlRef = useRef<string | null>(null);

  // Revoke the previous blob URL so we don't leak.
  const revoke = useCallback(() => {
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  const load = useCallback(
    async (a: ArtifactSummary) => {
      seqRef.current += 1;
      const seq = seqRef.current;
      revoke();
      setState({ tag: "loading" });
      try {
        const html = await fetchArtifactHtml(sessionId, a.id);
        if (seq !== seqRef.current) return; // stale
        // Use a blob URL instead of srcdoc so large embedded base64 images
        // (charts, etc.) are not limited by the srcdoc attribute size cap.
        const blob = new Blob([html], { type: "text/html;charset=utf-8" });
        const blobUrl = URL.createObjectURL(blob);
        blobUrlRef.current = blobUrl;
        setState({ tag: "ready", html, blobUrl });
      } catch (err) {
        if (seq !== seqRef.current) return;
        setState({
          tag: "error",
          message: err instanceof Error ? err.message : "Failed to load artifact",
        });
      }
    },
    [sessionId, revoke],
  );

  // Revoke on unmount.
  useEffect(() => () => revoke(), [revoke]);

  // Load when the artifact changes (or panel opens for the first time).
  useEffect(() => {
    if (artifact && open) {
      load(artifact);
    } else if (!open) {
      revoke();
      setState({ tag: "idle" });
    }
  }, [artifact, open, load, revoke]);

  const handlePrint = () => {
    iframeRef.current?.contentWindow?.print();
  };

  const handleOpenNewTab = () => {
    if (state.tag === "ready") {
      window.open(state.blobUrl, "_blank");
    }
  };

  const maxW = expanded ? "sm:max-w-[95vw]" : "sm:max-w-3xl";

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className={`flex w-full flex-col gap-0 p-0 ${maxW}`}>
        {/* Header */}
        <SheetHeader className="shrink-0 border-b border-border p-5">
          <div className="flex items-center gap-2">
            <FileCode className="size-5 shrink-0 text-brand-blue" />
            <SheetTitle className="min-w-0 flex-1 truncate">
              {artifact?.title ?? "Artifact"}
            </SheetTitle>
            {artifact && (
              <Badge variant="secondary" className="shrink-0 text-xs">
                v{artifact.latestVersion}
              </Badge>
            )}
            {/* Expand / collapse */}
            <Button
              variant="ghost"
              size="icon"
              className="size-7 shrink-0"
              onClick={() => setExpanded((v) => !v)}
              aria-label={expanded ? "Collapse" : "Expand"}
            >
              {expanded ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
            </Button>
            {/* Actions (right-aligned, before the Sheet's built-in X) */}
            <div className="ml-auto flex items-center gap-1">
              {state.tag === "ready" && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handlePrint}
                    className="gap-1.5"
                  >
                    <Printer className="size-4" />
                    Export
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleOpenNewTab}
                    className="gap-1.5"
                  >
                    <ExternalLink className="size-4" />
                    Open
                  </Button>
                </>
              )}
            </div>
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
                src={state.blobUrl}
                sandbox="allow-scripts allow-modals"
                title={artifact?.title ?? "Artifact"}
                className="h-full min-h-[70vh] w-full border-0"
              />
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
