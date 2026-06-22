import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { motion } from "framer-motion";
import { FileText, Volume2, VolumeX } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { useVoiceSynthesis } from "@/hooks/useVoiceSynthesis";
import { cn, formatFileSize } from "@/lib/utils";
import type { Attachment, Message } from "@/types/chat";

function AttachmentChip({ attachment }: { attachment: Attachment }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-background/60 px-3 py-2 text-sm">
      <FileText className="size-4 shrink-0 text-brand-blue" />
      <span className="truncate font-medium">{attachment.fileName}</span>
      <span className="shrink-0 text-xs text-muted-foreground">
        {formatFileSize(attachment.fileSize)}
      </span>
    </div>
  );
}

export const MessageBubble = memo(function MessageBubble({
  message,
}: {
  message: Message;
}) {
  const { speak, stop, isSpeaking, isSupported } = useVoiceSynthesis();
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  if (isSystem) {
    return (
      <div className="mx-auto my-2 max-w-2xl rounded-md bg-muted px-3 py-2 text-center text-xs text-muted-foreground">
        {message.content}
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={cn(
        "flex w-full gap-3",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      {!isUser && (
        <Avatar className="mt-0.5 size-8 shrink-0">
          <AvatarFallback className="bg-brand-red text-xs text-white">
            AI
          </AvatarFallback>
        </Avatar>
      )}

      <div
        className={cn(
          "flex max-w-[min(48rem,85%)] flex-col gap-2",
          isUser ? "items-end" : "items-start",
        )}
      >
        {message.attachments && message.attachments.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {message.attachments.map((a) => (
              <AttachmentChip key={a.id} attachment={a} />
            ))}
          </div>
        )}

        {message.content && (
          <div
            className={cn(
              "rounded-2xl px-4 py-2.5 text-sm",
              isUser
                ? "rounded-br-md bg-primary text-primary-foreground"
                : "rounded-bl-md bg-muted text-foreground",
            )}
          >
            {isUser ? (
              <p className="whitespace-pre-wrap">{message.content}</p>
            ) : (
              <div className="prose-chat">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {message.content}
                </ReactMarkdown>
              </div>
            )}
          </div>
        )}

        {!isUser && isSupported && message.content && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2 text-xs text-muted-foreground"
            onClick={() => (isSpeaking ? stop() : speak(message.content))}
          >
            {isSpeaking ? (
              <VolumeX className="size-3.5" />
            ) : (
              <Volume2 className="size-3.5" />
            )}
            {isSpeaking ? "Stop" : "Read aloud"}
          </Button>
        )}
      </div>
    </motion.div>
  );
});
