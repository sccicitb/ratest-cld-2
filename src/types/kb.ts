export type FileStatus = "indexing" | "ready" | "error";

export interface KnowledgeBaseFile {
  id: string;
  name: string;
  size: number;
  uploadDate: string;
  chunkCount: number;
  status: FileStatus;
  tags: string[];
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
