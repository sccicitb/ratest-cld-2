# Thinking visibility (reasoning_content → Thoughts panel) — design

- **Date:** 2026-07-24
- **Status:** Draft (awaiting review)
- **Scope:** Surface a reasoning model's chain-of-thought live, ephemerally. Plus remove leftover debug prints.
- **Related:** the model-client abstraction (`app/chat/client.py`), the chat SSE event contract (`app/chat/events.py` ↔ `frontend/src/types/chat.ts`).

## 1. Problem

`OpenAIModelClient.stream` reads only `delta.content` (`client.py:77`) and **silently drops `delta.reasoning_content`** — the separate channel that reasoning models stream their chain-of-thought on. Empirically, **both current providers use this field**: DeepSeek (`deepseek-reasoner` / v4 thinking) and Qwen3.6-MTP via the llama-server (confirmed in the PDFOxide smoke test — Qwen3.6 returned a populated `reasoning_content` alongside `content`). Neither uses inline `<think>` tags in the current setup. So a reasoning model's thinking is invisible today.

Separately, `client.py:68-71` contains three leftover debug `print()` statements polluting server stdout.

## 2. Decision

Capture `reasoning_content` and surface it as a **new ephemeral stream, parallel to tokens** — rendered live in a collapsible "Thoughts" panel that fills while the model thinks and is gone on reload (matching how the existing step timeline already behaves). No persistence, no schema change. And delete the debug prints.

**Decided forks (from brainstorming):** full thinking visibility (not cleanup-only); ephemeral/live-only (not persisted); target `reasoning_content` (no inline `<think>` parser — no current model needs it).

## 3. Goals / Non-goals

**Goals**
- Reasoning streams live into a collapsible "Thoughts" panel per assistant turn, for any provider that emits `reasoning_content`.
- Remove the debug `print()`s.
- Zero change to token / tool-call / step behavior or the final answer.

**Non-goals (explicitly out)**
- **Persistence / schema change** — reasoning is not stored on the Message; it vanishes on reload (consistent with the live-only step timeline).
- **Inline `<think>` parsing** — no current model emits it; if one ever does, it renders raw in content (acceptable). A streaming tag parser is out of scope.
- No change to the token stream, tool loop, pipeline steps, or history API.

## 4. Architecture

Reasoning is a **third chunk kind** flowing client → loop → SSE → hook → a panel, orthogonal to the existing text/tool_call chunks and the step timeline.

```
delta.reasoning_content
  → ModelChunk(type="reasoning", text=…)          [client.py]
  → events.reasoning(text) → {"type":"reasoning","content":…}   [loop.py + events.py]
  → SSE
  → useStreamChat: case "reasoning" → streamedReasoning += content   [hook]
  → <ThoughtsPanel reasoning={streamedReasoning} active={thinking step} />   [render]
```

## 5. Backend changes

### 5.1 `app/chat/client.py`
- Delete the three debug prints (`client.py:68-71`: `print(kwargs)`, the separator, `print(response)`).
- Extend `ModelChunk.type` Literal to `Literal["text", "tool_call", "reasoning"]`.
- In the stream loop, before/after the `delta.content` branch, read reasoning robustly (the OpenAI SDK may expose it as an attribute or only in `model_extra`):
  ```python
  reasoning = getattr(delta, "reasoning_content", None)
  if reasoning is None:
      reasoning = (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
  if reasoning:
      yield ModelChunk(type="reasoning", text=reasoning)
  ```

### 5.2 `app/chat/events.py`
- Add a builder:
  ```python
  def reasoning(content: str) -> dict:
      return {"type": "reasoning", "content": content}
  ```

### 5.3 `app/chat/loop.py`
- In the `async for chunk in model.stream(...)` loop (`loop.py:220-223`), add a branch that yields the reasoning **immediately** (so the panel fills live), leaving the text-buffering and tool-collection branches untouched:
  ```python
  if chunk.type == "text":
      text_parts.append(chunk.text or "")
  elif chunk.type == "reasoning":
      yield reasoning(chunk.text or "")
  elif chunk.type == "tool_call":
      ...
  ```
- Import `reasoning` from `app.chat.events`. The existing `step("thinking", …)` events (`loop.py:204/232`) are unchanged and gate the panel's expand/collapse on the frontend.

## 6. Frontend changes

### 6.1 `src/types/chat.ts`
- Add `{ type: "reasoning"; content: string }` to the `StreamEvent` union.

### 6.2 `src/hooks/useStreamChat.ts`
- Add `streamedReasoning` state (string), reset to `""` at turn start alongside `streamedContent`.
- Add `case "reasoning": setStreamedReasoning((prev) => prev + event.content); break;` (mirrors the `case "token"` accumulation at `useStreamChat.ts:75`).
- Expose `streamedReasoning` in the hook's return alongside `steps` / `streamedContent`.

### 6.3 `ThoughtsPanel` component + wiring
- New `src/components/chat/ThoughtsPanel.tsx`: a collapsible panel (reuse the Brain iconography / styling from `StepTracker`'s "thinking" step) showing `streamedReasoning` as muted, monospace-ish text. Renders `null` when reasoning is empty.
- Behavior: **auto-expanded while the "thinking" step is active, auto-collapsed once it completes** (user can still toggle). Derive active/complete from the existing `steps` state.
- Wire it into the assistant-message area of `src/routes/_auth.chat.$sessionId.tsx`, near `StepTracker`, for the in-flight streaming turn only (ephemeral — not rendered for historical messages).

## 7. Cleanup

Remove `client.py:68-71` debug prints (folded into §5.1; called out separately because it's independently valuable and unconditional).

## 8. Testing

- **Backend unit** (`tests/test_chat_client.py` or the loop's test): a fake streamed response whose deltas carry `reasoning_content` → `OpenAIModelClient.stream` yields `ModelChunk(type="reasoning", text=…)` in order, and does NOT fold reasoning into text chunks. `events.reasoning("x") == {"type":"reasoning","content":"x"}`. The loop, given a scripted client that emits a reasoning chunk then a text chunk then done, yields a `reasoning` event (before the final `token`) and the answer is unaffected.
- **Backend regression:** existing chat-loop / client tests stay green (token/tool/step unchanged); no debug output in captured stdout.
- **Frontend:** typecheck + build (repo has no FE test runner, per prior stages) + a controller click-through against a reasoning model (llama-server Qwen3.6) confirming the Thoughts panel fills live then collapses, and the answer renders normally.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| SDK doesn't expose `reasoning_content` as an attribute | Robust access via `getattr` + `model_extra` fallback (§5.1). |
| A non-reasoning model never sends the field | `if reasoning:` guard → no reasoning events, panel renders `null`. No behavior change. |
| Reasoning is large/long | Ephemeral + collapsible; it's live-only, not stored or re-fed to the model. |
| Wire-contract drift (events.py ↔ types/chat.ts) | Add the variant in both in the same change; the `events.py` docstring already mandates byte-for-byte match. |

## 10. Follow-ups (not built here)

- Persist reasoning on the Message (schema + migration) if revisiting past thoughts becomes valuable.
- Inline `<think>` streaming parser, only if a deployed model emits tags instead of `reasoning_content`.
