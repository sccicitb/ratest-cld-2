import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Brain,
  Check,
  ChevronDown,
  Loader2,
  Search,
  Sparkles,
  Wrench,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { PipelineStep } from "@/types/chat";
import type { StepState } from "@/hooks/useStreamChat";

const STEP_META: Record<
  PipelineStep,
  { label: string; icon: typeof Brain }
> = {
  thinking: { label: "Thinking", icon: Brain },
  retrieving_context: { label: "Retrieving context", icon: Search },
  calling_tool: { label: "Calling tool", icon: Wrench },
  generating_response: { label: "Generating response", icon: Sparkles },
};

export function StepTracker({
  steps,
  active,
}: {
  steps: StepState[];
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  if (steps.length === 0) return null;

  const allComplete = steps.every((s) => s.status === "complete");

  return (
    <div className="flex w-full gap-3">
      <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-red text-xs font-medium text-white">
        AI
      </div>
      <div className="w-full max-w-[min(48rem,85%)] overflow-hidden rounded-2xl rounded-bl-md border border-border bg-card">
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="flex w-full items-center justify-between px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/50"
        >
          <span className="flex items-center gap-2">
            {allComplete ? (
              <Check className="size-3.5 text-emerald-500" />
            ) : (
              <Loader2 className="size-3.5 animate-spin text-brand-blue" />
            )}
            {allComplete ? "Pipeline complete" : "Running pipeline…"}
          </span>
          <ChevronDown
            className={cn(
              "size-4 transition-transform",
              !expanded && "-rotate-90",
            )}
          />
        </button>

        <AnimatePresence initial={false}>
          {expanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <ul className="space-y-1 border-t border-border px-3 py-2">
                {steps.map((step) => {
                  const meta = STEP_META[step.step];
                  const Icon = meta.icon;
                  const isActive = step.status === "active" && active;
                  const query =
                    typeof step.toolArgs?.query === "string"
                      ? step.toolArgs.query
                      : undefined;
                  const scope =
                    typeof step.toolArgs?.scope === "string"
                      ? step.toolArgs.scope
                      : undefined;
                  return (
                    <li
                      key={step.id ?? step.step}
                      className="flex items-start gap-2.5 text-sm"
                    >
                      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center">
                        {step.status === "complete" ? (
                          <Check className="size-4 text-emerald-500" />
                        ) : isActive ? (
                          <Loader2 className="size-4 animate-spin text-brand-blue" />
                        ) : (
                          <Icon className="size-4 text-muted-foreground" />
                        )}
                      </span>
                      <div className="min-w-0">
                        <span
                          className={cn(
                            "font-medium",
                            step.status === "active" && active
                              ? "text-foreground"
                              : "text-muted-foreground",
                          )}
                        >
                          {meta.label}
                        </span>
                        {step.toolName && (
                          <code className="ml-1.5 rounded bg-muted px-1.5 py-0.5 text-xs text-foreground">
                            {step.toolName}
                          </code>
                        )}
                        {query && (
                          <span className="ml-1.5 truncate text-xs text-muted-foreground">
                            “{query}”
                          </span>
                        )}
                        {scope && (
                          <span className="ml-1.5 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                            scope: {scope}
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
