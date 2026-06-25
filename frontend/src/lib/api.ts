/**
 * API layer. Every function delegates to `mock.ts` — no real network calls are
 * made. To connect a real backend, swap the mock calls here for `fetch`.
 */
import type { AuthResponse, LoginCredentials, User } from "@/types/api";
import type { Attachment, Message, Session, StreamEvent } from "@/types/chat";
import type {
  FileStatus,
  KBFilters,
  KnowledgeBaseFile,
} from "@/types/kb";
import * as mock from "@/lib/mock";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// --- Auth ---------------------------------------------------------- //
export const login = (credentials: LoginCredentials): Promise<AuthResponse> =>
  mock.mockLogin(credentials);

export const getMe = (token: string | null): Promise<User> =>
  mock.mockGetMe(token);

export const logout = (): Promise<void> => mock.mockLogout();

// --- Sessions ------------------------------------------------------ //
export const getSessions = (): Promise<Session[]> => mock.mockGetSessions();

export const getSession = (id: string): Promise<Session> =>
  mock.mockGetSession(id);

export const createSession = (): Promise<Session> => mock.mockCreateSession();

export const renameSession = (id: string, title: string): Promise<Session> =>
  mock.mockRenameSession(id, title);

export const deleteSession = (id: string): Promise<void> =>
  mock.mockDeleteSession(id);

export const getMessages = (sessionId: string): Promise<Message[]> =>
  mock.mockGetMessages(sessionId);

export function streamChat(
  sessionId: string,
  message: string,
  attachments?: Attachment[],
): AsyncGenerator<StreamEvent> {
  return mock.mockStreamChat(sessionId, message, attachments);
}

// --- Knowledge base ------------------------------------------------ //
export const getKnowledgeBaseFiles = (
  filters?: KBFilters,
): Promise<KnowledgeBaseFile[]> => mock.mockGetKBFiles(filters);

export const uploadKnowledgeBaseFile = (
  file: File,
  onProgress?: (progress: number, status: FileStatus | "uploading") => void,
): Promise<KnowledgeBaseFile> => mock.mockUploadKBFile(file, onProgress);

export const deleteKnowledgeBaseFile = (id: string): Promise<void> =>
  mock.mockDeleteKBFile(id);

export const reindexKnowledgeBaseFile = (
  id: string,
): Promise<KnowledgeBaseFile> => mock.mockReindexKBFile(id);

export const updateFileTags = (
  id: string,
  tags: string[],
): Promise<KnowledgeBaseFile> => mock.mockUpdateFileTags(id, tags);

// --- Session-scoped files (heavy chat attachments) ---------------- //
export const getSessionFiles = (
  sessionId: string,
): Promise<KnowledgeBaseFile[]> => mock.mockGetSessionFiles(sessionId);

export const promoteSessionFile = (
  sessionId: string,
  fileId: string,
): Promise<KnowledgeBaseFile> =>
  mock.mockPromoteSessionFile(sessionId, fileId);
