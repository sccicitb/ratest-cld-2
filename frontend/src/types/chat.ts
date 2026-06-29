import type { KnowledgeBaseFile } from "./kb";

export interface Session {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export type MessageRole = "user" | "assistant" | "system";

export interface Attachment {
  id: string;
  fileName: string;
  fileType: string;
  fileSize: number;
  url: string;
  thumbnailUrl?: string;
  /**
   * True when the file was too big to inline and was ingested session-scoped
   * (retrieved via the search tool) rather than placed directly in context.
   */
  ingested?: boolean;
}

export interface Message {
  id: string;
  sessionId: string;
  role: MessageRole;
  content: string;
  attachments?: Attachment[];
  createdAt: string;
}

export type PipelineStep =
  | "thinking"
  | "retrieving_context"
  | "calling_tool"
  | "generating_response";

export type StepStatus = "active" | "complete";

export interface StepEvent {
  type: "step";
  step: PipelineStep;
  status: StepStatus;
  /**
   * Unique id for steps that can occur multiple times in one turn (e.g. an
   * agentic model calling `search_knowledge_base` more than once). The same id
   * is reused for the matching `active`/`complete` pair. Omitted for the
   * single-occurrence pipeline steps, which are keyed by `step`.
   */
  id?: string;
  toolName?: string;
  toolArgs?: Record<string, unknown>;
}

export interface TokenEvent {
  type: "token";
  content: string;
}

export interface ChunkProgressEvent {
  type: "chunk_progress";
  fileName: string;
  progress: number;
  chunkCount: number;
  total: number;
}

export interface DoneEvent {
  type: "done";
  messageId: string;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export interface ArtifactEvent {
  type: "artifact";
  artifactId: string;
  version: number;
  title: string;
}

export type StreamEvent =
  | StepEvent
  | TokenEvent
  | ChunkProgressEvent
  | DoneEvent
  | ErrorEvent
  | ArtifactEvent;

/** Returned by GET /api/sessions/{sid}/artifacts */
export interface ArtifactSummary {
  id: string;
  title: string;
  latestVersion: number;
  createdAt: string;
}

// --- Upload-stream events (§3 of the backend spec) — separate vocabulary ---
// These come from the multipart→SSE upload endpoints (attachment upload §6.1
// and KB upload §8.3), NOT from the chat SSE stream. They share
// `ChunkProgressEvent` with the chat union above but have their own terminal
// events.

export interface AttachmentResolvedEvent {
  type: "attachment_resolved";
  attachment: Attachment;
}

export interface FileResolvedEvent {
  type: "file_resolved";
  file: KnowledgeBaseFile;
}

export type UploadStreamEvent =
  | ChunkProgressEvent
  | AttachmentResolvedEvent
  | FileResolvedEvent
  | { type: "done" }
  | { type: "error"; message: string };
