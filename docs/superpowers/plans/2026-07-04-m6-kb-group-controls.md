# M6: KB Page Group Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the KB page to M3's group-gated upload — admin files into any group + public + tags; regular users auto-file or pick their group; no-group users blocked; file rows show group/Public badges.

**Architecture:** One tiny backend endpoint (`GET /api/groups/mine`) for non-admin group discovery; frontend types, API, hook, and page updated; FileCard gains group + Public badges. The M3 upload enforcement already lives in the backend (`_resolve_kb_filing`); this stage only surfaces it in the UI.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + TanStack Query + shadcn/tailwind (frontend), TypeScript strict.

## Global Constraints

- Only ONE new backend endpoint: `GET /api/groups/mine` — no §8/KB-logic changes
- No docs edits (beyond this plan file)
- Match existing shadcn/tailwind + FastAPI style
- TypeScript strict, YAGNI
- Don't break existing KB list/upload for common case
- Verify: `cd frontend && export PATH="/opt/homebrew/opt/node/bin:$PATH" && npm run typecheck && npm run build`
- Backend: `env -u VIRTUAL_ENV uv run pytest tests/test_groups.py -q` + `ruff check app tests`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/groups/routes.py` | **CREATE** | `GET /api/groups/mine` endpoint |
| `backend/app/main.py` | **MODIFY** | Mount new groups router |
| `backend/tests/test_groups.py` | **MODIFY** | Add tests for `/api/groups/mine` |
| `frontend/src/types/kb.ts` | **MODIFY** | Add `groupId`, `isPublic` to `KnowledgeBaseFile` |
| `frontend/src/lib/api.ts` | **MODIFY** | Extend upload fn + add `getMyGroups()` |
| `frontend/src/hooks/useFileUpload.ts` | **MODIFY** | Pass filing opts through |
| `frontend/src/routes/_auth.knowledge-base.tsx` | **MODIFY** | Role-based upload UI |
| `frontend/src/components/kb/FileCard.tsx` | **MODIFY** | Group badge + Public badge |

---

### Task 1: Backend — `GET /api/groups/mine` + tests

**Files:**
- Create: `backend/app/groups/routes.py`
- Modify: `backend/app/main.py` (lines ~145-155, router imports + include_router)
- Modify: `backend/tests/test_groups.py` (append new test section)

**Interfaces:**
- Produces: `GET /api/groups/mine` → `list[GroupOut]` (camelCase via alias), requires Bearer token (any auth'd user)

- [ ] **Step 1: Write failing tests (append to `tests/test_groups.py`)**

```python
# ---------------------------------------------------------------------------
# GET /api/groups/mine — caller's own groups (M6)
# ---------------------------------------------------------------------------


def test_mine_unauthenticated(client):
    """No token → 401."""
    r = client.get("/api/groups/mine")
    assert r.status_code == 401


def test_mine_no_groups(client, auth_headers):
    """Regular user with no group membership → empty list."""
    r = client.get("/api/groups/mine", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_mine_returns_own_groups_only(client, admin_headers, auth_headers, demo_user, session_factory):
    """User A sees only their groups; user B's groups never appear."""
    # Create two groups via admin
    g1 = client.post("/api/admin/groups", json={"name": "m6-g1"}, headers=admin_headers).json()
    g2 = client.post("/api/admin/groups", json={"name": "m6-g2"}, headers=admin_headers).json()

    # Add demo_user to g1 only
    client.put(
        f"/api/admin/groups/{g1['id']}/members",
        json={"userIds": [demo_user["id"]]},
        headers=admin_headers,
    )

    r = client.get("/api/groups/mine", headers=auth_headers)
    assert r.status_code == 200
    ids = [g["id"] for g in r.json()]
    assert g1["id"] in ids
    assert g2["id"] not in ids


def test_mine_multiple_groups(client, admin_headers, auth_headers, demo_user):
    """User in multiple groups sees all of them."""
    g1 = client.post("/api/admin/groups", json={"name": "m6-multi-1"}, headers=admin_headers).json()
    g2 = client.post("/api/admin/groups", json={"name": "m6-multi-2"}, headers=admin_headers).json()
    client.put(
        f"/api/admin/groups/{g1['id']}/members",
        json={"userIds": [demo_user["id"]]},
        headers=admin_headers,
    )
    client.put(
        f"/api/admin/groups/{g2['id']}/members",
        json={"userIds": [demo_user["id"]]},
        headers=admin_headers,
    )
    r = client.get("/api/groups/mine", headers=auth_headers)
    assert r.status_code == 200
    ids = {g["id"] for g in r.json()}
    assert {g1["id"], g2["id"]} == ids


def test_mine_admin_also_works(client, admin_headers, admin_user, session_factory):
    """Admin with no groups gets empty list (endpoint is not admin-guarded)."""
    r = client.get("/api/groups/mine", headers=admin_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_mine_response_shape(client, admin_headers, auth_headers, demo_user):
    """Response items match GroupOut shape (camelCase)."""
    g = client.post("/api/admin/groups", json={"name": "m6-shape", "defaultTags": ["x"]}, headers=admin_headers).json()
    client.put(
        f"/api/admin/groups/{g['id']}/members",
        json={"userIds": [demo_user["id"]]},
        headers=admin_headers,
    )
    r = client.get("/api/groups/mine", headers=auth_headers)
    assert r.status_code == 200
    item = r.json()[0]
    assert "id" in item
    assert "name" in item
    assert "defaultTags" in item
    assert "memberCount" in item
    assert "createdAt" in item
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend
env -u VIRTUAL_ENV uv run pytest tests/test_groups.py::test_mine_unauthenticated -q 2>&1 | tail -5
```

Expected: FAIL / ERROR (no route exists yet)

- [ ] **Step 3: Create `backend/app/groups/routes.py`**

```python
"""Non-admin group endpoints — available to any authenticated user."""
from __future__ import annotations

from fastapi import APIRouter

from app.auth.deps import CurrentUser, DbSession
from app.models import Group
from app.schemas import GroupOut

router = APIRouter()


@router.get("/mine", response_model=list[GroupOut])
def my_groups(user: CurrentUser, db: DbSession) -> list[GroupOut]:
    """Return the groups the caller belongs to (0, 1, or many)."""
    return [GroupOut.model_validate(g) for g in user.groups]
```

- [ ] **Step 4: Mount the router in `backend/app/main.py`**

Add after the existing admin router imports (around line 145):

```python
from app.groups.routes import router as groups_router  # noqa: E402
```

Add after the `app.include_router(admin_mcp_router, ...)` line:

```python
app.include_router(groups_router, prefix="/api/groups", tags=["groups"])
```

- [ ] **Step 5: Run tests**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend
env -u VIRTUAL_ENV uv run pytest tests/test_groups.py -q
```

Expected: all pass

- [ ] **Step 6: ruff check**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend
env -u VIRTUAL_ENV uv run ruff check app tests
```

Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add backend/app/groups/routes.py backend/app/main.py backend/tests/test_groups.py
git commit -m "feat(backend): GET /api/groups/mine — caller's own groups (M6.1)"
```

---

### Task 2: Frontend types + API

**Files:**
- Modify: `frontend/src/types/kb.ts` (add fields to `KnowledgeBaseFile`)
- Modify: `frontend/src/lib/api.ts` (extend upload fn + add getMyGroups)

**Interfaces:**
- Produces:
  - `KnowledgeBaseFile.groupId: string | null`
  - `KnowledgeBaseFile.isPublic: boolean`
  - `uploadKnowledgeBaseFile(file, opts?: { groupId?: string | null; isPublic?: boolean; tags?: string[] }): AsyncGenerator<UploadStreamEvent>`
  - `getMyGroups(): Promise<Group[]>`

- [ ] **Step 1: Update `frontend/src/types/kb.ts`**

Replace the `KnowledgeBaseFile` interface:

```typescript
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
```

- [ ] **Step 2: Update `frontend/src/lib/api.ts` — extend uploadKnowledgeBaseFile**

Replace the existing `uploadKnowledgeBaseFile` function signature + body:

```typescript
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
```

- [ ] **Step 3: Add `getMyGroups` to `frontend/src/lib/api.ts`**

Add after `adminDeleteMcpServer` (or at end of KB section):

```typescript
/* ------------------------------------------------------------------ */
/*  Groups — non-admin                                                 */
/* ------------------------------------------------------------------ */

export const getMyGroups = (): Promise<Group[]> =>
  req("/api/groups/mine", { headers: authHeaders() }).then((r) => r.json());
```

Note: `Group` type is already imported from `@/types/admin`.

- [ ] **Step 4: typecheck**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/frontend
export PATH="/opt/homebrew/opt/node/bin:$PATH"
npm run typecheck 2>&1 | tail -20
```

Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/kb.ts frontend/src/lib/api.ts
git commit -m "feat(frontend): KnowledgeBaseFile.groupId+isPublic types; uploadKnowledgeBaseFile opts; getMyGroups (M6.2)"
```

---

### Task 3: `useFileUpload` — filing opts

**Files:**
- Modify: `frontend/src/hooks/useFileUpload.ts`

**Interfaces:**
- Consumes: `uploadKnowledgeBaseFile(file, opts?: KBUploadOpts)` from Task 2
- Produces: `addFiles(incoming, opts?: KBUploadOpts)` — opts forwarded to each upload

- [ ] **Step 1: Update `frontend/src/hooks/useFileUpload.ts`**

Full replacement:

```typescript
import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { uploadKnowledgeBaseFile, type KBUploadOpts } from "@/lib/api";
import { generateId, isValidFileType } from "@/lib/utils";
import type { UploadTask, UploadTaskStatus } from "@/types/kb";

const MAX_CONCURRENT = 3;

export function useFileUpload() {
  const qc = useQueryClient();
  const [tasks, setTasks] = useState<UploadTask[]>([]);
  const activeCount = useRef(0);
  const queue = useRef<string[]>([]);
  // Files by task id, kept in a ref so `pump` can start an upload without
  // reading React state inside a setState updater (see `pump` below).
  const files = useRef<Map<string, File>>(new Map());
  // Filing opts per task id — set at addFiles time, read at runTask time.
  const filingOpts = useRef<Map<string, KBUploadOpts>>(new Map());

  const update = useCallback(
    (id: string, patch: Partial<UploadTask>) => {
      setTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, ...patch } : t)),
      );
    },
    [],
  );

  const runTask = useCallback(
    async (id: string, file: File) => {
      activeCount.current++;
      update(id, { status: "uploading", progress: 0 });
      const opts = filingOpts.current.get(id);
      try {
        for await (const ev of uploadKnowledgeBaseFile(file, opts)) {
          if (ev.type === "chunk_progress") {
            update(id, { progress: ev.progress, status: "indexing" });
          } else if (ev.type === "file_resolved") {
            // File is indexed and ready.
          }
        }
        update(id, { status: "done", progress: 100 });
        qc.invalidateQueries({ queryKey: ["kb-files"] });
      } catch (err) {
        update(id, {
          status: "error",
          error: err instanceof Error ? err.message : "Upload failed",
        });
      } finally {
        activeCount.current--;
        files.current.delete(id);
        filingOpts.current.delete(id);
        pump();
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [qc, update],
  );

  // Start queued tasks up to the concurrency cap. This MUST NOT mutate state:
  // a side effect inside a setState updater gets invoked twice by React Strict
  // Mode, which previously fired every upload twice. The queue + `files` ref
  // give us everything we need without touching `tasks`.
  const pump = useCallback(() => {
    while (activeCount.current < MAX_CONCURRENT && queue.current.length > 0) {
      const id = queue.current.shift()!;
      const file = files.current.get(id);
      if (file) void runTask(id, file);
    }
  }, [runTask]);

  const addFiles = useCallback(
    (incoming: FileList | File[], opts?: KBUploadOpts) => {
      const newTasks: UploadTask[] = Array.from(incoming).map((file) => {
        const valid = isValidFileType(file.name);
        const status: UploadTaskStatus = valid ? "queued" : "error";
        const id = generateId();
        if (valid) {
          files.current.set(id, file);
          if (opts) filingOpts.current.set(id, opts);
        }
        return {
          id,
          file,
          progress: 0,
          status,
          error: valid ? undefined : "Unsupported file type",
        };
      });
      setTasks((prev) => [...prev, ...newTasks]);
      for (const t of newTasks) {
        if (t.status === "queued") queue.current.push(t.id);
      }
      pump();
    },
    [pump],
  );

  const cancelTask = useCallback((id: string) => {
    queue.current = queue.current.filter((q) => q !== id);
    files.current.delete(id);
    filingOpts.current.delete(id);
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const removeTask = cancelTask;

  const retryTask = useCallback(
    (id: string) => {
      const task = tasks.find((t) => t.id === id);
      if (!task) return;
      files.current.set(id, task.file);
      setTasks((prev) =>
        prev.map((t) =>
          t.id === id
            ? { ...t, status: "queued", progress: 0, error: undefined }
            : t,
        ),
      );
      queue.current.push(id);
      pump();
    },
    [pump, tasks],
  );

  return { addFiles, tasks, cancelTask, retryTask, removeTask };
}
```

- [ ] **Step 2: typecheck**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/frontend
export PATH="/opt/homebrew/opt/node/bin:$PATH"
npm run typecheck 2>&1 | tail -20
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useFileUpload.ts
git commit -m "feat(frontend): useFileUpload passes KBUploadOpts through to uploadKnowledgeBaseFile (M6.3)"
```

---

### Task 4: KB page role-based UI + FileCard badges

**Files:**
- Modify: `frontend/src/routes/_auth.knowledge-base.tsx`
- Modify: `frontend/src/components/kb/FileCard.tsx`

**Interfaces:**
- Consumes:
  - `useAuthStore().user?.isAdmin: boolean`
  - `getMyGroups(): Promise<Group[]>` from Task 2
  - `adminListGroups(): Promise<Group[]>` (already in api.ts)
  - `addFiles(files, opts?: KBUploadOpts)` from Task 3
  - `KnowledgeBaseFile.groupId: string | null` + `.isPublic: boolean` from Task 2

- [ ] **Step 1: Update `FileCard.tsx` to show group + Public badges**

After the status badge line (in the `<div className="flex items-center justify-between gap-2">` block), add group and Public badges. Replace the whole `FileCard.tsx`:

```typescript
import { useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileText,
  Globe,
  Loader2,
  MoreVertical,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { TagEditor } from "@/components/kb/TagEditor";
import {
  useDeleteKBFile,
  useReindexKBFile,
  useUpdateFileTags,
} from "@/lib/queries";
import { formatFileSize, formatRelativeTime } from "@/lib/utils";
import type { FileStatus, KnowledgeBaseFile } from "@/types/kb";

const STATUS_META: Record<
  FileStatus,
  {
    label: string;
    variant: "success" | "warning" | "destructive";
    icon: typeof CheckCircle2;
    spin?: boolean;
  }
> = {
  ready: { label: "Ready", variant: "success", icon: CheckCircle2 },
  indexing: { label: "Indexing", variant: "warning", icon: Loader2, spin: true },
  error: { label: "Error", variant: "destructive", icon: AlertCircle },
};

export function FileCard({
  file,
  groupName,
}: {
  file: KnowledgeBaseFile;
  groupName?: string;
}) {
  const deleteFile = useDeleteKBFile();
  const reindexFile = useReindexKBFile();
  const updateTags = useUpdateFileTags();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const status = STATUS_META[file.status];
  const StatusIcon = status.icon;

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-muted">
          <FileText className="size-5 text-brand-blue" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium" title={file.name}>
            {file.name}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatFileSize(file.size)} · {formatRelativeTime(file.uploadDate)}
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-8 shrink-0">
              <MoreVertical className="size-4" />
              <span className="sr-only">File actions</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() => reindexFile.mutate(file.id)}
              disabled={file.status === "indexing"}
            >
              <RefreshCw />
              Re-index
            </DropdownMenuItem>
            <DropdownMenuItem
              variant="destructive"
              onSelect={() => setConfirmOpen(true)}
            >
              <Trash2 />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={status.variant} className="gap-1">
          <StatusIcon className={status.spin ? "size-3 animate-spin" : "size-3"} />
          {status.label}
        </Badge>
        {groupName != null && (
          <Badge variant="outline" className="text-xs font-normal">
            {groupName}
          </Badge>
        )}
        {file.isPublic && (
          <Badge variant="secondary" className="gap-1 text-xs font-normal">
            <Globe className="size-3" />
            Public
          </Badge>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {file.status === "ready"
            ? `${file.chunkCount} chunks`
            : file.status === "indexing"
              ? "Processing…"
              : "Failed to index"}
        </span>
      </div>

      <TagEditor
        tags={file.tags}
        onChange={(tags) => updateTags.mutate({ id: file.id, tags })}
      />

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete file?</AlertDialogTitle>
            <AlertDialogDescription>
              "{file.name}" and its {file.chunkCount} indexed chunks will be
              permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteFile.mutate(file.id)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
```

- [ ] **Step 2: Update KB page route**

Full replacement of `frontend/src/routes/_auth.knowledge-base.tsx` — see implementation notes: read `isAdmin` from authStore, `getMyGroups` for regular users, `adminListGroups` for admin; role-based upload panel above the dropzone; pass `groupId`/`isPublic`/`tags` opts to `addFiles`; pass resolved `groupName` prop to `FileCard`.

- [ ] **Step 3: typecheck + build**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/frontend
export PATH="/opt/homebrew/opt/node/bin:$PATH"
npm run typecheck && npm run build 2>&1 | tail -30
```

Expected: no errors, build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/_auth.knowledge-base.tsx frontend/src/components/kb/FileCard.tsx
git commit -m "feat(frontend): KB page role-based upload UI + group/Public badges on FileCard (M6.4)"
```

---

### Task 5: Final verification + report

- [ ] **Step 1: Backend tests**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend
env -u VIRTUAL_ENV uv run pytest tests/test_groups.py -q
```

- [ ] **Step 2: ruff**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/backend
env -u VIRTUAL_ENV uv run ruff check app tests
```

- [ ] **Step 3: Frontend typecheck + build**

```bash
cd /Users/ark/arkan/playground/ratest-cld-2/frontend
export PATH="/opt/homebrew/opt/node/bin:$PATH"
npm run typecheck && npm run build
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "v1.1-M6: KB page group controls (role-based upload + group/public badges) + GET /api/groups/mine"
```

- [ ] **Step 5: Write report to `.superpowers/sdd/m6-report.md`**
