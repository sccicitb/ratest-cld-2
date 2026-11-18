import { useMemo } from "react";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";
import { SimulationResultCard } from "@/components/chat/SimulationResultCard";
import { parseSimulationReports } from "@/lib/simulation";

/** Both streaming and history use message-owned results, never the session snapshot. */
export function AssistantContent({
  content,
  simulationResults,
}: {
  content: string;
  simulationResults?: unknown;
}) {
  const reports = useMemo(
    () => parseSimulationReports(simulationResults),
    [simulationResults],
  );
  if (reports) {
    return (
      <div className="simulation-answer">
        {reports.map((report, i) => (
          <SimulationResultCard key={i} report={report} />
        ))}
      </div>
    );
  }
  return (
    <div className="min-w-0 max-w-full rounded-2xl rounded-bl-md bg-muted px-4 py-2.5 text-sm">
      <ChatMarkdown>{content}</ChatMarkdown>
    </div>
  );
}
