#!/usr/bin/env python
"""Smoke-test candidate STT engines on real Indonesian audio (voice mode §1a).

This is the STT counterpart of check_pdf_pipeline.py: a probe that decides an
engine choice with numbers from OUR audio instead of English-benchmark blog
posts. Voice mode must run local (air-gapped prod), and the corpus is
Indonesian with heavy English code-switching -- a combination that eliminates
most of the 2026 "fastest STT" field (Parakeet, Moonshine, Voxtral are all
English/European only), leaving two families worth measuring:

  faster-whisper (CTranslate2)   large-v3-turbo, large-v3
  Qwen3-ASR (qwen_asr toolkit)   1.7B, 0.6B   -- native code-switching, id supported

For each engine x clip it reports:
  WER/CER    -- against the read script (clips 01-08 are verbatim, so the
                script itself is the reference; no hand-transcription needed)
  LATENCY    -- wall time per clip + real-time factor (audio_sec / proc_sec)
  MEMORY     -- model load time, peak process RSS, per-process VRAM
  SILENCE    -- clip 10 is room tone: any output at all is a hallucination.
                With push-to-talk, users WILL record nothing, and a confident
                fabricated sentence entering the chat is far worse than "".

Scoring caveat (clip 06): engines disagree on "2023" vs "dua ribu dua puluh
tiga" -- both right, wildly different WER. Digits are spelled out on both
sides before scoring; raw output is always printed so you can judge acronyms
(SIPD, BPS) and currency yourself.

Usage (run each family in ITS OWN env -- these deps do not co-exist happily,
and neither belongs in the backend's env):

    # faster-whisper family
    env -u VIRTUAL_ENV uv run --with faster-whisper --with psutil --with nvidia-ml-py \
        python scripts/check_stt_pipeline.py --engines fw-turbo,fw-large-v3

    # Qwen3-ASR family
    env -u VIRTUAL_ENV uv run --with qwen-asr --with av --with psutil --with nvidia-ml-py \
        python scripts/check_stt_pipeline.py --engines qwen-1.7b,qwen-0.6b

    # one clip, forced language, keep raw JSON
    ... check_stt_pipeline.py --engines fw-turbo --clips 03 --language id --out report.json

Audio lives in backend/data/voice_samples/ (gitignored -- recordings are the
user's). Clips are matched by their leading NN- prefix, so file naming beyond
that is free.

Dev (Apple Silicon) vs prod (L40): device is autodetected (cuda > mps > cpu).
On a Mac, WER/CER, code-switching and the silence check are still valid -- they
are properties of the model. Latency, RTF and memory are NOT: CTranslate2 has
no Metal backend, so faster-whisper runs on the CPU there. Re-run on the L40
before quoting any speed number.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "voice_samples"
TARGET_SR = 16_000


# ---------------------------------------------------------------------------
# The read script -- clips 01-08 are verbatim, so these ARE the references.
# ---------------------------------------------------------------------------

@dataclass
class Clip:
    num: str
    label: str
    kind: str  # verbatim | freeform | silence
    reference: str = ""


CLIPS: list[Clip] = [
    Clip("01", "formal dictation", "verbatim",
         "Selamat pagi. Tolong carikan dokumen laporan realisasi anggaran belanja "
         "daerah tahun dua ribu dua puluh empat, khususnya bagian pendapatan asli "
         "daerah dan dana transfer dari pemerintah pusat."),
    Clip("02", "natural question", "verbatim",
         "Berapa jumlah penduduk kecamatan Coblong menurut data terakhir yang ada "
         "di basis pengetahuan?"),
    Clip("03", "code-switching", "verbatim",
         "Tolong summarize dokumen quarterly report yang tadi saya upload ke "
         "knowledge base, terus bandingkan dengan data revenue tahun lalu, dan "
         "bikin tabel perbandingannya."),
    Clip("04", "short command", "verbatim",
         "Cari dokumen tentang retribusi parkir."),
    Clip("05", "short command", "verbatim",
         "Buka file laporan bulan Juni."),
    Clip("06", "numbers/dates/acronyms", "verbatim",
         "Pada tahun dua ribu dua puluh tiga, total anggaran mencapai satu koma "
         "lima triliun rupiah. Data tersebut bersumber dari SIPD dan sudah "
         "diverifikasi oleh BPS pada tanggal tujuh belas Agustus."),
    Clip("07", "fast and informal", "verbatim",
         "Eh, coba deh cariin data yang kemarin itu, yang soal anggaran kelurahan, "
         "kayaknya ada di folder yang sama sama laporan triwulan, terus tolong "
         "ringkasin aja poin pentingnya."),
    Clip("08", "noisy (repeat of 02)", "verbatim",
         "Berapa jumlah penduduk kecamatan Coblong menurut data terakhir yang ada "
         "di basis pengetahuan?"),
    Clip("09", "spontaneous ~60s", "freeform"),
    Clip("10", "silence / room tone", "silence"),
]


# ---------------------------------------------------------------------------
# Text normalisation + WER/CER (no jiwer dependency -- it is 40 lines)
# ---------------------------------------------------------------------------

_UNITS = ["nol", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh",
          "delapan", "sembilan"]


def spell_id(n: int) -> str:
    """Indonesian cardinal for *n* (handles the se- prefixes: sepuluh/seratus/seribu)."""
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


def _spell_number_token(tok: str) -> str:
    """'2023' -> 'dua ribu dua puluh tiga'; '1,5' -> 'satu koma lima'."""
    tok = tok.replace(".", "")  # thousands separator
    if "," in tok:
        whole, _, frac = tok.partition(",")
        # A comma with no digits after it is punctuation ("tahun 2023, total"),
        # not a decimal point -- don't emit a dangling "koma".
        if whole.isdigit() and frac.isdigit():
            digits = " ".join(_UNITS[int(d)] for d in frac)
            return f"{spell_id(int(whole))} koma {digits}"
        return spell_id(int(whole)) if whole.isdigit() else tok
    return spell_id(int(tok)) if tok.isdigit() else tok


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, spell digits out, so 2023 == dua ribu ..."""
    text = text.lower().replace("rp", " rupiah ")
    text = re.sub(r"[^\w,\s]", " ", text)          # keep the decimal comma
    text = re.sub(r"(?<!\d),|,(?!\d)", " ", text)  # keep ONLY digit,digit
    tokens = [_spell_number_token(t) for t in text.split()]
    return " ".join(" ".join(tokens).split())


def _levenshtein(a: list, b: list) -> int:
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> float:
    r, h = normalize(ref).split(), normalize(hyp).split()
    return _levenshtein(r, h) / len(r) if r else (0.0 if not h else 1.0)


def cer(ref: str, hyp: str) -> float:
    r = normalize(ref).replace(" ", "")
    h = normalize(hyp).replace(" ", "")
    return _levenshtein(list(r), list(h)) / len(r) if r else (0.0 if not h else 1.0)


# ---------------------------------------------------------------------------
# Audio decode -- PyAV first (ships with faster-whisper), ffmpeg as fallback.
# Both engines then get an identical float32 16k mono waveform, so decoding
# differences can't skew the comparison.
# ---------------------------------------------------------------------------


def decode_audio(path: Path) -> tuple["object", float]:
    """Return (float32 mono ndarray at 16 kHz, duration_seconds)."""
    import numpy as np

    try:
        import av  # noqa: F401

        return _decode_av(path)
    except ImportError:
        pass

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "clip.wav"
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
               "-ac", "1", "-ar", str(TARGET_SR), "-f", "wav", str(wav)]
        try:
            subprocess.run(cmd, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise SystemExit(
                f"Cannot decode {path.name}: install PyAV (--with av) or ffmpeg. ({exc})"
            ) from exc
        import wave

        with wave.open(str(wav), "rb") as w:
            frames = w.readframes(w.getnframes())
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, len(samples) / TARGET_SR


def _decode_av(path: Path):
    import av
    import numpy as np

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=TARGET_SR
        )
        chunks = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32), 0.0
    samples = np.concatenate(chunks).astype(np.float32) / 32768.0
    return samples, len(samples) / TARGET_SR


# ---------------------------------------------------------------------------
# Resource probes (Windows-safe: `resource` is POSIX-only, so psutil first)
# ---------------------------------------------------------------------------


def autodetect_device() -> str:
    """cuda > mps > cpu. torch may be absent entirely (faster-whisper doesn't
    need it), in which case CPU is the only thing CTranslate2 can use anyway."""
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def peak_rss_mb() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        pass
    try:
        import resource

        kb_or_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return (kb_or_bytes / 1024**2 if sys.platform == "darwin" else kb_or_bytes / 1024)
    except Exception:
        return None


def process_vram_mb() -> float | None:
    """VRAM held by THIS process -- the card is shared with llama-server/BGE-M3,
    so a device-wide reading would be meaningless."""
    try:
        import pynvml
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
        pid = os.getpid()
        for idx in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
            for proc in pynvml.nvmlDeviceGetComputeRunningProcesses(handle):
                if proc.pid == pid and proc.usedGpuMemory:
                    return proc.usedGpuMemory / (1024 * 1024)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

FW_MODELS = {"fw-turbo": "large-v3-turbo", "fw-large-v3": "large-v3"}
QWEN_MODELS = {"qwen-1.7b": "Qwen/Qwen3-ASR-1.7B", "qwen-0.6b": "Qwen/Qwen3-ASR-0.6B"}
ENGINES = list(FW_MODELS) + list(QWEN_MODELS)


class FasterWhisper:
    """CTranslate2 backend. float16 on the prod L40 (48 GB -- no quantisation
    compromise); int8 on CPU.

    NOTE: CTranslate2 has no Metal/MPS backend, so on Apple Silicon this runs on
    the CPU no matter what --device says. Accuracy is unaffected (int8 vs fp16
    moves WER by a fraction of a point); speed obviously is."""

    def __init__(self, key: str, device: str, vad: bool = False):
        from faster_whisper import WhisperModel

        if device != "cuda":
            print("  (faster-whisper: CPU/int8 -- CTranslate2 has no MPS backend)")
        self.vad = vad
        self.model = WhisperModel(
            FW_MODELS[key],
            device="cuda" if device == "cuda" else "cpu",
            compute_type="float16" if device == "cuda" else "int8",
        )

    def transcribe(self, samples, language: str | None) -> str:
        segments, _ = self.model.transcribe(
            samples, language=language, beam_size=5, vad_filter=self.vad,
        )
        return "".join(seg.text for seg in segments).strip()


class QwenASR:
    """qwen_asr toolkit. The ForcedAligner companion is deliberately NOT loaded:
    it only produces word timestamps (which we don't need) and doesn't support
    Indonesian anyway."""

    LANGS = {"id": "Indonesian", "en": "English"}

    def __init__(self, key: str, device: str):
        import torch
        from qwen_asr import Qwen3ASRModel

        # bfloat16 on CUDA (what the model card uses); float16 on MPS to halve
        # unified memory -- 1.7B at float32 is ~6.8 GB, which hurts on an 8/16 GB
        # Mac. float32 on plain CPU, where fp16 is slower, not faster.
        dtype = {"cuda": torch.bfloat16, "mps": torch.float16}.get(device, torch.float32)
        self.model = Qwen3ASRModel.from_pretrained(
            QWEN_MODELS[key],
            dtype=dtype,
            device_map="cuda:0" if device == "cuda" else device,
            max_new_tokens=256,
        )

    def transcribe(self, samples, language: str | None) -> str:
        results = self.model.transcribe(
            audio=(samples, TARGET_SR), language=self.LANGS.get(language or ""),
        )
        return (results[0].text or "").strip()


def build_engine(key: str, device: str, vad: bool = False):
    return FasterWhisper(key, device, vad) if key in FW_MODELS else QwenASR(key, device)


def classify_silence(text: str) -> str:
    """Not all silence output is equal: '. . . .' is a cosmetic artifact a
    two-line filter removes, while a fabricated SENTENCE would reach the model
    as a real user message. Only the latter is disqualifying."""
    stripped = text.strip()
    if not stripped:
        return "clean"
    return "punct-only" if not re.search(r"\w", stripped) else "HALLUCINATED"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

@dataclass
class ClipResult:
    num: str
    label: str
    kind: str
    audio_sec: float
    proc_sec: float
    rtf: float
    text: str
    wer: float | None = None
    cer: float | None = None


@dataclass
class EngineResult:
    engine: str
    load_sec: float
    peak_rss_mb: float | None
    vram_mb: float | None
    clips: list[ClipResult] = field(default_factory=list)
    error: str | None = None


def discover(sample_dir: Path, wanted: set[str] | None) -> list[tuple[Clip, Path]]:
    pairs = []
    for clip in CLIPS:
        if wanted and clip.num not in wanted:
            continue
        matches = sorted(sample_dir.glob(f"{clip.num}-*"))
        if matches:
            pairs.append((clip, matches[0]))
        else:
            print(f"  ! no audio for clip {clip.num} ({clip.label}) -- skipped")
    return pairs


def run_engine(key: str, pairs, device: str, language: str | None, vad: bool = False) -> EngineResult:
    print(f"\n=== {key} " + "=" * (60 - len(key)))
    t0 = time.perf_counter()
    try:
        engine = build_engine(key, device, vad)
    except ImportError as exc:
        print(f"  SKIP -- {exc}. Install it in this env (see the usage header).")
        return EngineResult(key, 0.0, None, None, error=str(exc))
    load_sec = time.perf_counter() - t0
    print(f"  loaded in {load_sec:6.1f}s")

    result = EngineResult(key, load_sec, None, None)
    for clip, path in pairs:
        samples, audio_sec = decode_audio(path)
        t0 = time.perf_counter()
        text = engine.transcribe(samples, language)
        proc = time.perf_counter() - t0
        cr = ClipResult(
            num=clip.num, label=clip.label, kind=clip.kind, audio_sec=audio_sec,
            proc_sec=proc, rtf=(audio_sec / proc if proc else 0.0), text=text,
        )
        if clip.kind == "verbatim":
            cr.wer, cr.cer = wer(clip.reference, text), cer(clip.reference, text)

        flag = ""
        if clip.kind == "silence":
            verdict = classify_silence(text)
            flag = "  (clean)" if verdict == "clean" else f"  <-- {verdict.upper()}"
        score = f"WER {cr.wer:5.1%}  CER {cr.cer:5.1%}" if cr.wer is not None else " " * 22
        print(f"  {clip.num} {clip.label:<24} {audio_sec:5.1f}s  "
              f"{proc:5.2f}s  RTFx{cr.rtf:5.1f}  {score}{flag}")
        print(f"       -> {text or '(empty)'}")
        result.clips.append(cr)

    result.peak_rss_mb, result.vram_mb = peak_rss_mb(), process_vram_mb()
    scored = [c for c in result.clips if c.wer is not None]
    if scored:
        mean_wer = sum(c.wer for c in scored) / len(scored)
        mean_rtf = sum(c.rtf for c in result.clips) / len(result.clips)
        print(f"  -- mean WER {mean_wer:.1%} over {len(scored)} clips, "
              f"mean RTFx {mean_rtf:.1f}, "
              f"RSS {result.peak_rss_mb or float('nan'):.0f} MB, "
              f"VRAM {result.vram_mb or float('nan'):.0f} MB")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", default=",".join(ENGINES),
                    help=f"comma-separated: {', '.join(ENGINES)}")
    ap.add_argument("--clips", default=None, help="comma-separated clip numbers, e.g. 03,06")
    ap.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    ap.add_argument("--language", default=None,
                    help="force a language (id|en); omit for auto-detect")
    ap.add_argument("--device", default=None, choices=["cuda", "mps", "cpu"],
                    help="default: autodetect (cuda > mps > cpu)")
    ap.add_argument("--vad", action="store_true",
                    help="faster-whisper: enable the VAD pre-filter (kills silence artifacts)")
    ap.add_argument("--out", type=Path, default=None, help="write the raw JSON report here")
    args = ap.parse_args()

    if not args.sample_dir.is_dir():
        raise SystemExit(f"No sample dir: {args.sample_dir}")

    device = args.device or autodetect_device()
    if device != "cuda":
        print("NOTE: not CUDA -- WER/CER, code-switching and the silence check are\n"
              "      device-independent and DO transfer to prod. Latency, RTF and\n"
              "      memory do NOT: re-run this on the L40 before trusting them.")

    wanted = set(args.clips.split(",")) if args.clips else None
    pairs = discover(args.sample_dir, wanted)
    if not pairs:
        raise SystemExit("No clips found -- expected files named NN-*.<ext>")
    print(f"{len(pairs)} clips from {args.sample_dir}, "
          f"language={args.language or 'auto'}, device={device}")

    results = [run_engine(k.strip(), pairs, device, args.language, args.vad)
               for k in args.engines.split(",") if k.strip()]

    print("\n" + "=" * 72)
    print(f"{'engine':<14}{'mean WER':>10}{'mean RTFx':>11}{'load s':>9}"
          f"{'RSS MB':>9}{'VRAM MB':>9}  silence")
    for r in results:
        if r.error:
            print(f"{r.engine:<14}  skipped ({r.error.split(chr(10))[0][:40]})")
            continue
        scored = [c for c in r.clips if c.wer is not None]
        mw = sum(c.wer for c in scored) / len(scored) if scored else float("nan")
        mr = sum(c.rtf for c in r.clips) / len(r.clips) if r.clips else float("nan")
        sil = next((c for c in r.clips if c.kind == "silence"), None)
        sil_txt = "-" if sil is None else classify_silence(sil.text)
        print(f"{r.engine:<14}{mw:>9.1%}{mr:>11.1f}{r.load_sec:>9.1f}"
              f"{r.peak_rss_mb or float('nan'):>9.0f}{r.vram_mb or float('nan'):>9.0f}"
              f"  {sil_txt}")

    if args.out:
        args.out.write_text(json.dumps([asdict(r) for r in results], indent=2,
                                       ensure_ascii=False), encoding="utf-8")
        print(f"\nRaw report -> {args.out}")


if __name__ == "__main__":
    main()
