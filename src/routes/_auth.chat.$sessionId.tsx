import { useEffect, useState } from "react";
import { useParams } from "react-router";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Database, Loader2 } from "lucide-react";

import { MessageBubble } from "@/components/chat/MessageBubble";
import { StepTracker } from "@/components/chat/StepTracker";
import { InputBar } from "@/components/chat/InputBar";
import { SourcesDrawer } from "@/components/chat/SourcesDrawer";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useStreamChat } from "@/hooks/useStreamChat";
import { useAutoScroll } from "@/hooks/useAutoScroll";
import { useMessages, useSession } from "@/lib/queries";
import type { Attachment } from "@/types/chat";

export default function ChatRoute() {
  const { sessionId = "" } = useParams();
  const { data: session } = useSession(sessionId);
  const { data: messages, isLoading } = useMessages(sessionId);
  const { sendMessage, isStreaming, steps, streamedContent, reset, abort } =
    useStreamChat(sessionId);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  const { scrollRef, scrollToBottom } = useAutoScroll(
    `${messages?.length}-${streamedContent.length}-${steps.length}`,
  );

  // Reset transient streaming UI when switching sessions.
  useEffect(() => {
    reset();
  }, [sessionId, reset]);

  const handleSend = (message: string, attachments: Attachment[]) => {
    void sendMessage(message, attachments);
    requestAnimationFrame(() => scrollToBottom());
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
        <h1 className="truncate font-medium">
          {session?.title ?? "Chat"}
        </h1>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setSourcesOpen(true)}
          className="gap-1.5"
        >
          <Database className="size-4" />
          Sources
        </Button>
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

      {/* Composer */}
      <div className="shrink-0">
        <InputBar
          onSend={handleSend}
          isStreaming={isStreaming}
          onAbort={abort}
        />
      </div>

      <SourcesDrawer open={sourcesOpen} onOpenChange={setSourcesOpen} />
    </div>
  );
}
