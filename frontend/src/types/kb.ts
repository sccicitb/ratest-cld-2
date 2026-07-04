export type FileStatus = "indexing" | "ready" | "error";

/**
 * Where an ingested file lives:
 * - `kb`      — the persistent, curated Knowledge Base (shown on the KB page,
 *               retrievable from every chat).
 * - `session` — ingested from a chat attachment that was too big to inline;
 *               retrievable only within that conversation, not shown on the KB page.
 */
export type FileScope = "kb" | "session";

export interface KnowledgeBaseFile {
  id: string;
  name: string;
  size: number;
  uploadDate: string;
  chunkCount: number;
  status: FileStatus;
  tags: string[];
  scope?: FileScope; // defaults to "kb"
  groupId: string | null;
  isPublic: boolean;
}

export type UploadTaskStatus =
  | "queued"
  | "uploading"
  | "processing"
  | "indexing"
  | "done"
  | "error";

export interface UploadTask {
  id: string;
  file: File;
  progress: number;
  status: UploadTaskStatus;
  error?: string;
}

export interface KBFilters {
  search?: string;
  status?: FileStatus;
  tag?: string;
}
