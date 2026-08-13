# Citya Persona + Indonesian Enforcement — Design

**Date:** 2026-08-13
**Status:** approved (design), not yet implemented
**Touches:** BACKEND_SPEC.md §7 (turn construction)

## 1. Problem

The app has **no system prompt**. `run_turn` builds

```python
messages = history + [{"role": "user", "content": current_content}]
```

(`app/chat/loop.py`) and hands that straight to the model. No `{"role": "system"}`
message is constructed anywhere in `backend/app/`, and neither BACKEND_SPEC.md
nor V1.1_SPEC.md mentions one.

Two consequences the deployment cares about:

- **No identity.** Asked "siapa kamu?", the model answers as whatever base model
  is behind `MODEL_BASE_URL`. For a city-facing deployment it should answer as
  Citya.
- **No language guarantee.** Replies track whatever the base model feels like.
  Observed in testing: an Indonesian question got an Indonesian answer but
  English reasoning. Nothing enforces either.

## 2. Requirements

1. Every reply is in Bahasa Indonesia, **regardless of the language the user
   writes in**. An English question gets an Indonesian answer.
2. Asked who it is, the assistant identifies as **Citya, an assistant for the
   city**.
3. The wording is changeable on the air-gapped Windows host **without a code
   change or rebuild**.
4. No topic restriction. Citya states its identity but still answers questions
   outside city matters — an LLM topic gate is leaky in both directions and
   would produce false refusals. Revisit once real usage shows it is needed.

## 3. Design

### 3.1 `app/chat/prompt.py` (new)

Holds the default prompt text and one helper:

```python
DEFAULT_SYSTEM_PROMPT = (
    "Kamu adalah Citya, asisten kota. "
    "Selalu jawab dalam Bahasa Indonesia, apa pun bahasa yang digunakan pengguna. "
    "Jika pengguna bertanya siapa kamu, perkenalkan diri sebagai Citya, asisten kota."
)


def system_message() -> dict | None:
    """The system message for a turn, or None when disabled."""
```

Its own module rather than a constant in `loop.py`: the prompt is content that
will be edited on its own cadence, and `loop.py` is already the largest file on
the chat path.

### 3.2 `app/config.py`

```python
system_prompt: str | None = None
```

Three states, and the third is deliberate:

| `SYSTEM_PROMPT` | Behaviour |
|---|---|
| unset | built-in Citya default |
| set to text | that text replaces the default |
| set to empty | **no system message at all** |

The empty case is the escape hatch for comparing raw model behaviour against
the persona without editing code — useful when a model swap makes answers worse
and the prompt is a suspect.

### 3.3 `app/chat/loop.py`

Prepend the system message when assembling `messages`, **after** `_cap_images`
so that function's logic is untouched:

```python
messages = _cap_images(messages, settings.max_vision_images_per_turn)
sys_msg = system_message()
if sys_msg is not None:
    messages = [sys_msg] + messages
```

Two properties that matter:

- **Not persisted.** `_history_messages` reads only real `Message` rows, so the
  prompt is re-applied fresh on every turn. Editing it changes behaviour in
  existing conversations, and there is no migration and no stale copy in the DB.
- **Survives the tool loop.** The system message sits at index 0 while the loop
  appends assistant/tool messages, so it still applies on the iteration that
  writes the final answer. That is the iteration that matters: KB-grounded
  answers are written after the tool result comes back.

### 3.4 Reasoning language (accepted limitation)

The rule says *jawab* — answer. Reasoning tokens may still stream in English,
and the Thoughts panel shows them to users. Forcing Indonesian reasoning is one
more sentence in the prompt but tends to cost reasoning quality, so it is out of
scope here. If the English Thoughts panel proves to be a problem in use, that
sentence is a one-line change to `DEFAULT_SYSTEM_PROMPT` — no code change.

## 4. Testing

Against the existing `_FakeModelClient` in `tests/test_chat_loop.py`, which
records the `messages` passed to each `.stream()` call:

1. The first message of the first model call is `role: "system"` and names Citya.
2. It is still first on the **second** model call, after a tool call — the
   post-tool answer is governed too.
3. `SYSTEM_PROMPT` set to custom text replaces the default.
4. `SYSTEM_PROMPT` set to empty produces no system message.
5. No `system` row is written to `session.messages` (the prompt never reaches
   the database, and so never reaches the frontend transcript).

Language enforcement itself is a model behaviour and is not asserted in tests —
the tests verify the instruction is delivered, on every model call, and nothing
more. Verifying the model actually complies is a manual check against the real
endpoint.

## 5. Docs

- `.env.example` and `.env.prod.example`: commented `SYSTEM_PROMPT` with the
  three states from §3.2.
- **BACKEND_SPEC.md §7**: turn construction begins with a system message. This
  is a spec change and needs sign-off before the edit lands.

## 6. Out of scope

- Per-user or per-session prompt overrides.
- Admin-UI editing of the prompt (needs a migration, an API, a UI, and tests —
  larger than the whole of the rest of this change).
- Topic restriction (§2.4).
- Forcing the reasoning language (§3.4).
