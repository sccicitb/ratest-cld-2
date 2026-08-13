#!/usr/bin/env python
"""A/B candidate TTS engines on Indonesian text (voice mode §1b).

The STT counterpart (check_stt_pipeline.py) could score itself: we had a read
script, so WER against it settled the engine choice. TTS has no such luxury --
there is no ground truth for "does this sound like a person". So this probe
does two things and refuses to conflate them:

  1. SYNTHESIZE  every text with every engine, into wavs you can listen to.
     Naturalness is decided by your ear. Nothing here scores it.
  2. ROUND-TRIP  each wav back through faster-whisper and score WER/CER
     against the source text. This is NOT a quality score -- it is an
     INTELLIGIBILITY score, and it exists to catch the failure your ear
     forgives: an engine that sounds lovely while reading "Rp 2,3 miliar" as
     "dua tiga miliar", or turning "BPS" into "beps".

A city assistant reads back retrieval answers, so the corpus is the four
places that actually break: numbers/currency/dates/acronyms, long-form
answers (does prosody survive past two sentences?), Indonesian prose with
English technical loanwords, and one-line confirmations (do short utterances
get clipped?).

Digits are spelled out on both sides before scoring -- reusing the STT
probe's Indonesian number speller -- so "2026" vs "dua ribu dua puluh enam"
does not register as an error. Raw transcripts are always printed so you can
judge acronyms and currency yourself.

Usage (two phases -- these deps do NOT co-exist happily, and none of them
belong in the backend's env):

    # phase 1: synthesize. One engine per env.
    env -u VIRTUAL_ENV uv run --with piper-tts \
        python scripts/check_tts_pipeline.py --synth piper

    env -u VIRTUAL_ENV uv run --with supertonic \
        python scripts/check_tts_pipeline.py --synth supertonic

    # phase 2: score every wav produced so far, in one pass.
    env -u VIRTUAL_ENV uv run --with faster-whisper --with av \
        python scripts/check_tts_pipeline.py --score

Model provisioning (once, before phase 1):

    # Piper: writes id_ID-news_tts-medium.onnx + .onnx.json
    env -u VIRTUAL_ENV uv run --with piper-tts \
        python -m piper.download_voices id_ID-news_tts-medium

    # Supertonic: first TTS(auto_download=True) pulls ~415 MB from HuggingFace.
    #   Air-gapped hosts: copy that directory over and pass --supertonic-dir.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

# The STT probe owns the Indonesian normalizer and the edit-distance scorers.
# It has no heavy module-level imports, so this is safe in any of the envs above.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_stt_pipeline import (  # noqa: E402
    _UNITS,
    _spell_number_token,
    cer,
    normalize,
    wer,
)

import re  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "data" / "tts_samples"
DEFAULT_PIPER_MODEL = ROOT / "data" / "tts_models" / "id_ID-news_tts-medium.onnx"


# ---------------------------------------------------------------------------
# The corpus -- what Citya actually says out loud.
# ---------------------------------------------------------------------------

@dataclass
class Text:
    num: str
    category: str  # numbers | longform | codeswitch | short
    text: str


TEXTS: list[Text] = [
    # --- numbers, currency, dates, acronyms: where TTS fails loudest --------
    Text("01", "numbers",
         "Retribusi parkir menyumbang Rp 2,3 miliar pada kuartal ketiga 2026, "
         "naik 15 persen dibandingkan kuartal sebelumnya."),
    Text("02", "numbers",
         "Data BPS per 31 Desember 2025 mencatat 1.247 pelaku usaha mikro "
         "yang terdaftar di 14 kelurahan."),
    Text("03", "numbers",
         "Anggaran sebesar Rp 875.000.000 dialokasikan untuk perbaikan jalan "
         "di RT 03 RW 07 melalui sistem SIPD."),

    # --- long-form: does prosody survive past two sentences? ---------------
    Text("04", "longform",
         "Berdasarkan dokumen yang tersedia, laporan penjualan kuartal ketiga "
         "tahun 2026 mencatat kenaikan sebesar 15 persen dibandingkan kuartal "
         "sebelumnya. Kenaikan ini terutama didorong oleh sektor retribusi "
         "parkir dan izin usaha. Namun, realisasi belanja modal masih berada "
         "di bawah target yang ditetapkan. Dinas terkait menyarankan "
         "percepatan proses pengadaan pada triwulan berikutnya."),
    Text("05", "longform",
         "Prosedur pengajuan izin mendirikan bangunan dimulai dengan "
         "pendaftaran melalui sistem daring. Pemohon wajib melampirkan "
         "sertifikat tanah, gambar rencana bangunan, dan bukti pembayaran "
         "retribusi. Setelah berkas dinyatakan lengkap, petugas akan melakukan "
         "verifikasi lapangan dalam waktu paling lama tujuh hari kerja."),
    Text("06", "longform",
         "Layanan pengaduan masyarakat beroperasi setiap hari kerja mulai "
         "pukul delapan pagi hingga pukul empat sore. Pengaduan yang masuk di "
         "luar jam tersebut akan diproses pada hari kerja berikutnya. Setiap "
         "laporan memperoleh nomor tiket yang dapat digunakan untuk memantau "
         "status penanganan."),

    # --- code-switching: Indonesian prose, English technical terms ---------
    Text("07", "codeswitch",
         "Silakan upload dokumen ke dashboard, lalu server akan memprosesnya "
         "secara otomatis."),
    Text("08", "codeswitch",
         "Fitur export ke format spreadsheet sedang dalam tahap testing oleh "
         "tim developer."),
    Text("09", "codeswitch",
         "Klik tombol submit setelah mengisi form, kemudian tunggu notifikasi "
         "email yang masuk."),

    # --- short: clipped or rushed? matters for ②'s auto-read ---------------
    Text("10", "short", "Dokumen ditemukan."),
    Text("11", "short", "Maaf, tidak ada hasil yang cocok."),
    Text("12", "short", "Ada yang bisa saya bantu?"),
]


# ---------------------------------------------------------------------------
# TTS-side text normalization (the §1b front-end question)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d[\d.,]*")
# The scale word must be swallowed with the amount: "Rp 2,3 miliar" is
# "dua koma tiga MILIAR rupiah", not "dua koma tiga rupiah miliar". Written
# Indonesian puts the currency marker first and the scale after the digits,
# so a naive substitution straddles the scale word and says nonsense.
_RUPIAH_RE = re.compile(
    r"\bRp\.?\s*(\d[\d.,]*)(\s+(?:triliun|miliar|juta|ribu))?", re.IGNORECASE
)


def _spell_for_tts(tok: str) -> str:
    """Spell one numeric token, keeping any trailing sentence punctuation.

    Leading zeros are spoken digit by digit: "RT 03" is "RT nol tiga", not
    "RT tiga" -- the zero is part of how the address is said aloud.
    """
    trail = ""
    while tok and tok[-1] in ".,":
        trail = tok[-1] + trail
        tok = tok[:-1]
    if not tok:
        return trail
    if tok.isdigit() and tok.startswith("0"):
        return " ".join(_UNITS[int(d)] for d in tok) + trail
    return _spell_number_token(tok) + trail


def tts_normalize(text: str) -> str:
    """Expand digits and `Rp` for synthesis, PRESERVING punctuation and case.

    Deliberately not `check_stt_pipeline.normalize`, which lowercases and
    strips punctuation. That is correct for scoring and wrong for speaking:
    commas and full stops are where a TTS engine takes its breath, and
    flattening them to score better would destroy the thing we are judging.

    Scope is the tested hypothesis and nothing more -- numbers and currency.
    Administrative abbreviations (RT/RW) are left alone on purpose, so the
    rematch shows whether they were ever a number problem to begin with.
    """
    text = _RUPIAH_RE.sub(
        lambda m: f"{_spell_for_tts(m.group(1))}{m.group(2) or ''} rupiah", text
    )
    return _NUMBER_RE.sub(lambda m: _spell_for_tts(m.group(0)), text)


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def wav_duration(path: Path) -> float:
    with contextlib.closing(wave.open(str(path), "rb")) as wf:
        return wf.getnframes() / float(wf.getframerate())


class PiperEngine:
    """piper-tts. One .onnx per voice; the Indonesian voice is a separate file."""

    key = "piper"

    def __init__(self, model_path: Path) -> None:
        from piper import PiperVoice

        if not model_path.exists():
            raise SystemExit(
                f"Piper voice not found: {model_path}\n"
                f"Provision it with:\n"
                f"  env -u VIRTUAL_ENV uv run --with piper-tts \\\n"
                f"      python -m piper.download_voices id_ID-news_tts-medium\n"
                f"then move the .onnx and .onnx.json into {model_path.parent}"
            )
        t0 = time.perf_counter()
        self._voice = PiperVoice.load(str(model_path))
        self.load_seconds = time.perf_counter() - t0
        cfg = model_path.with_suffix(model_path.suffix + ".json")
        self.model_mb = round(
            (model_path.stat().st_size + (cfg.stat().st_size if cfg.exists() else 0))
            / 1e6, 1
        )
        self.detail = model_path.stem

    def synthesize(self, text: str, out_path: Path) -> None:
        with contextlib.closing(wave.open(str(out_path), "wb")) as wf:
            self._voice.synthesize_wav(text, wf)


class SupertonicEngine:
    """supertonic. One 99M model covers all 31 languages -- `lang` selects,
    and the voice style is independent of it (voices are not language-bound)."""

    key = "supertonic"

    def __init__(self, model_dir: Path | None, voice: str, steps: int, speed: float) -> None:
        from supertonic import TTS

        t0 = time.perf_counter()
        # An explicit directory is the air-gapped path; auto_download is the
        # convenience path and needs the network exactly once.
        self._tts = TTS(str(model_dir)) if model_dir else TTS(auto_download=True)
        self._style = self._tts.get_voice_style(voice_name=voice)
        self.load_seconds = time.perf_counter() - t0
        # Instance-level so a voice sweep writes one directory per voice
        # instead of ten runs overwriting each other.
        self.key = f"supertonic-{voice}"
        self._steps = steps
        self._speed = speed
        self.model_mb = _dir_size_mb(model_dir) if model_dir else None
        self.detail = f"voice={voice} steps={steps} speed={speed}"

    def synthesize(self, text: str, out_path: Path) -> None:
        wav, _duration = self._tts.synthesize(
            text=text,
            lang="id",
            voice_style=self._style,
            total_steps=self._steps,
            speed=self._speed,
        )
        self._tts.save_audio(wav, str(out_path))


def _dir_size_mb(path: Path | None) -> float | None:
    if path is None or not path.exists():
        return None
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / 1e6, 1)


# ---------------------------------------------------------------------------
# Phase 1 -- synthesize
# ---------------------------------------------------------------------------

@dataclass
class SynthResult:
    num: str
    category: str
    wav: str
    audio_seconds: float
    proc_seconds: float
    rtf: float          # proc/audio. Lower is faster. Supertonic publishes 0.200.
    x_realtime: float   # audio/proc. The same number the friendly way round.


def run_synth(engine, texts: list[Text], out_dir: Path, normalize_numbers: bool = False) -> list[SynthResult]:
    # A normalized run gets its own directory so it stands beside the raw run
    # rather than overwriting the evidence it is being compared against.
    engine_dir = out_dir / (f"{engine.key}-norm" if normalize_numbers else engine.key)
    engine_dir.mkdir(parents=True, exist_ok=True)
    results: list[SynthResult] = []

    for t in texts:
        out_path = engine_dir / f"{t.num}-{t.category}.wav"
        spoken = tts_normalize(t.text) if normalize_numbers else t.text
        t0 = time.perf_counter()
        engine.synthesize(spoken, out_path)
        proc = time.perf_counter() - t0
        audio = wav_duration(out_path)
        results.append(SynthResult(
            num=t.num, category=t.category, wav=str(out_path),
            audio_seconds=round(audio, 2), proc_seconds=round(proc, 3),
            rtf=round(proc / audio, 3) if audio else 0.0,
            x_realtime=round(audio / proc, 2) if proc else 0.0,
        ))
        print(f"  {t.num} {t.category:<10} {audio:5.2f}s audio in {proc:5.2f}s "
              f"(RTF {results[-1].rtf:.3f}, {results[-1].x_realtime:.1f}x)")

    # The source text travels with the wavs so --score needs no arguments and
    # cannot drift from what was actually spoken.
    (engine_dir / "_manifest.json").write_text(json.dumps({
        "engine": engine.key + ("-norm" if normalize_numbers else ""),
        "detail": engine.detail + (" +number-normalized" if normalize_numbers else ""),
        "load_seconds": round(engine.load_seconds, 2),
        "model_mb": engine.model_mb,
        # `text` stays the ORIGINAL -- it is what the user asked to hear, so it
        # is the scoring reference. `spoken` records what the engine was
        # actually fed, so a normalized run can be audited rather than trusted.
        "texts": {
            t.num: {
                "category": t.category,
                "text": t.text,
                "spoken": tts_normalize(t.text) if normalize_numbers else t.text,
            }
            for t in texts
        },
    }, ensure_ascii=False, indent=2))
    return results


# ---------------------------------------------------------------------------
# Phase 2 -- round-trip through STT for an intelligibility score
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    num: str
    category: str
    reference: str
    transcript: str
    wer: float
    cer: float


@dataclass
class EngineScore:
    engine: str
    detail: str
    clips: list[ScoreResult] = field(default_factory=list)
    mean_wer: float = 0.0
    mean_cer: float = 0.0


def run_score(out_dir: Path, stt_model: str) -> list[EngineScore]:
    from faster_whisper import WhisperModel

    print(f"Loading scorer: faster-whisper {stt_model} (cpu/int8)")
    model = WhisperModel(stt_model, device="cpu", compute_type="int8")

    scores: list[EngineScore] = []
    for manifest_path in sorted(out_dir.glob("*/_manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        engine_dir = manifest_path.parent
        es = EngineScore(engine=manifest["engine"], detail=manifest.get("detail", ""))
        print(f"\n=== {es.engine} ({es.detail}) ===")

        for num, meta in sorted(manifest["texts"].items()):
            wav = engine_dir / f"{num}-{meta['category']}.wav"
            if not wav.exists():
                continue
            segments, _info = model.transcribe(str(wav), language="id", vad_filter=True)
            hyp = "".join(s.text for s in segments).strip()
            ref = meta["text"]
            w = wer(normalize(ref), normalize(hyp))
            c = cer(normalize(ref), normalize(hyp))
            es.clips.append(ScoreResult(num, meta["category"], ref, hyp, w, c))
            print(f"  {num} {meta['category']:<10} WER {w*100:5.1f}%  CER {c*100:5.1f}%")
            print(f"       heard: {hyp[:110]}")

        if es.clips:
            es.mean_wer = round(sum(x.wer for x in es.clips) / len(es.clips), 4)
            es.mean_cer = round(sum(x.cer for x in es.clips) / len(es.clips), 4)
        scores.append(es)
    return scores


def print_summary(scores: list[EngineScore]) -> None:
    print("\n" + "=" * 72)
    print("INTELLIGIBILITY (round-trip WER -- NOT a naturalness score)")
    print("=" * 72)
    for es in scores:
        print(f"{es.engine:<14} mean WER {es.mean_wer*100:5.1f}%   "
              f"mean CER {es.mean_cer*100:5.1f}%   ({es.detail})")
    print("\nPer category:")
    cats = ["numbers", "longform", "codeswitch", "short"]
    header = "  " + "engine".ljust(14) + "".join(c.ljust(12) for c in cats)
    print(header)
    for es in scores:
        row = "  " + es.engine.ljust(14)
        for cat in cats:
            vals = [x.wer for x in es.clips if x.category == cat]
            row += (f"{sum(vals)/len(vals)*100:.1f}%".ljust(12) if vals else "-".ljust(12))
        print(row)
    print("\nNaturalness is not measured here. Listen to the wavs.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synth", choices=["piper", "supertonic"],
                    help="synthesize the corpus with this engine")
    ap.add_argument("--score", action="store_true",
                    help="round-trip every synthesized wav through faster-whisper")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--texts", help="comma-separated ids, e.g. 01,04,10")
    ap.add_argument("--categories", help="comma-separated: numbers,longform,codeswitch,short")
    ap.add_argument("--piper-model", type=Path, default=DEFAULT_PIPER_MODEL)
    ap.add_argument("--supertonic-dir", type=Path, default=None,
                    help="local Supertonic model dir (air-gapped); omit to auto-download")
    ap.add_argument("--voice", default="M1", help="Supertonic voice style (M1..M5, F1..F5)")
    ap.add_argument("--steps", type=int, default=8, help="Supertonic quality steps, 5-12")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--normalize-numbers", action="store_true",
                    help="expand digits and Rp before synthesis (writes to <engine>-norm/)")
    ap.add_argument("--stt-model", default="large-v3-turbo", help="round-trip scorer")
    ap.add_argument("--json", type=Path, help="write raw results here")
    args = ap.parse_args()

    if not args.synth and not args.score:
        ap.error("nothing to do: pass --synth <engine> and/or --score")

    texts = TEXTS
    if args.categories:
        wanted = {c.strip() for c in args.categories.split(",")}
        texts = [t for t in texts if t.category in wanted]
    if args.texts:
        wanted_ids = {n.strip() for n in args.texts.split(",")}
        texts = [t for t in texts if t.num in wanted_ids]
    if not texts:
        ap.error("no texts matched --texts/--categories")

    payload: dict = {}

    if args.synth == "piper":
        print(f"=== piper: {args.piper_model.name} ===")
        engine = PiperEngine(args.piper_model)
        print(f"loaded in {engine.load_seconds:.2f}s, model {engine.model_mb} MB")
        payload["synth"] = [asdict(r) for r in run_synth(engine, texts, args.out_dir, args.normalize_numbers)]
    elif args.synth == "supertonic":
        print(f"=== supertonic: voice={args.voice} steps={args.steps} ===")
        engine = SupertonicEngine(args.supertonic_dir, args.voice, args.steps, args.speed)
        print(f"loaded in {engine.load_seconds:.2f}s, model {engine.model_mb or '?'} MB")
        payload["synth"] = [asdict(r) for r in run_synth(engine, texts, args.out_dir, args.normalize_numbers)]

    if args.score:
        scores = run_score(args.out_dir, args.stt_model)
        if not scores:
            raise SystemExit(f"no synthesized wavs found under {args.out_dir} — run --synth first")
        print_summary(scores)
        payload["scores"] = [asdict(s) for s in scores]

    if args.json:
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
