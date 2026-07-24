import { useEffect, useState } from "react";
import { Brain, ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export function ThoughtsPanel({
  reasoning,
  active,
}: {
  reasoning: string;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  // Auto-expand while the model is thinking; auto-collapse when it finishes.
  useEffect(() => {
    setExpanded(active);
  }, [active]);

  if (!reasoning) return null;

  return (
    <div className="w-full rounded-lg border border-border/50 bg-muted/30 text-sm">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-muted-foreground"
      >
        <Brain className="h-4 w-4" />
        <span className="font-medium">Thoughts</span>
        <ChevronDown
          className={cn("ml-auto h-4 w-4 transition-transform", expanded && "rotate-180")}
        />
      </button>
      {expanded && (
        <div className="whitespace-pre-wrap px-3 pb-3 font-mono text-xs text-muted-foreground">
          {reasoning}
        </div>
      )}
    </div>
  );
}
