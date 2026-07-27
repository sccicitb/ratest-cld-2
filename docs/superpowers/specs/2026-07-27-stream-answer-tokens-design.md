# Stream answer tokens live (fix time-to-first-token) — design

- **Date:** 2026-07-27
- **Status:** Draft
- **Scope:** `app/chat/loop.py` (stream) + `frontend/src/hooks/useStreamChat.ts` (tool-reset). QoL "thread A".
- **Related:** the chat SSE tool-loop; thinking-visibility (reasoning already streams; the *answer* still buffers).

## 1. Problem

`run_turn` buffers the entire answer and emits it as a single `token` event **after** generation completes (`loop.py:236-238` — `full_text = "".join(text_parts); yield token(full_text)`). The model streams tokens, but the app hides the stream, so the user sees nothing until the whole answer is done — the felt "long time to first token" is really "time to *last* token."

## 2. Decision

Stream each text chunk as a `token` event **as it arrives**, and drop the buffered end-of-turn emit. Persistence is unchanged (the saved assistant message is still the full final answer). A tiny frontend rule clears the live bubble when a tool call starts, so multi-tool turns stay clean.

## 3. Design

**Backend — `loop.py`:**
- In the `async for chunk in model.stream(...)` loop (currently `if chunk.type == "text": text_parts.append(...)`), also **`yield token(chunk.text)`** live. Keep the `text_parts.append` — the final iteration's `text_parts` is what gets persisted.
- In the final-answer branch, **remove** `full_text = "".join(text_parts); if full_text: yield token(full_text)` (already streamed). Keep `final_text_parts.append("".join(text_parts))` so the persisted message is identical to today.
- Everything else (tool collection/execution, steps, reasoning, artifact linkage, `done`) unchanged.

**Frontend — `useStreamChat.ts`:**
- The `case "token"` accumulation (`streamedContent += content`) already handles many small events — no change needed for the common path.
- Add to `case "step"`: when the step is `calling_tool` with status `active`, reset `streamedContent` to `""`. This clears any interim (suppressed) chatter so only the final answer streams into the bubble; live view then matches the persisted answer on every path.

## 4. Behavior

- **No-tool turn (majority):** the answer types out from the first token — the pause-then-wall-of-text is gone.
- **Tool turn:** brief interim chatter (if the model emits any) clears when the tool step starts; the final answer types live.
- **Persistence & reload:** the saved assistant message equals the full final answer, exactly as before. Interim chatter is ephemeral (never persisted) — the existing "suppress interim text" rule is preserved.

## 5. Testing

- Loop test: drive `run_turn` with a fake client that yields several `text` chunks; assert **multiple** `token` events stream in order, their concatenation equals the answer, and the persisted `Message.content` equals the full answer.
- Regression: existing `test_chat_loop` (persistence, tool loop, done, reasoning ordering) stays green.
- Frontend: typecheck + build; controller/user runs FE+BE against llama-server to confirm live typing.

## 6. Non-goals

- No change to tool execution, persistence content, reasoning/steps, the model client, or the SSE transport.
- Not the decouple/cross-chat-progress work (QoL "thread B") — separate.
