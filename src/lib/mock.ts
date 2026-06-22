/**
 * ⭐ Central mock layer.
 *
 * All API endpoints, SSE streaming, file-upload progress, and demo data live
 * here. The API layer (`api.ts`) and auth layer (`auth.ts`) call these
 * functions directly — no real network requests are made. Mutable state lives
 * in module-level variables and resets on page refresh.
 */
import type {
  AuthResponse,
  LoginCredentials,
  User,
} from "@/types/api";
import type {
  Attachment,
  ChunkProgressEvent,
  Message,
  Session,
  StreamEvent,
} from "@/types/chat";
import type {
  FileStatus,
  KBFilters,
  KnowledgeBaseFile,
} from "@/types/kb";

// ------------------------------------------------------------------ //
// Helpers                                                             //
// ------------------------------------------------------------------ //

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function generateId(): string {
  // RFC4122-ish v4; good enough for mock identifiers.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

class MockError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "MockError";
  }
}

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
const hoursAgo = (h: number) => minutesAgo(h * 60);
const daysAgo = (d: number) => hoursAgo(d * 24);

// ------------------------------------------------------------------ //
// Demo identity                                                       //
// ------------------------------------------------------------------ //

export const MOCK_USER: User = {
  id: "user-1",
  email: "demo@example.com",
  displayName: "Alex Demo",
};

export const MOCK_TOKEN = "mock-jwt.eyJzdWIiOiJ1c2VyLTEifQ.demo-signature";

// ------------------------------------------------------------------ //
// Seed data                                                           //
// ------------------------------------------------------------------ //

const seedSessions: Session[] = [
  {
    id: "sess-rag",
    title: "How does RAG work?",
    createdAt: hoursAgo(1),
    updatedAt: hoursAgo(1),
  },
  {
    id: "sess-async",
    title: "Python async patterns",
    createdAt: hoursAgo(3),
    updatedAt: hoursAgo(3),
  },
  {
    id: "sess-report",
    title: "Quarterly report analysis",
    createdAt: daysAgo(1),
    updatedAt: daysAgo(1),
  },
  {
    id: "sess-db",
    title: "Database optimization tips",
    createdAt: daysAgo(5),
    updatedAt: daysAgo(5),
  },
  {
    id: "sess-welcome",
    title: "Welcome to RAG Chat",
    createdAt: daysAgo(14),
    updatedAt: daysAgo(14),
  },
];

const msg = (
  sessionId: string,
  role: Message["role"],
  content: string,
  minsAfter: number,
  base: number,
  attachments?: Attachment[],
): Message => ({
  id: generateId(),
  sessionId,
  role,
  content,
  attachments,
  createdAt: new Date(Date.now() - base * 60_000 + minsAfter * 60_000).toISOString(),
});

const seedMessages: Record<string, Message[]> = {
  "sess-rag": [
    msg("sess-rag", "user", "How does RAG work?", 0, 60),
    msg(
      "sess-rag",
      "assistant",
      "**Retrieval-Augmented Generation (RAG)** combines a retriever with a generator:\n\n1. **Indexing** — your documents are split into chunks and embedded into a vector store.\n2. **Retrieval** — at query time, the most relevant chunks are fetched via similarity search.\n3. **Generation** — those chunks are injected into the prompt so the LLM answers from grounded context.\n\nThe key benefit is that the model can cite *up-to-date, private* knowledge without retraining.",
      1,
      60,
    ),
    msg("sess-rag", "user", "What embedding model should I use?", 2, 60),
    msg(
      "sess-rag",
      "assistant",
      "For most use cases, start with a general-purpose model:\n\n- `text-embedding-3-large` — strong quality, 3072 dims\n- `bge-large-en` — great open-source option\n\n> Tip: keep the *same* embedding model for indexing and querying — mismatched vectors will silently degrade retrieval.",
      3,
      60,
    ),
  ],
  "sess-async": [
    msg("sess-async", "user", "Show me a Python async pattern for concurrent requests.", 0, 180),
    msg(
      "sess-async",
      "assistant",
      "Use `asyncio.gather` to run coroutines concurrently:\n\n```python\nimport asyncio\nimport httpx\n\nasync def fetch(client, url):\n    r = await client.get(url)\n    return r.status_code\n\nasync def main(urls):\n    async with httpx.AsyncClient() as client:\n        tasks = [fetch(client, u) for u in urls]\n        return await asyncio.gather(*tasks)\n\nasyncio.run(main([\"https://example.com\"] * 5))\n```\n\nThis fires all requests without blocking.",
      1,
      180,
    ),
    msg("sess-async", "user", "How do I limit concurrency?", 2, 180),
    msg(
      "sess-async",
      "assistant",
      "Wrap calls in an `asyncio.Semaphore`:\n\n```python\nsem = asyncio.Semaphore(3)\n\nasync def fetch(client, url):\n    async with sem:\n        r = await client.get(url)\n        return r.status_code\n```\n\nThe semaphore caps in-flight requests at 3.",
      3,
      180,
    ),
    msg("sess-async", "user", "What about timeouts?", 4, 180),
    msg(
      "sess-async",
      "assistant",
      "Use `asyncio.wait_for` or the client's `timeout` argument:\n\n```python\nasync with httpx.AsyncClient(timeout=10.0) as client:\n    ...\n```",
      5,
      180,
    ),
  ],
  "sess-report": [
    msg(
      "sess-report",
      "user",
      "Can you summarize the attached quarterly report?",
      0,
      1440,
      [
        {
          id: generateId(),
          fileName: "quarterly-report-q1.pdf",
          fileType: "application/pdf",
          fileSize: 2_340_000,
          url: "#",
        },
      ],
    ),
    msg(
      "sess-report",
      "assistant",
      "Here are the highlights from **Q1**:\n\n| Metric | Value | QoQ |\n|---|---|---|\n| Revenue | $4.2M | +12% |\n| Gross margin | 68% | +3pts |\n| Churn | 1.8% | -0.4pts |\n\nNet: healthy top-line growth with improving retention. The main risk flagged is rising CAC in the enterprise segment.",
      1,
      1440,
    ),
  ],
  "sess-db": [
    msg("sess-db", "user", "My Postgres queries are slow. Where do I start?", 0, 7200),
    msg(
      "sess-db",
      "assistant",
      "Start with `EXPLAIN (ANALYZE, BUFFERS)` on the slow query. Look for:\n\n- **Seq Scan** on large tables → add an index\n- **High rows removed by filter** → your index isn't selective\n- **Nested loop** with big row counts → may need a hash join\n\nThen confirm indexes exist on your `WHERE` and `JOIN` columns.",
      1,
      7200,
    ),
    msg("sess-db", "user", "How big should my shared_buffers be?", 2, 7200),
    msg(
      "sess-db",
      "assistant",
      "A common starting point is **25% of system RAM**, capped around 8–16GB for most workloads. Pair it with `effective_cache_size` set to ~75% of RAM so the planner knows how much OS cache is available.",
      3,
      7200,
    ),
  ],
  "sess-welcome": [
    msg("sess-welcome", "user", "Hi! What can this app do?", 0, 20160),
    msg(
      "sess-welcome",
      "assistant",
      "Welcome to **RAG Chat**! 👋\n\nYou can:\n\n- Chat with an assistant grounded in your **knowledge base**\n- Upload documents and watch them get indexed\n- See the retrieval **pipeline** as it runs (thinking → retrieving → tools → generating)\n- Use **voice** input and output\n\nTry asking a question or head to the Knowledge Base to add files.",
      1,
      20160,
    ),
  ],
};

const seedKBFiles: KnowledgeBaseFile[] = [
  {
    id: "kb-1",
    name: "company-handbook.pdf",
    size: 3_200_000,
    uploadDate: daysAgo(30),
    chunkCount: 142,
    status: "ready",
    tags: ["hr", "policy"],
  },
  {
    id: "kb-2",
    name: "api-documentation.md",
    size: 480_000,
    uploadDate: daysAgo(12),
    chunkCount: 89,
    status: "ready",
    tags: ["engineering", "api"],
  },
  {
    id: "kb-3",
    name: "quarterly-report-q1.pdf",
    size: 2_340_000,
    uploadDate: daysAgo(8),
    chunkCount: 67,
    status: "ready",
    tags: ["finance", "reports"],
  },
  {
    id: "kb-4",
    name: "product-roadmap.docx",
    size: 690_000,
    uploadDate: minutesAgo(4),
    chunkCount: 0,
    status: "indexing",
    tags: ["product", "planning"],
  },
  {
    id: "kb-5",
    name: "research-paper.pdf",
    size: 5_100_000,
    uploadDate: daysAgo(3),
    chunkCount: 203,
    status: "ready",
    tags: ["research", "ml"],
  },
  {
    id: "kb-6",
    name: "meeting-notes.txt",
    size: 24_000,
    uploadDate: daysAgo(1),
    chunkCount: 0,
    status: "error",
    tags: ["meetings"],
  },
];

// ------------------------------------------------------------------ //
// Mutable in-memory state (resets on refresh)                         //
// ------------------------------------------------------------------ //

const mockSessions = new Map<string, Session>(
  seedSessions.map((s) => [s.id, { ...s }]),
);
const mockMessages = new Map<string, Message[]>(
  Object.entries(seedMessages).map(([k, v]) => [k, v.map((m) => ({ ...m }))]),
);
let mockKBFiles: KnowledgeBaseFile[] = seedKBFiles.map((f) => ({
  ...f,
  tags: [...f.tags],
}));

// ------------------------------------------------------------------ //
// Auth                                                                //
// ------------------------------------------------------------------ //

export async function mockLogin(
  _credentials: LoginCredentials,
): Promise<AuthResponse> {
  await delay(500);
  return { accessToken: MOCK_TOKEN, user: { ...MOCK_USER } };
}

export async function mockGetMe(token: string | null): Promise<User> {
  await delay(300);
  if (token !== MOCK_TOKEN) throw new MockError(401, "Unauthorized");
  return { ...MOCK_USER };
}

export async function mockLogout(): Promise<void> {
  await delay(200);
}

// ------------------------------------------------------------------ //
// Sessions                                                            //
// ------------------------------------------------------------------ //

export async function mockGetSessions(): Promise<Session[]> {
  await delay(400);
  return [...mockSessions.values()].sort(
    (a, b) =>
      new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
  );
}

export async function mockGetSession(id: string): Promise<Session> {
  await delay(200);
  const session = mockSessions.get(id);
  if (!session) throw new MockError(404, "Session not found");
  return { ...session };
}

export async function mockCreateSession(): Promise<Session> {
  await delay(300);
  const now = new Date().toISOString();
  const session: Session = {
    id: generateId(),
    title: "New Chat",
    createdAt: now,
    updatedAt: now,
  };
  mockSessions.set(session.id, session);
  mockMessages.set(session.id, []);
  return { ...session };
}

export async function mockRenameSession(
  id: string,
  title: string,
): Promise<Session> {
  await delay(200);
  const session = mockSessions.get(id);
  if (!session) throw new MockError(404, "Session not found");
  session.title = title;
  session.updatedAt = new Date().toISOString();
  return { ...session };
}

export async function mockDeleteSession(id: string): Promise<void> {
  await delay(200);
  mockSessions.delete(id);
  mockMessages.delete(id);
}

export async function mockGetMessages(sessionId: string): Promise<Message[]> {
  await delay(300);
  return (mockMessages.get(sessionId) ?? []).map((m) => ({ ...m }));
}

// ------------------------------------------------------------------ //
// Chat streaming                                                      //
// ------------------------------------------------------------------ //

const RESPONSE_TEMPLATES: { match: RegExp; body: string }[] = [
  {
    match: /\b(rag|retriev|embedding|vector)\b/i,
    body: "Based on the retrieved context, **RAG** grounds the model's answer in your indexed documents. The retriever finds the top-k most similar chunks and the generator weaves them into a coherent response.\n\nKey levers:\n\n- **Chunk size** affects recall vs. precision\n- **Re-ranking** improves the final ordering\n- **Citations** let users verify the source",
  },
  {
    match: /\b(python|async|code|function|bug|error)\b/i,
    body: "Here's a concise approach:\n\n```python\ndef solve(items):\n    return [transform(x) for x in items if is_valid(x)]\n```\n\nThis keeps the logic declarative and easy to test. Let me know if you'd like an async variant or error handling added.",
  },
  {
    match: /\b(report|finance|revenue|metric|analy)\b/i,
    body: "Looking at the document, the headline figures are trending positively. Revenue is up quarter-over-quarter and margins are stable.\n\n| Area | Trend |\n|---|---|\n| Growth | ↑ |\n| Retention | ↑ |\n| Costs | → |\n\nWould you like a deeper breakdown of any section?",
  },
];

const DEFAULT_RESPONSE =
  "Good question. After reviewing the relevant context from your knowledge base, here's what I found:\n\nThe most important point is that the answer depends on your specific setup, but the general best practice is to start simple and measure before optimizing.\n\n- Establish a baseline\n- Change one variable at a time\n- Verify against real data\n\nLet me know if you'd like me to expand on any of these.";

export function generateMockResponse(userMessage: string): string {
  const hit = RESPONSE_TEMPLATES.find((t) => t.match.test(userMessage));
  return hit ? hit.body : DEFAULT_RESPONSE;
}

export async function* mockStreamChat(
  sessionId: string,
  message: string,
  attachments?: Attachment[],
): AsyncGenerator<StreamEvent> {
  const session = mockSessions.get(sessionId);
  if (!session) throw new MockError(404, "Session not found");

  // Persist the user message immediately.
  const list = mockMessages.get(sessionId) ?? [];
  const userMsg: Message = {
    id: generateId(),
    sessionId,
    role: "user",
    content: message,
    attachments,
    createdAt: new Date().toISOString(),
  };
  list.push(userMsg);
  mockMessages.set(sessionId, list);

  // Auto-title a fresh "New Chat" from the first user message.
  if (session.title === "New Chat") {
    session.title =
      message.length > 40 ? `${message.slice(0, 40)}…` : message || "New Chat";
  }
  session.updatedAt = new Date().toISOString();

  // Pipeline: thinking -> retrieving -> calling_tool -> generating
  yield { type: "step", step: "thinking", status: "active" };
  await delay(800);
  yield { type: "step", step: "thinking", status: "complete" };

  yield { type: "step", step: "retrieving_context", status: "active" };
  await delay(1200);
  yield { type: "step", step: "retrieving_context", status: "complete" };

  yield {
    type: "step",
    step: "calling_tool",
    status: "active",
    toolName: "search_knowledge_base",
    toolArgs: { query: message.slice(0, 60) },
  };
  await delay(1000);
  yield { type: "step", step: "calling_tool", status: "complete" };

  yield { type: "step", step: "generating_response", status: "active" };
  await delay(300);

  const responseText = generateMockResponse(message);
  const tokens = responseText.match(/\S+\s*/g) ?? [responseText];
  for (const token of tokens) {
    await delay(30 + Math.random() * 20);
    yield { type: "token", content: token };
  }
  yield { type: "step", step: "generating_response", status: "complete" };

  // Persist the assistant message.
  const assistantMsg: Message = {
    id: generateId(),
    sessionId,
    role: "assistant",
    content: responseText,
    createdAt: new Date().toISOString(),
  };
  const updated = mockMessages.get(sessionId) ?? [];
  updated.push(assistantMsg);
  mockMessages.set(sessionId, updated);
  session.updatedAt = new Date().toISOString();

  yield { type: "done", messageId: assistantMsg.id };
}

// ------------------------------------------------------------------ //
// Knowledge base                                                      //
// ------------------------------------------------------------------ //

export async function mockGetKBFiles(
  filters?: KBFilters,
): Promise<KnowledgeBaseFile[]> {
  await delay(400);
  let files = mockKBFiles.map((f) => ({ ...f, tags: [...f.tags] }));
  if (filters?.search) {
    const q = filters.search.toLowerCase();
    files = files.filter((f) => f.name.toLowerCase().includes(q));
  }
  if (filters?.status) {
    files = files.filter((f) => f.status === filters.status);
  }
  if (filters?.tag) {
    files = files.filter((f) => f.tags.includes(filters.tag!));
  }
  return files.sort(
    (a, b) =>
      new Date(b.uploadDate).getTime() - new Date(a.uploadDate).getTime(),
  );
}

export async function mockUploadKBFile(
  file: File,
  onProgress?: (progress: number, status: FileStatus | "uploading") => void,
): Promise<KnowledgeBaseFile> {
  // Upload progress 0 -> 100 over ~2s.
  for (let i = 1; i <= 10; i++) {
    await delay(200);
    onProgress?.(i * 10, "uploading");
  }
  // Processing then indexing.
  onProgress?.(100, "indexing");
  await delay(1200);

  const kbFile: KnowledgeBaseFile = {
    id: generateId(),
    name: file.name,
    size: file.size,
    uploadDate: new Date().toISOString(),
    chunkCount: 20 + Math.floor(Math.random() * 200),
    status: "ready",
    tags: [],
  };
  mockKBFiles = [kbFile, ...mockKBFiles];
  return { ...kbFile, tags: [] };
}

export async function mockDeleteKBFile(id: string): Promise<void> {
  await delay(200);
  mockKBFiles = mockKBFiles.filter((f) => f.id !== id);
}

export async function mockReindexKBFile(
  id: string,
): Promise<KnowledgeBaseFile> {
  await delay(200);
  const file = mockKBFiles.find((f) => f.id === id);
  if (!file) throw new MockError(404, "File not found");
  file.status = "indexing";
  file.chunkCount = 0;
  // Simulate async indexing completing later.
  setTimeout(() => {
    file.status = "ready";
    file.chunkCount = 20 + Math.floor(Math.random() * 200);
  }, 2000);
  return { ...file, tags: [...file.tags] };
}

export async function mockUpdateFileTags(
  id: string,
  tags: string[],
): Promise<KnowledgeBaseFile> {
  await delay(200);
  const file = mockKBFiles.find((f) => f.id === id);
  if (!file) throw new MockError(404, "File not found");
  file.tags = [...tags];
  return { ...file, tags: [...file.tags] };
}

export async function* mockSimulateChunkProgress(
  fileName: string,
): AsyncGenerator<ChunkProgressEvent> {
  const total = 40 + Math.floor(Math.random() * 60);
  const steps = 10;
  for (let i = 1; i <= steps; i++) {
    await delay(300);
    yield {
      type: "chunk_progress",
      fileName,
      progress: (i / steps) * 100,
      chunkCount: Math.round((i / steps) * total),
      total,
    };
  }
}
