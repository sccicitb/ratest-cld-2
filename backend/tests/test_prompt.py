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
