import { useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { fetchAttachmentObjectUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

interface AuthedImageProps {
  sessionId: string;
  attachmentId: string;
  alt?: string;
  className?: string;
  /** When true, clicking opens the image in a new tab. */
  openOnClick?: boolean;
}

/**
 * Renders a persisted attachment image by fetching its bytes with the Bearer
 * auth token (plain <img src> cannot set that header and would 401). Shows a
 * skeleton while loading and a fallback text on error. Revokes the object URL
 * on unmount to avoid memory leaks.
 */
export function AuthedImage({
  sessionId,
  attachmentId,
  alt = "attachment",
  className,
  openOnClick,
}: AuthedImageProps) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;

    fetchAttachmentObjectUrl(sessionId, attachmentId)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setSrc(url);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sessionId, attachmentId]);

  if (error) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-lg bg-muted text-xs text-muted-foreground",
          className,
        )}
      >
        Image unavailable
      </div>
    );
  }

  if (!src) {
    return <Skeleton className={cn("rounded-lg", className)} />;
  }

  return (
    <img
      src={src}
      alt={alt}
      className={cn(
        "rounded-lg object-cover",
        openOnClick && "cursor-pointer",
        className,
      )}
      onClick={openOnClick ? () => window.open(src, "_blank") : undefined}
    />
  );
}
