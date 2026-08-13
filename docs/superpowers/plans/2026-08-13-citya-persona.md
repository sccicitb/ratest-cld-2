# Citya Persona + Indonesian Enforcement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every chat turn a system message that makes the assistant answer in Bahasa Indonesia and identify itself as Citya, an assistant for the city.

**Architecture:** A new `app/chat/prompt.py` owns the default prompt text and a `system_message()` helper. `app/config.py` gains `system_prompt`, which overrides the default (and disables the system message entirely when set to empty). `run_turn` prepends the result when assembling `messages`. The prompt is never written to the database — it is re-applied fresh on every turn.

**Tech Stack:** Python 3.10, FastAPI, pydantic-settings, pytest, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-13-citya-persona-design.md`

## Global Constraints

- Run every backend command from `backend/` with `env -u VIRTUAL_ENV uv run …`. A bare `pytest` picks up the wrong interpreter on this repo.
- Baseline suite before this plan: **377 tests collected, 0 failures.** This plan adds 8, ending at 385.
- The prompt text is Indonesian. Do not translate it into English in code or tests.
- The system message must never be persisted as a `Message` row, and never appear in the frontend transcript.
- `settings.system_prompt` has three meaningful states — `None` (default), non-empty string (override), empty string (disabled). All three are tested; do not collapse them.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/chat/prompt.py` (create) | The default prompt text and `system_message()`. Nothing else. |
| `backend/app/config.py` (modify) | One new setting, `system_prompt`. |
| `backend/app/chat/loop.py` (modify) | Prepend the system message in `run_turn`. |
| `backend/tests/test_prompt.py` (create) | The three config states of `system_message()`. |
| `backend/tests/test_chat_loop.py` (modify) | Delivery on every model call; never persisted. |
| `backend/.env.example`, `backend/.env.prod.example` (modify) | Commented `SYSTEM_PROMPT`. |
| `docs/BACKEND_SPEC.md` (modify) | §7: turn construction begins with a system message. **Needs sign-off — see Task 3.** |

---

### Task 1: The prompt module and its setting

**Files:**
- Create: `backend/app/chat/prompt.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_prompt.py` (create)

**Interfaces:**
- Consumes: `app.config.settings`
- Produces: `DEFAULT_SYSTEM_PROMPT: str` and `system_message() -> dict | None`, where the dict is `{"role": "system", "content": <str>}`. Task 2 calls `system_message()`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_prompt.py`:

```python
"""System prompt (Citya persona) — the three states of SYSTEM_PROMPT."""
from __future__ import annotations

from app.chat.prompt import DEFAULT_SYSTEM_PROMPT, system_message
from app.config import settings


def test_default_prompt_names_citya_and_demands_indonesian():
    assert "Citya" in DEFAULT_SYSTEM_PROMPT
    assert "Bahasa Indonesia" in DEFAULT_SYSTEM_PROMPT


def test_system_message_unset_uses_the_built_in_default(monkeypatch):
    monkeypatch.setattr(settings, "system_prompt", None)
    assert system_message() == {"role": "system", "content": DEFAULT_SYSTEM_PROMPT}


def test_system_message_override_replaces_the_default(monkeypatch):
    monkeypatch.setattr(settings, "system_prompt", "Kamu adalah asisten uji coba.")
    assert system_message() == {
        "role": "system",
        "content": "Kamu adalah asisten uji coba.",
    }


def test_system_message_empty_disables_the_system_message(monkeypatch):
    """Empty is an escape hatch, not a typo: it runs the model with no persona
    so a bad answer can be blamed on the model rather than the prompt."""
    monkeypatch.setattr(settings, "system_prompt", "")
    assert system_message() is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_prompt.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.chat.prompt'`

- [ ] **Step 3: Add the setting**

In `backend/app/config.py`, immediately after the `max_concurrent_chat_turns` line and before `# --- Admin bootstrap (§M1) ---`:

```python
    # --- System prompt (§7): the Citya persona + Indonesian rule.
    #     None (unset) = the built-in default in app/chat/prompt.py.
    #     A string     = replaces it.
    #     Empty string = no system message at all, for comparing raw model
    #                    behaviour against the persona without editing code.
    system_prompt: str | None = None
```

- [ ] **Step 4: Create the prompt module**

Create `backend/app/chat/prompt.py`:

```python
"""The system prompt (§7) — who the assistant is and what language it answers in.

Its own module rather than a constant in `loop.py`: this is content, edited on
its own cadence by whoever tunes the deployment, and `loop.py` is already the
largest file on the chat path.
"""
from __future__ import annotations

from app.config import settings

#: Overridden by SYSTEM_PROMPT. Indonesian on purpose — the instruction is more
#: reliably followed when it is written in the language it is asking for.
DEFAULT_SYSTEM_PROMPT = (
    "Kamu adalah Citya, asisten kota. "
    "Selalu jawab dalam Bahasa Indonesia, apa pun bahasa yang digunakan pengguna. "
    "Jika pengguna bertanya siapa kamu, perkenalkan diri sebagai Citya, asisten kota."
)


def system_message() -> dict | None:
    """The system message to prepend to a turn, or None when disabled.

    Read per call rather than captured at import: tests monkeypatch the setting,
    and an operator changing .env gets the new prompt on restart with no other
    moving parts.
    """
    text = DEFAULT_SYSTEM_PROMPT if settings.system_prompt is None else settings.system_prompt
    if not text:
        return None
    return {"role": "system", "content": text}
```

- [ ] **Step 5: Run it to verify it passes**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_prompt.py -q`
Expected: PASS — 4 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/chat/prompt.py backend/app/config.py backend/tests/test_prompt.py
git commit -m "feat(chat): Citya system prompt module and SYSTEM_PROMPT setting"
```

---

### Task 2: Prepend it to every turn

**Files:**
- Modify: `backend/app/chat/loop.py` (import block; `run_turn` at line 202)
- Test: `backend/tests/test_chat_loop.py` (append)

**Interfaces:**
- Consumes: `system_message()` from Task 1.
- Produces: no new symbols. `run_turn`'s observable behaviour changes: the list passed to `ModelClient.stream()` now begins with the system message on every call of the turn.

Background the implementer needs: `tests/test_chat_loop.py` already has a `_FakeModelClient` that records every `.stream()` call as `(messages, tools)` in `model.calls`, a `_FakeTool` named `fake_tool`, and helpers `_make_session`, `_ctx`, `_registry`, `_collect`. Reuse them; do not write new fakes.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_chat_loop.py`:

```python
# --- System prompt (Citya persona) ----------------------------------------


def test_system_prompt_leads_every_model_call_including_after_a_tool(
    session_factory, monkeypatch
):
    """The post-tool call is the one that writes KB-grounded answers.

    A system message that only reached the first call would govern the opening
    pass and then go missing exactly where the grounded answer is composed.
    """
    monkeypatch.setattr(settings, "system_prompt", None)
    db, session = _make_session(session_factory)
    model = _FakeModelClient([
        [ModelChunk(type="tool_call", id="c1", name="fake_tool", arguments={"q": "x"})],
        [ModelChunk(type="text", text="Jawaban akhir.")],
    ])

    asyncio.run(_collect(run_turn(
        db=db, session=session, message="halo",
        registry=_registry(_FakeTool()), model=model, ctx=_ctx(db, session),
    )))

    assert len(model.calls) == 2
    for messages, _tools in model.calls:
        assert messages[0]["role"] == "system"
        assert "Citya" in messages[0]["content"]


def test_system_prompt_is_never_persisted(session_factory, monkeypatch):
    """It is re-applied per turn, so it must not reach the DB or the transcript."""
    monkeypatch.setattr(settings, "system_prompt", None)
    db, session = _make_session(session_factory)
    model = _FakeModelClient([[ModelChunk(type="text", text="Halo!")]])

    asyncio.run(_collect(run_turn(
        db=db, session=session, message="halo",
        registry=_registry(), model=model, ctx=_ctx(db, session),
    )))

    db.refresh(session)
    assert [m.role for m in session.messages] == ["user", "assistant"]


def test_system_prompt_empty_sends_no_system_message(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "system_prompt", "")
    db, session = _make_session(session_factory)
    model = _FakeModelClient([[ModelChunk(type="text", text="Halo!")]])

    asyncio.run(_collect(run_turn(
        db=db, session=session, message="halo",
        registry=_registry(), model=model, ctx=_ctx(db, session),
    )))

    assert model.calls[0][0][0]["role"] == "user"


def test_system_prompt_override_reaches_the_model(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "system_prompt", "Kamu adalah asisten uji coba.")
    db, session = _make_session(session_factory)
    model = _FakeModelClient([[ModelChunk(type="text", text="Halo!")]])

    asyncio.run(_collect(run_turn(
        db=db, session=session, message="halo",
        registry=_registry(), model=model, ctx=_ctx(db, session),
    )))

    assert model.calls[0][0][0] == {
        "role": "system",
        "content": "Kamu adalah asisten uji coba.",
    }
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_chat_loop.py -q -k system_prompt`
Expected: 3 FAIL, 1 PASS. `..._empty_sends_no_system_message` passes already — there is no system message yet, so it is a true statement about the current code. That is fine and expected; it becomes meaningful in Step 4.

- [ ] **Step 3: Wire it into `run_turn`**

In `backend/app/chat/loop.py`, add to the import block (alphabetical, after `from app.chat.events import …`):

```python
from app.chat.prompt import system_message
```

Then at line 202, replace:

```python
        messages = _cap_images(messages, settings.max_vision_images_per_turn)
```

with:

```python
        messages = _cap_images(messages, settings.max_vision_images_per_turn)
        # Prepended after capping so `_cap_images` keeps operating on exactly
        # the user/assistant turns it was written for. Index 0 for the rest of
        # the turn: the loop only ever appends, so the persona still applies on
        # the iteration that composes the post-tool answer.
        sys_msg = system_message()
        if sys_msg is not None:
            messages = [sys_msg] + messages
```

- [ ] **Step 4: Run them to verify they pass**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest tests/test_chat_loop.py -q -k system_prompt`
Expected: PASS — 4 passed

- [ ] **Step 5: Run the full backend suite for regressions**

Run: `cd backend && env -u VIRTUAL_ENV uv run pytest -q`
Expected: **385 passed**, 0 failures. Pay attention to `tests/test_vision_loop.py` and `tests/test_chat_turns.py` — they assert on message lists and are the likeliest place an off-by-one index shows up.

- [ ] **Step 6: Commit**

```bash
git add backend/app/chat/loop.py backend/tests/test_chat_loop.py
git commit -m "feat(chat): prepend the system prompt to every model call"
```

---

### Task 3: Documentation and the spec change

**Files:**
- Modify: `backend/.env.example`
- Modify: `backend/.env.prod.example`
- Modify: `docs/BACKEND_SPEC.md`

**Interfaces:**
- Consumes: the `SYSTEM_PROMPT` name and three states from Task 1.
- Produces: nothing code depends on.

> **Sign-off gate.** `docs/BACKEND_SPEC.md` is not edited without the user's explicit approval. Step 3 below is the only step in this plan that touches it. If approval has not been given, do Steps 1–2, commit, and stop — report that Step 3 is pending sign-off rather than doing it anyway.

- [ ] **Step 1: `backend/.env.example`**

Append:

```
# --- System prompt (§7): the Citya persona + Indonesian rule.
#     Unset  = the built-in default (app/chat/prompt.py).
#     Set    = replaces it.
#     Empty  = no system message at all (raw model behaviour).
# SYSTEM_PROMPT=Kamu adalah Citya, asisten kota. Selalu jawab dalam Bahasa Indonesia.
```

- [ ] **Step 2: `backend/.env.prod.example`**

Append the same block, with the deployment note:

```
# --- System prompt (§7): the Citya persona + Indonesian rule.
#     Unset  = the built-in default (app/chat/prompt.py) — Citya, answers in
#              Bahasa Indonesia whatever language the user writes in.
#     Set    = replaces it. Takes effect on backend restart; no rebuild needed.
#     Empty  = no system message at all. Use this to check whether a bad answer
#              is the model or the prompt.
# SYSTEM_PROMPT=Kamu adalah Citya, asisten kota. Selalu jawab dalam Bahasa Indonesia.
```

- [ ] **Step 3: `docs/BACKEND_SPEC.md` §7 — REQUIRES SIGN-OFF**

In §7 ("Chat streaming (SSE) — agentic tool-calling RAG"), immediately **before** the `### The retrieval tool` heading, insert:

```markdown
### Turn construction

Every turn is assembled as `[system] + history + [user message]`. The system
message carries the assistant's identity (**Citya**, an assistant for the city)
and the standing instruction to answer in **Bahasa Indonesia** regardless of the
language the user writes in.

It is prepended per turn and **never persisted** as a message: it does not
appear in `GET /api/sessions/:id/messages`, and changing it changes behaviour in
existing conversations with no migration. `SYSTEM_PROMPT` overrides the built-in
default; set empty, no system message is sent at all.
```

- [ ] **Step 4: Commit**

```bash
git add backend/.env.example backend/.env.prod.example docs/BACKEND_SPEC.md
git commit -m "docs(chat): SYSTEM_PROMPT in env examples and spec §7"
```

---

### Task 4: Manual verification against the real model

The tests prove the instruction is *delivered*. Whether the model *obeys* is a model behaviour and can only be checked against the live endpoint.

**Files:** none.

- [ ] **Step 1: Start the backend**

```bash
cd backend && env -u VIRTUAL_ENV COOKIE_SECURE=false uv run uvicorn app.main:app --port 8000
```

- [ ] **Step 2: Ask in English, expect Indonesian**

Log in, create a session, and send: `What is the capital of France?`
Expected: an answer in Bahasa Indonesia, not English.

- [ ] **Step 3: Ask who it is**

Send: `Who are you?`
Expected: identifies as Citya, an assistant for the city, in Bahasa Indonesia.

- [ ] **Step 4: Confirm the transcript is clean**

`GET /api/sessions/<id>/messages` — expected: only `user` and `assistant` roles, no `system`.

- [ ] **Step 5: Report**

Report the three actual replies verbatim. If the model answers in English despite the instruction, that is a prompt-strength finding, not a code bug — report it and stop rather than editing code.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2.1 always Indonesian | Task 1 Step 4 (prompt text), Task 4 Step 2 (behaviour) |
| §2.2 identifies as Citya | Task 1 Step 4, Task 4 Step 3 |
| §2.3 editable without code change | Task 1 Step 3, Task 3 Steps 1–2 |
| §2.4 no topic restriction | Nothing to build — the prompt says nothing about topics |
| §3.1 `prompt.py` | Task 1 Step 4 |
| §3.2 three config states | Task 1 Steps 1, 3, 4 |
| §3.3 prepend after `_cap_images`, not persisted, survives the tool loop | Task 2 Steps 1, 3 |
| §3.4 reasoning language out of scope | No task, by design |
| §4 five tests | Task 1 (3 state tests + 1 text test), Task 2 (4 loop tests) = 8 |
| §5 docs + spec | Task 3 |

**Type consistency:** `system_message() -> dict | None` is defined in Task 1 and called in Task 2 Step 3 with a `None` check. `DEFAULT_SYSTEM_PROMPT` is referenced in Task 1's tests only. `settings.system_prompt` is `str | None`, monkeypatched with `None`, `""`, and a string across both test files.

**Note on test count:** §4 of the spec listed five tests; this plan writes eight. The extra three are the default-text assertion and splitting the override/disabled cases across both the unit and loop layers. More coverage than specified is not a deviation worth reconciling.
