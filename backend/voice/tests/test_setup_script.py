"""scripts/setup_stt_model.py must agree with the runtime on the repo id.

The script lives in backend/scripts/ (the backend's env) but the only env that
has faster-whisper installed is this one, so it is loaded by path rather than
imported as a package. No network: `_MODELS` is a dict literal in the installed
package.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_stt_model.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("setup_stt_model", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repo_id_matches_what_the_runtime_will_look_up(script):
    """The prefetch must fill the cache under the id WhisperModel reads.

    A hand-written "Systran/faster-whisper-{model}" template gets this wrong for
    the default model -- that repo does not exist (HF 401) -- and a cache keyed
    under it would not satisfy the runtime even if it did.
    """
    from faster_whisper.utils import _MODELS

    for name, expected in _MODELS.items():
        assert script.resolve_repo_id(name) == expected

    assert script.resolve_repo_id("large-v3-turbo").startswith("mobiuslabsgmbh/")


def test_explicit_repo_ids_pass_through(script):
    assert script.resolve_repo_id("owner/some-ct2-model") == "owner/some-ct2-model"


def test_unknown_model_name_exits_rather_than_inventing_a_url(script):
    with pytest.raises(SystemExit):
        script.resolve_repo_id("large-v3-turboo")


def test_manifest_lists_the_vocabulary_file_that_actually_exists(script):
    """faster-whisper's allow_patterns uses the glob `vocabulary.*`.

    snapshot_download ignores patterns matching nothing, so `vocabulary.txt`
    (the previous value) silently omitted the file and printed a dead link --
    Systran/faster-whisper-large-v3 ships only vocabulary.json.
    """
    assert "vocabulary.json" in script.MANIFEST_FILES
    assert "vocabulary.txt" not in script.MANIFEST_FILES


def test_manifest_files_are_covered_by_the_engines_own_allow_patterns(script):
    """Anything we tell an operator to copy must be something the engine reads."""
    import fnmatch

    # Mirrors faster_whisper.utils.download_model's allow_patterns.
    allow = ["config.json", "preprocessor_config.json", "model.bin",
             "tokenizer.json", "vocabulary.*"]
    for f in script.MANIFEST_FILES:
        assert any(fnmatch.fnmatch(f, pat) for pat in allow), f
