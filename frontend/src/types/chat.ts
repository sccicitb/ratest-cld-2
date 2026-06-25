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

export type StreamEvent =
  | StepEvent
  | TokenEvent
  | ChunkProgressEvent
  | DoneEvent
  | ErrorEvent;
