import { useEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Mic, MicOff, Paperclip, Square } from "lucide-react";

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
import { useVoiceCapabilities } from "@/lib/queries";
import { cn, generateId, routeChatAttachment, SUPPORTED_FILE_TYPES } from "@/lib/utils";

interface InputBarProps {
  /** The receiver uploads every file via POST /sessions/:id/attachments
   *  (§6.1) before the chat turn; the backend authoritatively routes each
   *  one inline-vs-ingest and returns the resolved Attachment records, which
   *  are then sent with the message. */
  onSend: (message: string, files: File[]) => void;
  isStreaming: boolean;
  onAbort: () => void;
  /**
   * True when this room already has a live turn running elsewhere (backend
   * `activeTurn`, before/without us having attached to it via resume()).
   * Locks the send control the same way `isStreaming` does, without
   * swapping to the abort button — there's nothing local to abort yet.
   */
  locked?: boolean;
}

export function InputBar({ onSend, isStreaming, onAbort, locked }: InputBarProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    isRecording,
    isTranscribing,
    isSupported: voiceSupported,
    startRecording,
    stopRecording,
  } = useVoiceInput();
  const { data: voiceCaps } = useVoiceCapabilities();
  const micEnabled = voiceSupported && !!voiceCaps?.stt;

  // Auto-grow the textarea.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  const handleMic = async () => {
    if (isRecording) {
      const text = await stopRecording();
      // Append rather than replace: the user may have typed before recording.
      if (text) setText((prev) => (prev ? `${prev} ${text}` : text));
    } else {
      void startRecording();
    }
  };

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
    // Anything not rejected by the client-side sanity check gets uploaded;
    // the backend makes the authoritative inline-vs-ingest decision.
    const sendable = attachments.filter((a) => a.route !== "reject");
    if (!trimmed && sendable.length === 0) return;
    if (isStreaming || locked) return;

    // Pass the raw File objects — the receiver uploads them all to
    // POST /sessions/:id/attachments, then starts the chat turn with the
    // authority-resolved attachment records.
    onSend(trimmed, sendable.map((a) => a.file));
    setText("");
    setAttachments([]);
    if (isRecording) void stopRecording();
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

              {micEnabled && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={handleMic}
                      disabled={isTranscribing}
                      aria-label={
                        isRecording ? "Stop recording" : "Start voice input"
                      }
                      className={cn(
                        isRecording && "text-brand-red",
                      )}
                    >
                      {isTranscribing ? (
                        <Loader2 className="size-5 animate-spin" />
                      ) : isRecording ? (
                        <MicOff className="size-5" />
                      ) : (
                        <Mic className="size-5" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {isTranscribing
                      ? "Transcribing…"
                      : isRecording
                        ? "Stop recording"
                        : "Voice input"}
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
                  locked ||
                  (!text.trim() &&
                    attachments.every((a) => a.route === "reject"))
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
