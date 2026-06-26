import { useEffect, useRef, useState } from "react";
import { ArrowUp, Mic, MicOff, Paperclip, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  AttachmentPreview,
  type PendingAttachment,
} from "@/components/chat/AttachmentPreview";
import { useVoiceInput } from "@/hooks/useVoiceInput";
import { cn, generateId, routeChatAttachment, SUPPORTED_FILE_TYPES } from "@/lib/utils";
import type { Attachment } from "@/types/chat";

interface InputBarProps {
  onSend: (
    message: string,
    inlineAttachments: Attachment[],
    /** The raw File objects for heavy / ingest-routed attachments.  The
     *  receiver uploads these via POST /attachments (§6.1) before the
     *  chat turn so the resolved Attachment record (with authoritative
     *  `ingested`) arrives before the model sees the message. */
    ingestFiles: File[],
  ) => void;
  isStreaming: boolean;
  onAbort: () => void;
}

export function InputBar({ onSend, isStreaming, onAbort }: InputBarProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    isRecording,
    transcript,
    startRecording,
    stopRecording,
    isSupported: voiceSupported,
  } = useVoiceInput();

  // Auto-grow the textarea.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  // Pipe voice transcript into the input.
  useEffect(() => {
    if (isRecording && transcript) setText(transcript);
  }, [transcript, isRecording]);

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const next = Array.from(files).map((file) => {
      const decision = routeChatAttachment(file);
      return {
        id: generateId(),
        file,
        route: decision.route,
        message:
          decision.route === "ingest"
            ? decision.note
            : decision.route === "reject"
              ? decision.reason
              : undefined,
      };
    });
    setAttachments((prev) => [...prev, ...next]);
  };

  const handleSend = () => {
    const trimmed = text.trim();
    const inline = attachments.filter((a) => a.route === "inline");
    const ingest = attachments.filter((a) => a.route === "ingest");
    if (!trimmed && inline.length === 0 && ingest.length === 0) return;
    if (isStreaming) return;

    const inlineAttachments: Attachment[] = inline.map((a) => ({
      id: a.id,
      fileName: a.file.name,
      fileType: a.file.type || "application/octet-stream",
      fileSize: a.file.size,
      url: "#",
    }));
    // Ingested files still appear in the message bubble (marked "Indexed"), so
    // the attachment is visible even though it went to retrieval, not context.
    const ingestAttachments: Attachment[] = ingest.map((a) => ({
      id: a.id,
      fileName: a.file.name,
      fileType: a.file.type || "application/octet-stream",
      fileSize: a.file.size,
      url: "#",
      ingested: true,
    }));
    // Pass the raw File objects — the receiver uploads them to
    // POST /sessions/:id/attachments first, then starts the chat turn
    // with the authority-resolved attachment records.
    const ingestFiles: File[] = ingest.map((a) => a.file);
    onSend(trimmed, [...inlineAttachments, ...ingestAttachments], ingestFiles);
    setText("");
    setAttachments([]);
    if (isRecording) stopRecording();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <TooltipProvider delayDuration={300}>
      <div className="mx-auto w-full max-w-3xl px-4 pb-4">
        <div className="rounded-2xl border border-border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring">
          <AttachmentPreview
            attachments={attachments}
            onRemove={(id) =>
              setAttachments((prev) => prev.filter((a) => a.id !== id))
            }
          />
          <Textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder="Ask anything about your knowledge base…"
            className="max-h-48 min-h-0 resize-none border-0 bg-transparent px-2 py-1.5 shadow-none focus-visible:ring-0"
          />
          <div className="flex items-center justify-between px-1 pt-1">
            <div className="flex items-center gap-1">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                accept={SUPPORTED_FILE_TYPES.join(",")}
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => fileInputRef.current?.click()}
                    aria-label="Attach files"
                  >
                    <Paperclip className="size-5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Attach files</TooltipContent>
              </Tooltip>

              {voiceSupported && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={isRecording ? stopRecording : startRecording}
                      aria-label={
                        isRecording ? "Stop recording" : "Start voice input"
                      }
                      className={cn(
                        isRecording && "text-brand-red",
                      )}
                    >
                      {isRecording ? (
                        <MicOff className="size-5" />
                      ) : (
                        <Mic className="size-5" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {isRecording ? "Stop recording" : "Voice input"}
                  </TooltipContent>
                </Tooltip>
              )}
            </div>

            {isStreaming ? (
              <Button
                size="icon"
                variant="secondary"
                onClick={onAbort}
                aria-label="Stop generating"
                className="rounded-full"
              >
                <Square className="size-4 fill-current" />
              </Button>
            ) : (
              <Button
                size="icon"
                onClick={handleSend}
                disabled={
                  !text.trim() &&
                  attachments.every((a) => a.route === "reject")
                }
                aria-label="Send message"
                className="rounded-full"
              >
                <ArrowUp className="size-5" />
              </Button>
            )}
          </div>
        </div>
        <p className="px-2 pt-1.5 text-center text-xs text-muted-foreground">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </TooltipProvider>
  );
}
