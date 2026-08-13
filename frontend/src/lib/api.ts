/**
 * API layer — every function does real `fetch` against the backend.
 *
 * The Vite dev server proxies `/api` → backend (§2 of the spec), so the
 * browser sees a single origin and the auth refresh cookie works as SameSite=Lax.
 */
import type { AuthResponse, LoginCredentials, User } from "@/types/api";
import type {
  AdminUser,
  ChangePasswordPayload,
  CreateGroupPayload,
  CreateMcpServerPayload,
  CreateUserPayload,
  Group,
  GroupDetail,
  MCPServer,
  PatchGroupPayload,
  PatchMcpServerPayload,
  PatchUserPayload,
  TestMcpResult,
} from "@/types/admin";
import type {
  ArtifactSummary,
  Attachment,
  Message,
  Session,
  StreamEvent,
  UploadStreamEvent,
} from "@/types/chat";
import type { FileStatus, KBFilters, KnowledgeBaseFile } from "@/types/kb";
import { sseLines, uploadLines } from "@/lib/storage";

const PREFIX = (path: string) => path;

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

/* ------------------------------------------------------------------ */
/*  Fetch helpers                                                      */
/* ------------------------------------------------------------------ */

// In-memory token — registered by `auth.ts` on login/refresh.
let _token: (() => string | null) | null = null;
export function setTokenSource(fn: () => string | null) { _token = fn; }

function authHeaders(): Record<string, string> {
  const t = _token?.();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

const JSON_H = { "Content-Type": "application/json" };

async function req(path: string, init: RequestInit = {}) {
  const res = await fetch(PREFIX(path), { credentials: "include", ...init });
  if (!res.ok) {
    let body: { message?: string; code?: string } = {};
    try { body = await res.json(); } catch { /* no body */ }
    throw new ApiError(res.status, body.message || res.statusText, body.code);
  }
  return res;
}

/* ------------------------------------------------------------------ */
/*  Auth                                                               */
/* ------------------------------------------------------------------ */

export const login = (credentials: LoginCredentials): Promise<AuthResponse> =>
  req("/api/auth/login", { method: "POST", body: JSON.stringify(credentials), headers: JSON_H })
    .then(r => r.json());

export const getMe = (token: string | null): Promise<User> =>
  req("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } })
    .then(r => r.json());

export const logout = (): Promise<void> =>
  req("/api/auth/logout", { method: "POST" }).then(() => {});

export const refresh = (): Promise<AuthResponse> =>
  req("/api/auth/refresh", { method: "POST" }).then(r => r.json());

/* ------------------------------------------------------------------ */
/*  Sessions                                                           */
/* ------------------------------------------------------------------ */

export const getSessions = (): Promise<Session[]> =>
  req("/api/sessions", { headers: authHeaders() }).then(r => r.json());

export const getSession = (id: string): Promise<Session> =>
  req(`/api/sessions/${id}`, { headers: authHeaders() }).then(r => r.json());

export const createSession = (): Promise<Session> =>
  req("/api/sessions", { method: "POST", headers: authHeaders() }).then(r => r.json());

export const renameSession = (id: string, title: string): Promise<Session> =>
  req(`/api/sessions/${id}`, { method: "PATCH", body: JSON.stringify({ title }), headers: { ...JSON_H, ...authHeaders() } })
    .then(r => r.json());

export const deleteSession = (id: string): Promise<void> =>
  req(`/api/sessions/${id}`, { method: "DELETE", headers: authHeaders() }).then(() => {});

export const getMessages = (sessionId: string): Promise<Message[]> =>
  req(`/api/sessions/${sessionId}/messages`, { headers: authHeaders() }).then(r => r.json());

/* ------------------------------------------------------------------ */
/*  Chat streaming                                                     */
/* ------------------------------------------------------------------ */

export async function* streamChat(
  sessionId: string,
  msg: string,
  attachments?: Attachment[],
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(PREFIX(`/api/sessions/${sessionId}/chat`), {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...authHeaders(),
    },
    body: JSON.stringify({ message: msg, attachments }),
    signal,
  });
  if (!res.ok || !res.body) {
    let info: { message?: string; code?: string } = {};
    try { info = await res.json(); } catch { /* pre-stream error */ }
    throw new ApiError(res.status, info.message || "Chat failed", info.code);
  }
  yield* sseLines(res.body) as AsyncGenerator<StreamEvent>;
}

/**
 * Reattach to a live turn (or replay+tail one that just finished) after
 * navigating away and back. Empty stream if no turn is in flight.
 */
export async function* streamResume(
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const res = await fetch(PREFIX(`/api/sessions/${sessionId}/stream`), {
    method: "GET",
    credentials: "include",
    headers: {
      Accept: "text/event-stream",
      ...authHeaders(),
    },
    signal,
  });
  if (!res.ok || !res.body) {
    let info: { message?: string; code?: string } = {};
    try { info = await res.json(); } catch { /* pre-stream error */ }
    throw new ApiError(res.status, info.message || "Resume failed", info.code);
  }
  yield* sseLines(res.body) as AsyncGenerator<StreamEvent>;
}

/* ------------------------------------------------------------------ */
/*  Knowledge base                                                     */
/* ------------------------------------------------------------------ */

export const getKnowledgeBaseFiles = (filters?: KBFilters): Promise<KnowledgeBaseFile[]> => {
  const p = new URLSearchParams();
  if (filters?.search) p.set("search", filters.search);
  if (filters?.status) p.set("status", filters.status);
  if (filters?.tag) p.set("tag", filters.tag);
  const q = p.toString();
  return req(`/api/knowledge-base${q ? `?${q}` : ""}`, { headers: authHeaders() }).then(r => r.json());
};

export interface KBUploadOpts {
  groupId?: string | null;
  isPublic?: boolean;
  tags?: string[];
}

export async function* uploadKnowledgeBaseFile(
  file: File,
  opts?: KBUploadOpts,
): AsyncGenerator<UploadStreamEvent> {
  const form = new FormData();
  form.append("file", file);
  if (opts?.groupId != null) form.append("group_id", opts.groupId);
  if (opts?.isPublic != null) form.append("is_public", String(opts.isPublic));
  if (opts?.tags && opts.tags.length > 0) form.append("tags", opts.tags.join(","));
  const res = await fetch(PREFIX("/api/knowledge-base/upload"), {
    method: "POST",
    credentials: "include",
    headers: { ...authHeaders(), Accept: "text/event-stream" },
    body: form,
  });
  if (!res.ok || !res.body) {
    let info: { message?: string; code?: string } = {};
    try { info = await res.json(); } catch { /* pre-stream */ }
    throw new ApiError(res.status, info.message || "Upload failed", info.code);
  }
  yield* uploadLines(res.body) as AsyncGenerator<UploadStreamEvent>;
}

export const deleteKnowledgeBaseFile = (id: string): Promise<void> =>
  req(`/api/knowledge-base/${id}`, { method: "DELETE", headers: authHeaders() }).then(() => {});

export const reindexKnowledgeBaseFile = (id: string): Promise<KnowledgeBaseFile> =>
  req(`/api/knowledge-base/${id}/reindex`, { method: "POST", headers: authHeaders() }).then(r => r.json());

export const updateFileTags = (id: string, tags: string[]): Promise<KnowledgeBaseFile> =>
  req(`/api/knowledge-base/${id}/tags`, { method: "PATCH", body: JSON.stringify({ tags }), headers: { ...JSON_H, ...authHeaders() } })
    .then(r => r.json());

/* ------------------------------------------------------------------ */
/*  Attachments — multipart → SSE upload at send (§6.1)                */
/* ------------------------------------------------------------------ */

export async function* uploadChatAttachments(
  sessionId: string,
  files: File[],
): AsyncGenerator<UploadStreamEvent> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  const res = await fetch(PREFIX(`/api/sessions/${sessionId}/attachments`), {
    method: "POST",
    credentials: "include",
    headers: { ...authHeaders(), Accept: "text/event-stream" },
    body: form,
  });
  if (!res.ok || !res.body) {
    let info: { message?: string; code?: string } = {};
    try { info = await res.json(); } catch { /* pre-stream */ }
    throw new ApiError(res.status, info.message || "Upload failed", info.code);
  }
  yield* uploadLines(res.body) as AsyncGenerator<UploadStreamEvent>;
}

/* ------------------------------------------------------------------ */
/*  Session-scoped files                                               */
/* ------------------------------------------------------------------ */

export const getSessionFiles = (sessionId: string): Promise<KnowledgeBaseFile[]> =>
  req(`/api/sessions/${sessionId}/files`, { headers: authHeaders() }).then(r => r.json());

export const promoteSessionFile = (sessionId: string, fileId: string): Promise<KnowledgeBaseFile> =>
  req(`/api/sessions/${sessionId}/files/${fileId}/promote`, { method: "POST", headers: authHeaders() }).then(r => r.json());

/* ------------------------------------------------------------------ */
/*  Attachment raw serving (authed blob fetch)                          */
/* ------------------------------------------------------------------ */

/**
 * Fetch an attachment's raw bytes with the Bearer token (plain <img src>
 * cannot send the auth header and would 401). Returns a revocable object URL.
 * Caller MUST call URL.revokeObjectURL on the returned string when done.
 */
export async function fetchAttachmentObjectUrl(
  sessionId: string,
  attachmentId: string,
): Promise<string> {
  const res = await req(
    `/api/sessions/${sessionId}/attachments/${attachmentId}/raw`,
    { headers: authHeaders() },
  );
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

/* ------------------------------------------------------------------ */
/*  Artifacts                                                          */
/* ------------------------------------------------------------------ */

export const getArtifacts = (sessionId: string): Promise<ArtifactSummary[]> =>
  req(`/api/sessions/${sessionId}/artifacts`, { headers: authHeaders() }).then(
    (r) => r.json(),
  );

export async function fetchArtifactHtml(
  sessionId: string,
  artifactId: string,
  version?: number,
): Promise<string> {
  const qs = version != null ? `?version=${version}` : "";
  const res = await req(
    `/api/sessions/${sessionId}/artifacts/${artifactId}/raw${qs}`,
    { headers: authHeaders() },
  );
  return res.text();
}

/* ------------------------------------------------------------------ */
/*  Admin — Users                                                      */
/* ------------------------------------------------------------------ */

export const adminListUsers = (): Promise<AdminUser[]> =>
  req("/api/admin/users", { headers: authHeaders() }).then((r) => r.json());

export const adminCreateUser = (data: CreateUserPayload): Promise<AdminUser> =>
  req("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(data),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

export const adminPatchUser = (id: string, data: PatchUserPayload): Promise<AdminUser> =>
  req(`/api/admin/users/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

export const adminResetPassword = (id: string): Promise<{ tempPassword: string }> =>
  req(`/api/admin/users/${id}/reset-password`, {
    method: "POST",
    headers: authHeaders(),
  }).then((r) => r.json());

/* ------------------------------------------------------------------ */
/*  Admin — Groups                                                     */
/* ------------------------------------------------------------------ */

export const adminListGroups = (): Promise<Group[]> =>
  req("/api/admin/groups", { headers: authHeaders() }).then((r) => r.json());

export const adminGetGroup = (id: string): Promise<GroupDetail> =>
  req(`/api/admin/groups/${id}`, { headers: authHeaders() }).then((r) => r.json());

export const adminCreateGroup = (data: CreateGroupPayload): Promise<Group> =>
  req("/api/admin/groups", {
    method: "POST",
    body: JSON.stringify(data),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

export const adminPatchGroup = (id: string, data: PatchGroupPayload): Promise<Group> =>
  req(`/api/admin/groups/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

export const adminDeleteGroup = (id: string): Promise<void> =>
  req(`/api/admin/groups/${id}`, { method: "DELETE", headers: authHeaders() }).then(() => {});

export const adminSetGroupMembers = (id: string, userIds: string[]): Promise<GroupDetail> =>
  req(`/api/admin/groups/${id}/members`, {
    method: "PUT",
    body: JSON.stringify({ userIds }),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

export const adminSetGroupServers = (id: string, serverIds: string[]): Promise<GroupDetail> =>
  req(`/api/admin/groups/${id}/mcp-servers`, {
    method: "PUT",
    body: JSON.stringify({ serverIds }),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

/* ------------------------------------------------------------------ */
/*  Admin — MCP Servers                                                */
/* ------------------------------------------------------------------ */

export const adminListMcpServers = (): Promise<MCPServer[]> =>
  req("/api/admin/mcp-servers", { headers: authHeaders() }).then((r) => r.json());

export const adminCreateMcpServer = (data: CreateMcpServerPayload): Promise<MCPServer> =>
  req("/api/admin/mcp-servers", {
    method: "POST",
    body: JSON.stringify(data),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

export const adminPatchMcpServer = (id: string, data: PatchMcpServerPayload): Promise<MCPServer> =>
  req(`/api/admin/mcp-servers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
    headers: { ...JSON_H, ...authHeaders() },
  }).then((r) => r.json());

export const adminDeleteMcpServer = (id: string): Promise<void> =>
  req(`/api/admin/mcp-servers/${id}`, { method: "DELETE", headers: authHeaders() }).then(() => {});

export const adminTestMcpServer = (id: string): Promise<TestMcpResult> =>
  req(`/api/admin/mcp-servers/${id}/test`, { method: "POST", headers: authHeaders() }).then((r) =>
    r.json(),
  );

/* ------------------------------------------------------------------ */
/*  Self — change password                                             */
/* ------------------------------------------------------------------ */

export const changePassword = (data: ChangePasswordPayload): Promise<void> =>
  req("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify(data),
    headers: { ...JSON_H, ...authHeaders() },
  }).then(() => {});

/* ------------------------------------------------------------------ */
/*  Groups — non-admin                                                 */
/* ------------------------------------------------------------------ */

export const getMyGroups = (): Promise<Group[]> =>
  req("/api/groups/mine", { headers: authHeaders() }).then((r) => r.json());

/* ------------------------------------------------------------------ */
/*  Voice — local STT sidecar                                          */
/* ------------------------------------------------------------------ */

export const getVoiceCapabilities = (): Promise<{ stt: boolean }> =>
  req("/api/voice/capabilities", { headers: authHeaders() }).then(r => r.json());

export async function transcribeAudio(
  blob: Blob,
  language = "",
): Promise<{ text: string; durationMs: number }> {
  const form = new FormData();
  form.append("audio", blob, "clip.webm");
  if (language) form.append("language", language);
  const res = await req("/api/voice/transcribe", {
    method: "POST",
    body: form,
    headers: authHeaders(),
  });
  return res.json();
}
