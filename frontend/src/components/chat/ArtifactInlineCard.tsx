import { FileCode } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { ArtifactSummary } from "@/types/chat";

export function ArtifactInlineCard({
  artifact,
  onOpen,
}: {
  artifact: ArtifactSummary;
  onOpen: () => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-background/60 px-3 py-2">
      <FileCode className="size-4 shrink-0 text-brand-blue" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{artifact.title}</p>
      </div>
      <Badge variant="secondary" className="shrink-0 text-xs">
        v{artifact.latestVersion}
      </Badge>
      <Button
        variant="outline"
        size="sm"
        onClick={onOpen}
        className="shrink-0 gap-1"
      >
        <FileCode className="size-3.5" />
        Open in Canvas
      </Button>
    </div>
  );
}
