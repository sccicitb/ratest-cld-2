"""The sidecar must stay answerable while the GPU is busy -- and only use it once.

Two properties that pull in opposite directions:

* The blocking engine call must not sit on the event loop, or /health is
  unanswerable for the whole transcription and liveness monitoring reads "down"
  during normal work.
* But a single WhisperModel is not safe for concurrent transcribe() calls, so
  moving the work off the loop must not let two run at once.

Both are asserted here, against the fake engine -- no weights involved.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from voice.tests.test_audio import _sine_wav


def test_health_answers_while_a_transcription_is_in_flight(client, fake):
    fake.gate = threading.Event()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.post,
            "/transcribe",
            files={"audio": ("clip.wav", _sine_wav(0.5), "audio/wav")},
        )
        assert fake.entered.wait(10), "transcription never started"

        # The engine is mid-call right now. If it were blocking the event loop,
        # this request could not even be dispatched.
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        fake.gate.set()
        assert pending.result(timeout=10).status_code == 200


def test_only_one_transcription_touches_the_engine_at_a_time(client, fake):
    """asyncio.to_thread alone would let N run concurrently; the semaphore is
    what keeps the not-thread-safe model correct."""
    fake.gate = threading.Event()

    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = [
            pool.submit(
                client.post,
                "/transcribe",
                files={"audio": (f"c{i}.wav", _sine_wav(0.3), "audio/wav")},
            )
            for i in range(3)
        ]
        assert fake.entered.wait(10), "first transcription never started"
        # Give any wrongly-parallel calls a chance to pile in before releasing.
        threading.Event().wait(0.3)
        assert fake.max_overlap == 1

        fake.gate.set()
        for f in pending:
            assert f.result(timeout=15).status_code == 200

    assert fake.max_overlap == 1, "engine was entered concurrently"
    assert len(fake.calls) == 3, "a queued request was dropped, not queued"
