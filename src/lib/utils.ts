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
