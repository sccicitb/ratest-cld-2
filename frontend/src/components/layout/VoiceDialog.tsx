import { useState } from "react";
import { Check, Loader2, Volume2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { speakText, updateMe } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";

/** Mirrors backend VOICES / voice/service/tts.py. */
const VOICES = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"];

/** One short line, so every voice is judged on the same words. */
const PREVIEW = "Halo, saya Citya. Ada yang bisa saya bantu?";

/**
 * Pick the voice used when reading answers aloud.
 *
 * Selecting a voice saves it and plays a sample in one action. That is
 * deliberate rather than a Save button: `/api/voice/speak` takes no voice
 * parameter (the voice is server-side scope, resolved from the caller), so a
 * preview can only ever play the *saved* voice. A separate Save step would
 * mean previewing a voice silently persisted it and "Cancel" was a lie.
 */
export function VoiceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const user = useAuthStore((s) => s.user);
  const accessToken = useAuthStore((s) => s.accessToken);
  const setAuth = useAuthStore((s) => s.setAuth);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const choose = async (voice: string) => {
    setBusy(voice);
    setError(null);
    try {
      const updated = await updateMe({ voice });
      if (accessToken) setAuth(updated, accessToken);
      const blob = await speakText(PREVIEW);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      audio.onerror = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch {
      setError("Could not play that voice. It is still saved.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Voice</DialogTitle>
          <DialogDescription>
            Used when reading answers aloud. Choosing a voice saves it and plays
            a sample.
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-2">
          {VOICES.map((v) => {
            const selected = user?.voice === v;
            return (
              <Button
                key={v}
                type="button"
                variant="outline"
                aria-pressed={selected}
                disabled={busy !== null}
                onClick={() => choose(v)}
                className={cn(
                  "justify-between",
                  selected && "border-brand-red text-brand-red",
                )}
              >
                <span>{v}</span>
                {busy === v ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : selected ? (
                  <Check className="size-4" />
                ) : (
                  <Volume2 className="size-4 opacity-50" />
                )}
              </Button>
            );
          })}
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
      </DialogContent>
    </Dialog>
  );
}
