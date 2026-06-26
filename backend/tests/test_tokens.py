"""Token routing tests (§6.1) — ``route_by_tokens`` inline / ingest decisions."""

from __future__ import annotations

from app.config import settings
from app.rag.tokens import route_by_tokens


def test_empty_or_whitespace_returns_inline():
    assert route_by_tokens("") == "inline"
    assert route_by_tokens("   \n\t  ") == "inline"


def test_short_text_returns_inline():
    words = "hello world " * 5  # 10 words — well under the default 6000
    assert route_by_tokens(words.strip()) == "inline"


def test_large_text_returns_ingest(monkeypatch):
    # Lower the threshold so we don't need 6000+ words in the test.
    monkeypatch.setattr(settings, "inline_token_budget", 10)
    words = "word " * 50  # 50 words > 10
    assert route_by_tokens(words.strip()) == "ingest"


def test_exactly_at_boundary_returns_inline(monkeypatch):
    monkeypatch.setattr(settings, "inline_token_budget", 5)
    assert route_by_tokens("a b c d e") == "inline"


def test_one_above_boundary_returns_ingest(monkeypatch):
    monkeypatch.setattr(settings, "inline_token_budget", 4)
    assert route_by_tokens("a b c d e") == "ingest"
