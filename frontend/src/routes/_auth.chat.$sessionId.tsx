import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Database, FileCode, Loader2 } from "lucide-react";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { StepTracker } from "@/components/chat/StepTracker";
import { InputBar } from "@/components/chat/InputBar";
import { SourcesDrawer } from "@/components/chat/SourcesDrawer";
import { ChunkingProgress } from "@/components/chat/ChunkingProgress";
import { ArtifactCanvas } from "@/components/chat/ArtifactCanvas";
import { ArtifactInlineCard } from "@/components/chat/ArtifactInlineCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useStreamChat } from "@/hooks/useStreamChat";
import { useSessionIngestion } from "@/hooks/useSessionIngestion";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { useMessages, useSession } from "@/lib/queries";
import * as api from "@/lib/api";
import type { ArtifactSummary, Attachment } from "@/types/chat";

export default function ChatRoute() {
  const { sessionId = "" } = useParams();
  const { data: session } = useSession(sessionId);
  const { data: messages, isLoading } = useMessages(sessionId);
  const { sendMessage, isStreaming, steps, streamedContent, currentArtifact, reset, abort } =
    useStreamChat(sessionId);
  const { upload, tasks: ingestTasks } = useSessionIngestion(sessionId);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [canvasOpen, setCanvasOpen] = useState(false);

  // Artifacts from the listing endpoint (reload).
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [latestArtifact, setLatestArtifact] = useState<ArtifactSummary | null>(null);

  // Track whether auto-open for the current stream event has fired.
  const autoOpenFiredRef = useRef(false);

  const { scrollRef, scrollToBottom } = useAutoScroll(
    `${messages?.length}-${streamedContent.length}-${steps.length}-${ingestTasks.length}`,
  );

  // Reset transient streaming UI when switching sessions.
  useEffect(() => {
    reset();
    setArtifacts([]);
    setLatestArtifact(null);
    setCanvasOpen(false);
    autoOpenFiredRef.current = false;
  }, [sessionId, reset]);

  // Load artifacts on mount / session change (reload).
  useEffect(() => {
    let cancelled = false;
    api.getArtifacts(sessionId).then((list) => {
      if (cancelled) return;
      setArtifacts(list);
      if (list.length > 0) {
        const latest = list.reduce((a, b) =>
          a.latestVersion > b.latestVersion ? a : b,
        );
        setLatestArtifact(latest);
      }
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  // Auto-open canvas when a new artifact arrives via SSE, and always update
  // the latest artifact state (so v2 of the same artifact overwrites v1).
  useEffect(() => {
    if (currentArtifact) {
      setLatestArtifact(currentArtifact);
      setArtifacts((prev) => {
        const idx = prev.findIndex((a) => a.id === currentArtifact.id);
        if (idx === -1) return [...prev, currentArtifact];
        const copy = [...prev];
        copy[idx] = currentArtifact;
        return copy;
      });
      // Only fire auto-open once per stream burst; v2 updating the canvas
      // is handled above regardless of this ref.
      if (!autoOpenFiredRef.current) {
        autoOpenFiredRef.current = true;
        setCanvasOpen(true);
      }
    }
    if (!currentArtifact) {
      autoOpenFiredRef.current = false;
    }
  }, [currentArtifact]);

  // Derived: which artifact to show in the canvas (SSE latest takes priority).
  const canvasArtifact = latestArtifact;

  const openCanvas = useCallback((artifact: ArtifactSummary) => {
    setLatestArtifact(artifact);
    setCanvasOpen(true);
  }, []);

  const handleSend = async (message: string, files: File[]) => {
    // §6.1: upload every attachment first (multipart → SSE).  The backend
    // authoritatively routes each file inline-vs-ingest, streams chunk_progress
    // for the heavy ones, and yields the resolved Attachment records (real id,
    // url, and `ingested` flag).  We send those with the chat turn so the bubble
    // renders their chips and the model sees inline file text.
    let resolved: Attachment[] = [];
    if (files.length > 0) {
      resolved = await upload(files);
    }
    void sendMessage(message, resolved);
    requestAnimationFrame(() => scrollToBottom());
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <h1 className="truncate font-medium">
          {session?.title ?? "Chat"}
        </h1>
        <div className="flex items-center gap-2">
          {artifacts.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCanvasOpen(true)}
              className="gap-1.5"
            >
              <FileCode className="size-4" />
              Artifacts
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setSourcesOpen(true)}
            className="gap-1.5"
          >
            <Database className="size-4" />
            Sources
          </Button>
        </div>
      </header>

      {/* Message feed */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-5 px-4 py-6">
          {isLoading && (
            <div className="space-y-4">
              <Skeleton className="ml-auto h-12 w-2/3" />
              <Skeleton className="h-24 w-3/4" />
              <Skeleton className="ml-auto h-12 w-1/2" />
            </div>
          )}

          {messages?.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}

          {ingestTasks.map((task) => (
            <ChunkingProgress
              key={task.fileName}
              fileName={task.fileName}
              progress={task.progress}
              chunkCount={task.chunkCount}
              total={task.total}
            />
          ))}

          {isStreaming && steps.length > 0 && (
            <StepTracker steps={steps} active={isStreaming} />
          )}

          {isStreaming && streamedContent && (
            <div className="flex w-full gap-3">
              <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-red text-xs font-medium text-white">
                AI
              </div>
              <div className="max-w-[min(48rem,85%)] rounded-2xl rounded-bl-md bg-muted px-4 py-2.5 text-sm">
                <div className="prose-chat">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {streamedContent}
                  </ReactMarkdown>
                </div>
                <Loader2 className="mt-1 size-3.5 animate-spin text-muted-foreground" />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Inline artifact cards */}
      {artifacts.length > 0 && (
        <div className="shrink-0 border-t border-border px-4 py-2">
          <div className="flex flex-wrap gap-2">
            {artifacts.map((a) => (
              <ArtifactInlineCard
                key={a.id}
                artifact={a}
                onOpen={() => openCanvas(a)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Composer */}
      <div className="shrink-0">
        <InputBar
          onSend={handleSend}
          isStreaming={isStreaming}
          onAbort={abort}
        />
      </div>

      <SourcesDrawer
        sessionId={sessionId}
        open={sourcesOpen}
        onOpenChange={setSourcesOpen}
      />

      <ArtifactCanvas
        sessionId={sessionId}
        artifact={canvasArtifact}
        open={canvasOpen}
        onOpenChange={setCanvasOpen}
      />
    </div>
  );
}
