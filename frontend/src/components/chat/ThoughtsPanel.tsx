import { useEffect, useRef, useState } from "react";
import { Brain, ChevronDown, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

export function ThoughtsPanel({
  reasoning,
  active,
}: {
  reasoning: string;
  active: boolean;
}) {
  // Collapsed by default — the user opens it to read the full reasoning.
  const [expanded, setExpanded] = useState(false);

  // Time the thinking phase (active → done) for the "Thought for Ns" chip.
  const startRef = useRef<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  useEffect(() => {
    if (active) {
      startRef.current = Date.now();
      setElapsedMs(null);
    } else if (startRef.current != null) {
      setElapsedMs(Date.now() - startRef.current);
    }
  }, [active]);

  if (!reasoning) return null;

  // Collapsed garnish: a live teaser (last line of reasoning) while thinking,
  // settling to a "Thought for Ns" chip once done.
  const lines = reasoning.trim().split("\n").filter(Boolean);
  const teaser = lines[lines.length - 1] ?? "";
  const seconds = elapsedMs != null ? Math.max(1, Math.round(elapsedMs / 1000)) : null;

  return (
    <div className="w-full rounded-lg border border-border/50 bg-muted/30 text-sm">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-muted-foreground"
        aria-expanded={expanded}
        aria-controls="thoughts-body"
      >
        {active ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
        ) : (
          <Brain className="h-4 w-4 shrink-0" />
        )}
        {active ? (
          <span className="min-w-0 flex-1 truncate italic opacity-80">
            {teaser || "Thinking…"}
          </span>
        ) : (
          <span className="min-w-0 flex-1 truncate font-medium">
            {seconds != null ? `Thought for ${seconds}s` : "Thoughts"}
          </span>
        )}
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 transition-transform", expanded && "rotate-180")}
        />
      </button>
      {expanded && (
        <div
          id="thoughts-body"
          className="whitespace-pre-wrap px-3 pb-3 font-mono text-xs text-muted-foreground"
        >
          {reasoning}
        </div>
      )}
    </div>
  );
}
