# Thinking Visibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface a reasoning model's `reasoning_content` live in a collapsible "Thoughts" panel (ephemeral), and remove leftover debug prints.

**Architecture:** Reasoning is a new chunk kind parallel to text/tool_call: `client.py` captures `delta.reasoning_content` → `ModelChunk(type="reasoning")`; `loop.py` yields a `reasoning` SSE event immediately (live); the frontend hook accumulates a `streamedReasoning` string that a `ThoughtsPanel` renders. Not persisted — gone on reload, like the existing step timeline.

**Tech Stack:** Python 3.10, FastAPI/openai-SDK, pytest (backend); React 19 + TanStack + Tailwind/shadcn + lucide + framer-motion (frontend); uv; npm.

## Global Constraints

- Reasoning is **ephemeral** — no Message schema change, no persistence, no history-API change.
- **No inline `<think>` parser** — target `reasoning_content` only.
- Zero change to token / tool-call / step behavior or the final answer.
- The SSE event shape is `{"type":"reasoning","content": <str>}` and MUST match byte-for-byte between `app/chat/events.py` and `frontend/src/types/chat.ts`.
- Backend commands from `backend/` with `env -u VIRTUAL_ENV uv run`; frontend commands from `frontend/`.
- Spec: `docs/superpowers/specs/2026-07-24-thinking-visibility-design.md`.

---

## File Structure

- `backend/app/chat/client.py` — remove debug prints; capture `reasoning_content`; extend `ModelChunk.type` (Task 1).
- `backend/app/chat/events.py` — add `reasoning()` builder (Task 1).
- `backend/app/chat/loop.py` — yield `reasoning` in the chunk loop (Task 1).
- `backend/tests/test_chat_client.py` — **new**: client reasoning unit test (Task 1).
- `backend/tests/test_chat_loop.py` — **modify**: loop reasoning test (Task 1).
- `frontend/src/types/chat.ts` — add `reasoning` StreamEvent variant (Task 2).
- `frontend/src/hooks/useStreamChat.ts` — `streamedReasoning` state + `case "reasoning"` (Task 2).
- `frontend/src/components/chat/ThoughtsPanel.tsx` — **new** (Task 2).
- `frontend/src/routes/_auth.chat.$sessionId.tsx` — wire `ThoughtsPanel` (Task 2).

---

### Task 1: Backend — capture reasoning, emit reasoning events, drop debug prints

**Files:**
- Modify: `backend/app/chat/client.py`, `backend/app/chat/events.py`, `backend/app/chat/loop.py`
- Create: `backend/tests/test_chat_client.py`
- Modify: `backend/tests/test_chat_loop.py`

**Interfaces:**
- Produces: `ModelChunk(type="reasoning", text=...)`; `events.reasoning(content: str) -> {"type":"reasoning","content":content}`; a `reasoning` event yielded from `run_turn` per reasoning chunk.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_chat_client.py`:

```python
"""Unit test: OpenAIModelClient surfaces reasoning_content as a reasoning chunk."""
from __future__ import annotations

import asyncio

from app.chat.client import ModelChunk, OpenAIModelClient


class _Delta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls or []


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, delta):
        self.choices = [_Choice(delta)]


async def _fake_response(chunks):
    for c in chunks:
        yield c


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kwargs):
        return _fake_response(self._chunks)


class _FakeChat:
    def __init__(self, chunks):
        self.completions = _FakeCompletions(chunks)


class _FakeAsyncClient:
    def __init__(self, chunks):
        self.chat = _FakeChat(chunks)


def test_stream_yields_reasoning_then_text():
    client = OpenAIModelClient()
    client._client = _FakeAsyncClient([
        _Chunk(_Delta(reasoning_content="thinking hard")),
        _Chunk(_Delta(content="the answer")),
    ])

    async def _run():
        return [c async for c in client.stream(
            messages=[{"role": "user", "content": "q"}], tools=[]
        )]

    chunks = asyncio.run(_run())
    assert chunks == [
        ModelChunk(type="reasoning", text="thinking hard"),
        ModelChunk(type="text", text="the answer"),
    ]
```

Append to `backend/tests/test_chat_loop.py` (mirrors the harness at `test_run_turn_zero_tool_calls_streams_tokens_and_persists`, ~line 140):

```python
def test_run_turn_yields_reasoning_before_final_token(session_factory):
    db, session = _make_session(session_factory)
    model = _FakeModelClient([[
        ModelChunk(type="reasoning", text="Let me think."),
        ModelChunk(type="text", text="Final answer."),
    ]])
    registry = _registry()
    ctx = _ctx(db, session)

    events = asyncio.run(_collect(run_turn(
        db=db, session=session, message="q",
        registry=registry, model=model, ctx=ctx,
    )))

    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    assert reasoning_events == [{"type": "reasoning", "content": "Let me think."}]
    token_events = [e for e in events if e["type"] == "token"]
    assert "".join(e["content"] for e in token_events) == "Final answer."
    # reasoning arrives before the final answer token
    assert events.index(reasoning_events[0]) < events.index(token_events[0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_chat_client.py tests/test_chat_loop.py::test_run_turn_yields_reasoning_before_final_token -q`
Expected: FAIL — client yields no reasoning chunk (drops `reasoning_content`); loop has no `reasoning` event (`events.index` raises / assertion fails).

- [ ] **Step 3: Update `client.py`**

In `backend/app/chat/client.py`: change the `ModelChunk.type` annotation to `Literal["text", "tool_call", "reasoning"]`. Then in `stream`, delete the three debug prints and add the reasoning capture. Replace the block from `print(kwargs)` through the `if delta.content:` yield with:

```python
        response = await self._client.chat.completions.create(**kwargs)
        async for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is None:
                reasoning = (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
            if reasoning:
                yield ModelChunk(type="reasoning", text=reasoning)

            if delta.content:
                yield ModelChunk(type="text", text=delta.content)
```

(The `for tc in delta.tool_calls or []:` block and everything after it are unchanged.)

- [ ] **Step 4: Update `events.py`**

Add after the `token` builder in `backend/app/chat/events.py`:

```python
def reasoning(content: str) -> dict:
    return {"type": "reasoning", "content": content}
```

- [ ] **Step 5: Update `loop.py`**

Import the builder — change `from app.chat.events import ...` to include `reasoning` (add it to the existing import list; `step` and `token` are already imported there). Then in the chunk loop (`loop.py:220-223`), add the reasoning branch:

```python
            async for chunk in model.stream(messages, tools):
                if chunk.type == "text":
                    text_parts.append(chunk.text or "")
                elif chunk.type == "reasoning":
                    yield reasoning(chunk.text or "")
                elif chunk.type == "tool_call":
                    tool_calls.append(chunk)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `env -u VIRTUAL_ENV uv run pytest tests/test_chat_client.py tests/test_chat_loop.py -q`
Expected: PASS (all green, including the existing loop tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/chat/client.py backend/app/chat/events.py backend/app/chat/loop.py backend/tests/test_chat_client.py backend/tests/test_chat_loop.py
git commit -m "feat(chat): surface reasoning_content as a live reasoning event; drop debug prints

OpenAIModelClient now captures delta.reasoning_content (dropped before) and
yields a ModelChunk(type='reasoning'); the loop emits a {type:'reasoning'}
SSE event immediately so a Thoughts panel can fill live. Removes the leftover
debug print() statements. Token/tool/step behavior and the final answer are
unchanged."
```

---

### Task 2: Frontend — Thoughts panel fed by the reasoning stream

**Files:**
- Modify: `frontend/src/types/chat.ts`, `frontend/src/hooks/useStreamChat.ts`, `frontend/src/routes/_auth.chat.$sessionId.tsx`
- Create: `frontend/src/components/chat/ThoughtsPanel.tsx`

**Interfaces:**
- Consumes: SSE `{type:"reasoning", content}` from Task 1.
- Produces: `useStreamChat().streamedReasoning: string`; `<ThoughtsPanel reasoning active />`.

- [ ] **Step 1: Add the `reasoning` StreamEvent variant**

In `frontend/src/types/chat.ts`, add to the `StreamEvent` union (near the `token` variant):

```ts
  | { type: "reasoning"; content: string }
```

- [ ] **Step 2: Accumulate `streamedReasoning` in the hook**

In `frontend/src/hooks/useStreamChat.ts`:
- After the `streamedContent` state declaration, add:
  ```ts
  const [streamedReasoning, setStreamedReasoning] = useState("");
  ```
- In `reset()`, add `setStreamedReasoning("");`.
- In the event `switch`, after `case "token":`, add:
  ```ts
            case "reasoning":
              setStreamedReasoning((prev) => prev + event.content);
              break;
  ```
- Add `streamedReasoning` to the hook's returned object (alongside `steps`, `streamedContent`).

- [ ] **Step 3: Create `ThoughtsPanel.tsx`**

Create `frontend/src/components/chat/ThoughtsPanel.tsx` (match the app's Tailwind/shadcn conventions; reference `StepTracker.tsx` for class idioms):

```tsx
import { useEffect, useState } from "react";
import { Brain, ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export function ThoughtsPanel({
  reasoning,
  active,
}: {
  reasoning: string;
  active: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  // Auto-expand while the model is thinking; auto-collapse when it finishes.
  useEffect(() => {
    setExpanded(active);
  }, [active]);

  if (!reasoning) return null;

  return (
    <div className="w-full rounded-lg border border-border/50 bg-muted/30 text-sm">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-muted-foreground"
      >
        <Brain className="h-4 w-4" />
        <span className="font-medium">Thoughts</span>
        <ChevronDown
          className={cn("ml-auto h-4 w-4 transition-transform", expanded && "rotate-180")}
        />
      </button>
      {expanded && (
        <div className="whitespace-pre-wrap px-3 pb-3 font-mono text-xs text-muted-foreground">
          {reasoning}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire it into the chat route**

In `frontend/src/routes/_auth.chat.$sessionId.tsx`:
- Import: `import { ThoughtsPanel } from "@/components/chat/ThoughtsPanel";`
- Add `streamedReasoning` to the `useStreamChat(...)` destructure (line ~27).
- Derive thinking-active from steps (near where `steps` is used):
  ```ts
  const thinkingActive = steps.some((s) => s.step === "thinking" && s.status === "active");
  ```
- Render the panel next to `<StepTracker steps={steps} active={isStreaming} />` (line ~187), for the in-flight turn only:
  ```tsx
            <StepTracker steps={steps} active={isStreaming} />
            {isStreaming && (
              <ThoughtsPanel reasoning={streamedReasoning} active={thinkingActive} />
            )}
  ```
- Add `streamedReasoning.length` to the render-key memo at line ~41 so the panel updates as reasoning grows:
  ```ts
  `${messages?.length}-${streamedContent.length}-${streamedReasoning.length}-${steps.length}-${ingestTasks.length}`,
  ```

- [ ] **Step 5: Typecheck + build**

Run (from `frontend/`): the project's typecheck + build (check `frontend/package.json` scripts — typically `npm run build`, which runs `tsc` + Vite):
```bash
npm run build
```
Expected: typecheck passes, build succeeds, no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/chat.ts frontend/src/hooks/useStreamChat.ts frontend/src/components/chat/ThoughtsPanel.tsx "frontend/src/routes/_auth.chat.\$sessionId.tsx"
git commit -m "feat(chat-ui): live Thoughts panel from the reasoning stream

useStreamChat accumulates streamedReasoning from {type:'reasoning'} events; a
new collapsible ThoughtsPanel renders it in the in-flight assistant turn,
auto-expanded while the thinking step is active and collapsed when it finishes.
Ephemeral — not persisted, gone on reload."
```

---

### Task 3: Verification

**Files:** none.

- [ ] **Step 1: Full backend suite**

Run: `env -u VIRTUAL_ENV uv run pytest -q` (from `backend/`)
Expected: PASS — prior baseline (343 passed, 1 Docker-skip) plus the new client/loop reasoning tests; **0 failures**; no stray debug output in captured stdout.

- [ ] **Step 2: Frontend build**

Run: `npm run build` (from `frontend/`)
Expected: clean typecheck + build.

- [ ] **Step 3: Controller click-through (manual, note in report)**

Against llama-server Qwen3.6 (a reasoning model): send a chat message, confirm the Thoughts panel fills live during the thinking step, collapses when it completes, and the final answer renders normally. Confirm no `<think>` tags leak into the answer.

- [ ] **Step 4: Confirm branch state**

Run: `git log --oneline main..HEAD`
Expected: the spec commit + the two task commits on `feat/thinking-visibility`.

---

## Self-Review

**Spec coverage:**
- §5.1 client (drop prints, capture reasoning, extend Literal) → Task 1 Steps 3. ✅
- §5.2 events.reasoning → Task 1 Step 4. ✅
- §5.3 loop yields reasoning immediately → Task 1 Step 5. ✅
- §6.1 types StreamEvent variant → Task 2 Step 1. ✅
- §6.2 hook streamedReasoning → Task 2 Step 2. ✅
- §6.3 ThoughtsPanel + route wiring (auto-expand while thinking active) → Task 2 Steps 3-4. ✅
- §7 cleanup (debug prints) → Task 1 Step 3. ✅
- §8 testing (client unit, loop event, regression, FE build, click-through) → Task 1 tests + Task 3. ✅
- §3 ephemeral / no schema → nothing persists reasoning; no model/migration touched. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. "Check package.json scripts" (Task 2 Step 5) names the concrete default `npm run build`.

**Type consistency:** `ModelChunk(type="reasoning", text=...)`, `events.reasoning(content) -> {"type":"reasoning","content"}`, `{type:"reasoning"; content:string}`, `streamedReasoning: string`, `<ThoughtsPanel reasoning active />` used consistently across Tasks 1–2. The wire shape `{"type":"reasoning","content"}` is identical in `events.py` and `types/chat.ts`. ✅
