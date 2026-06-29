import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import {
  formatDistanceToNow,
  isToday,
  isYesterday,
  isThisWeek,
} from "date-fns";

/** Merge class names with Tailwind conflict resolution. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** "2 hours ago", "Yesterday", "Just now". */
export function formatRelativeTime(date: Date | string): string {
  const d = typeof date === "string" ? new Date(date) : date;
  if (isToday(d)) {
    const diffMs = Date.now() - d.getTime();
    if (diffMs < 60_000) return "Just now";
    return formatDistanceToNow(d, { addSuffix: true });
  }
  if (isYesterday(d)) return "Yesterday";
  return formatDistanceToNow(d, { addSuffix: true });
}

/** "1.5 MB", "340 KB", "12 bytes". */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit++;
  }
  return `${size.toFixed(size < 10 ? 1 : 0)} ${units[unit]}`;
}

/** "John Doe" -> "JD", "alex" -> "AL". */
export function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export type DateGroupLabel = "Today" | "Yesterday" | "Last 7 Days" | "Older";

/** Group items into Today / Yesterday / Last 7 Days / Older buckets. */
export function groupByDate<T>(
  items: T[],
  dateKey: (item: T) => Date | string,
): { label: DateGroupLabel; items: T[] }[] {
  const buckets: Record<DateGroupLabel, T[]> = {
    Today: [],
    Yesterday: [],
    "Last 7 Days": [],
    Older: [],
  };
  for (const item of items) {
    const raw = dateKey(item);
    const d = typeof raw === "string" ? new Date(raw) : raw;
    if (isToday(d)) buckets.Today.push(item);
    else if (isYesterday(d)) buckets.Yesterday.push(item);
    else if (isThisWeek(d, { weekStartsOn: 1 })) buckets["Last 7 Days"].push(item);
    else buckets.Older.push(item);
  }
  const order: DateGroupLabel[] = ["Today", "Yesterday", "Last 7 Days", "Older"];
  return order
    .map((label) => ({ label, items: buckets[label] }))
    .filter((g) => g.items.length > 0);
}

export const SUPPORTED_FILE_TYPES = [
  ".pdf",
  ".md",
  ".txt",
  ".docx",
  ".doc",
  ".csv",
  ".json",
  ".png",
  ".jpg",
  ".jpeg",
] as const;

/** Whether a filename has a supported extension. */
export function isValidFileType(fileName: string): boolean {
  const lower = fileName.toLowerCase();
  return SUPPORTED_FILE_TYPES.some((ext) => lower.endsWith(ext));
}

const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg"] as const;

/** Whether a filename is an image (sent to the model as a vision block). */
export function isImageFile(fileName: string): boolean {
  const lower = fileName.toLowerCase();
  return IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

// Hard ceiling for anything the composer will accept — purely a sanity guard.
export const MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024; // 100 MB

// Image ceiling — matches backend max_image_bytes.
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024; // 10 MB

// Inline routing threshold for documents. NOTE: in the real backend the inline-
// vs-ingest decision is made on *token count after parsing* (bytes ≠ tokens — an
// image-heavy PDF can be 30 MB yet only a few thousand tokens). The frontend
// can't count tokens, so this byte size is only a crude demo proxy + sanity
// ceiling; the authoritative routing happens server-side.
export const MAX_INLINE_DOC_BYTES = 2 * 1024 * 1024; // 2 MB

export type AttachmentRoute =
  | { route: "inline" }
  | { route: "ingest"; note: string }
  | { route: "reject"; reason: string };

/**
 * Decide how a file dropped into the chat composer should be handled:
 * - `inline`  — small enough to send straight into the model's context
 * - `ingest`  — too big to inline; gets session-scoped ingestion (retrievable
 *               in this chat only, NOT added to the global Knowledge Base)
 * - `reject`  — unsupported type, or past the sanity ceiling
 */
export function routeChatAttachment(file: {
  name: string;
  size: number;
}): AttachmentRoute {
  if (!isValidFileType(file.name)) {
    return { route: "reject", reason: "Unsupported file type" };
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    return { route: "reject", reason: "File too large (max 100 MB)" };
  }
  // Images go inline as vision blocks — but reject if they exceed the backend cap.
  if (isImageFile(file.name)) {
    if (file.size > MAX_IMAGE_BYTES) {
      return { route: "reject", reason: "Image too large (max 10 MB)" };
    }
    return { route: "inline" };
  }
  if (file.size > MAX_INLINE_DOC_BYTES) {
    return { route: "ingest", note: "Indexed for this chat" };
  }
  return { route: "inline" };
}

/** RFC4122-ish v4 UUID generator. */
export function generateId(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
