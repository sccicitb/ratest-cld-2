"""TTS engines behind one protocol (§1b) — the mirror of `engines.py`.

Supertonic 3: 99M params, 31 languages including `id`, ONNX Runtime, ~385 MB
on disk, RTF ~0.195 on CPU. Chosen over Piper on measured WER over Indonesian
text (2.5% vs 3.7% mean) plus a 3x lead on English loanwords, and because §3
real-time voice needs expressive range that a one-voice-per-file engine
cannot give.

Text normalization lives HERE, inside the adapter, not in a shared layer:
the identical normalization improved Supertonic (numbers 21.1% -> 4.5%) and
made Piper WORSE (4.5% -> 8.9%), because Piper ships its own front-end. A
future Piper adapter simply would not call it.
"""
from __future__ import annotations

import re
from typing import Protocol

import numpy as np

from .config import Settings

#: The voice styles Supertonic 3 ships. A voice name arriving from a client is
#: untrusted input that resolves to `voice_styles/<name>.json`, so this list is
#: a whitelist, not documentation.
VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]

#: One of five voices that scored 0.0% WER on the round-trip sweep.
DEFAULT_VOICE = "F2"

_UNITS = ["nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh",
          "delapan", "sembilan"]

_NUMBER_RE = re.compile(r"\d[\d.,]*")
# The scale word must be swallowed with the amount -- see the docstring on
# tts_normalize.
_RUPIAH_RE = re.compile(
    r"\bRp\.?\s*(\d[\d.,]*)(\s+(?:triliun|miliar|juta|ribu))?", re.IGNORECASE
)


def spell_id(n: int) -> str:
    """Indonesian cardinal for *n* (handles the se- prefixes)."""
    if n < 10:
        return _UNITS[n]
    if n < 12:
        return {10: "sepuluh", 11: "sebelas"}[n]
    if n < 20:
        return f"{_UNITS[n - 10]} belas"
    if n < 100:
        head, rest = divmod(n, 10)
        return f"{_UNITS[head]} puluh" + (f" {spell_id(rest)}" if rest else "")
    for div, word in ((10**12, "triliun"), (10**9, "miliar"),
                      (10**6, "juta"), (1000, "ribu"), (100, "ratus")):
        if n >= div:
            head, rest = divmod(n, div)
            prefix = ("seratus" if div == 100 and head == 1
                      else "seribu" if div == 1000 and head == 1
                      else f"{spell_id(head)} {word}")
            return prefix + (f" {spell_id(rest)}" if rest else "")
    return str(n)


def _spell_token(tok: str) -> str:
    """Spell one numeric token, preserving trailing sentence punctuation."""
    trail = ""
    while tok and tok[-1] in ".,":
        trail = tok[-1] + trail
        tok = tok[:-1]
    if not tok:
        return trail
    if tok.isdigit() and tok.startswith("0"):
        # "RT 03" is "RT nol tiga": the zero is part of how it is said aloud,
        # and dropping it made the engine invent words.
        return " ".join(_UNITS[int(d)] for d in tok) + trail
    tok = tok.replace(".", "")  # thousands separator
    if "," in tok:
        whole, _, frac = tok.partition(",")
        if whole.isdigit() and frac.isdigit():
            digits = " ".join(_UNITS[int(d)] for d in frac)
            return f"{spell_id(int(whole))} koma {digits}" + trail
        return (spell_id(int(whole)) if whole.isdigit() else tok) + trail
    return (spell_id(int(tok)) if tok.isdigit() else tok) + trail


def tts_normalize(text: str) -> str:
    """Expand digits and `Rp` for synthesis, PRESERVING punctuation and case.

    Written Indonesian puts the currency marker before the digits and the
    scale after them, so "Rp 2,3 miliar" must become "dua koma tiga MILIAR
    rupiah". A substitution that ignores the scale word emits "dua koma tiga
    rupiah miliar".
    """
    text = _RUPIAH_RE.sub(
        lambda m: f"{_spell_token(m.group(1))}{m.group(2) or ''} rupiah", text
    )
    return _NUMBER_RE.sub(lambda m: _spell_token(m.group(0)), text)


class Synthesizer(Protocol):
    name: str
    model: str
    voices: list[str]

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]: ...


class SupertonicSynthesizer:
    """Supertonic 3 via ONNX Runtime. Whole-text and blocking: the library
    has no incremental output (`max_chunk_length` is internal text
    segmentation). Fine for §1b, which reads a finished answer; §3 will need
    sentence chunking written by us."""

    name = "supertonic"

    def __init__(self, s: Settings) -> None:
        from supertonic import TTS

        self.model = s.tts_model_dir or "supertonic-3"
        self._steps = s.tts_steps
        self._speed = s.tts_speed
        # `model_dir` is a keyword, and the first positional argument is a model
        # NAME -- passing a path there fails with "Invalid model". auto_download
        # is off for the air-gapped path on purpose: a provisioning gap must
        # fail loudly at startup, not silently reach for the network.
        self._tts = (
            TTS(model_dir=s.tts_model_dir, auto_download=False)
            if s.tts_model_dir
            else TTS(auto_download=True)
        )
        # Styles are resolved once at load: doing it per request would put a
        # file read in front of every synthesis, and it is also the second
        # place an unknown voice would reach the filesystem.
        #
        # `voices` reports what actually loaded rather than what we hope shipped
        # (spec §3.4), so an incomplete provisioning directory shows up as a
        # missing voice in /health instead of a 500 on first use.
        available = set(getattr(self._tts, "voice_style_names", None) or VOICES)
        self._styles = {
            v: self._tts.get_voice_style(voice_name=v) for v in VOICES if v in available
        }
        self.voices = sorted(self._styles, key=VOICES.index)

    def synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]:
        if voice not in self._styles:
            raise ValueError(f"unknown voice: {voice!r}")
        wav, _duration = self._tts.synthesize(
            text=tts_normalize(text),
            lang="id",
            voice_style=self._styles[voice],
            total_steps=self._steps,
            speed=self._speed,
        )
        # The library returns shape (1, N), not (N,). Flattened here so the
        # protocol's contract is genuinely 1-D mono: the WAV writer only
        # produced correct audio because tobytes() happens to flatten
        # row-major, which would silently interleave if this ever became
        # (2, N).
        return np.asarray(wav, dtype=np.float32).reshape(-1), 44_100


def build_synthesizer(s: Settings) -> Synthesizer:
    if s.tts_engine == "supertonic":
        return SupertonicSynthesizer(s)
    raise ValueError(f"unknown TTS_ENGINE: {s.tts_engine!r}")
