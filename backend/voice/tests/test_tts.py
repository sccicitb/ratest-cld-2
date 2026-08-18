"""TTS text front-end (§1b).

The measured reason this exists: raw Supertonic scored 21.1% WER on the
numbers category, reading "Rp 875.000.000" as "2775 ribu" and "RT 03 RW 07"
as "RT NOA 3 RW Loara Juju". Expanding digits first dropped that to 4.5%.
"""
from __future__ import annotations

from voice.service.tts import VOICES, DEFAULT_VOICE, tts_normalize


def test_default_voice_is_f2_and_is_a_real_voice():
    assert DEFAULT_VOICE == "F2"
    assert DEFAULT_VOICE in VOICES
    assert len(VOICES) == 10


def test_currency_keeps_the_scale_word_with_the_amount():
    """"Rp 2,3 miliar" is "dua koma tiga MILIAR rupiah".

    A naive substitution straddles the scale word and says "dua koma tiga
    rupiah miliar", which is a different (and meaningless) quantity.
    """
    assert tts_normalize("Rp 2,3 miliar") == "dua koma tiga miliar rupiah"


def test_large_currency_amount_is_spelled_in_full():
    assert tts_normalize("Rp 875.000.000") == "delapan ratus tujuh puluh lima juta rupiah"


def test_leading_zero_is_spoken_digit_by_digit():
    """"RT 03" is "RT nol tiga". Dropping the zero is what produced the
    "RW Loara Juju" garbage in the probe."""
    assert tts_normalize("RT 03 RW 07") == "RT nol tiga RW nol tujuh"


def test_punctuation_and_casing_survive():
    """Commas and full stops are where the engine takes its breath; casing
    carries acronyms. The STT-side normalizer flattens both, which is right
    for scoring and wrong for speaking."""
    out = tts_normalize("Data BPS per 31 Desember 2025, mencatat 14 kelurahan.")
    assert out.startswith("Data BPS per tiga puluh satu Desember")
    assert out.endswith("empat belas kelurahan.")
    assert "," in out


def test_text_without_numbers_is_returned_unchanged():
    assert tts_normalize("Dokumen ditemukan.") == "Dokumen ditemukan."
