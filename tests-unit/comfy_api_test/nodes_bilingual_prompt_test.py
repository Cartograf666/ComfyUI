import sys
import os
# Prepend project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import the root utils package first to cache it in sys.modules before nodes.py can shadow it
import utils
import utils.install_util

import pytest

import comfy_api_nodes.nodes_scene_parser as nsp
from comfy_api_nodes.nodes_scene_parser import EditableRuPromptNode


# ──────────────────────────────────────────────────────────────────────────────
# Helpers: keep the bilingual layer hermetic — no network, no disk.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def memo(monkeypatch):
    """Replace the disk-backed RU memo with an in-memory dict."""
    store: dict[str, str] = {}
    monkeypatch.setattr(nsp, "_ru_memo_get", lambda key: store.get(key))
    monkeypatch.setattr(nsp, "_ru_memo_put", lambda key, value: store.__setitem__(key, value))
    return store


def _stub_gemini(monkeypatch, fn):
    """Install an async stub for the one-shot Gemini call."""
    async def _fake(api_key, model, system_prompt, user_prompt, temperature=0.4, max_tokens=60000):
        return fn(system_prompt, user_prompt)
    monkeypatch.setattr(nsp, "_gemini_generate_text", _fake)


# ──────────────────────────────────────────────────────────────────────────────
# Translation helper: EN→RU display, cached so it never drifts.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_translate_to_ru_caches_and_strips(monkeypatch, memo):
    calls = []

    def gen(system, user):
        calls.append(user)
        return "**Стив идёт по полю.**"  # markdown that must be stripped

    _stub_gemini(monkeypatch, gen)

    first = await nsp._translate_prompt_to_ru("key", "m", "Steve walks on a field.", "image")
    assert first == "Стив идёт по полю."  # markdown stripped

    # Second call with the same English text must hit the memo, not the LLM.
    second = await nsp._translate_prompt_to_ru("key", "m", "Steve walks on a field.", "image")
    assert second == first
    assert len(calls) == 1, "translation should be cached by (kind, English text)"


@pytest.mark.asyncio
async def test_translate_to_ru_empty_input(monkeypatch, memo):
    _stub_gemini(monkeypatch, lambda s, u: pytest.fail("LLM must not be called for empty text"))
    assert await nsp._translate_prompt_to_ru("key", "m", "   ", "image") == ""


# ──────────────────────────────────────────────────────────────────────────────
# Re-render helper: RU edit → English, with the video structure guard.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rerender_no_edit_passthrough(monkeypatch, memo):
    _stub_gemini(monkeypatch, lambda s, u: pytest.fail("LLM must not be called without an edit"))
    out = await nsp._rerender_ru_to_en("key", "m", "original english", "", "image")
    assert out == "original english"


@pytest.mark.asyncio
async def test_rerender_image_uses_llm_output(monkeypatch, memo):
    _stub_gemini(monkeypatch, lambda s, u: "Steve in a red hat on a grass field.")
    out = await nsp._rerender_ru_to_en("key", "m", "Steve on a field.", "Стив в красной шляпе", "image")
    assert out == "Steve in a red hat on a grass field."


@pytest.mark.asyncio
async def test_rerender_video_keeps_segments(monkeypatch, memo):
    segmented = "START: Steve stands. ACTION: Steve waves. END: Steve smiles. CAMERA: static lock-off. ATMOSPHERE: sunny."
    _stub_gemini(monkeypatch, lambda s, u: segmented)
    out = await nsp._rerender_ru_to_en("key", "m", segmented, "Стив машет рукой", "video")
    # Segmented structure survives; node-level stabilization keeps the labels intact.
    for label in ("START:", "ACTION:", "END:", "CAMERA:", "ATMOSPHERE:"):
        assert label in out


@pytest.mark.asyncio
async def test_rerender_video_falls_back_on_broken_structure(monkeypatch, memo):
    original = "START: Steve stands. ACTION: Steve waves. END: Steve smiles. CAMERA: static lock-off. ATMOSPHERE: sunny."
    # A re-render that drops the segmented structure must never reach the video model.
    _stub_gemini(monkeypatch, lambda s, u: "Steve just waves around, no structure here.")
    out = await nsp._rerender_ru_to_en("key", "m", original, "Стив машет рукой", "video")
    assert out == original


# ──────────────────────────────────────────────────────────────────────────────
# Node behavior: passthrough vs. edit, fingerprint, and the UI signal.
# ──────────────────────────────────────────────────────────────────────────────
def _patch_node(monkeypatch, *, ru_auto="АВТО", rerender="EDITED-EN"):
    async def fake_translate(api_key, model, text, kind):
        return ru_auto

    async def fake_rerender(api_key, model, generated_en, ru_edit, kind):
        return rerender

    monkeypatch.setattr(nsp, "_translate_prompt_to_ru", fake_translate)
    monkeypatch.setattr(nsp, "_rerender_ru_to_en", fake_rerender)


@pytest.mark.asyncio
async def test_node_empty_input(monkeypatch):
    _patch_node(monkeypatch)
    out = await EditableRuPromptNode.execute(generated_en="", kind="image", api_key="key")
    assert out[0] == "" and out[1] == "" and out[2] == ""
    assert out.ui == {"ru_auto": [""], "edited": [False]}


@pytest.mark.asyncio
async def test_node_passthrough_when_not_edited(monkeypatch):
    _patch_node(monkeypatch, ru_auto="АВТО")
    # ru_text empty → not edited → English passes through, fingerprint == English.
    out = await EditableRuPromptNode.execute(
        generated_en="Steve on a field.", kind="image", api_key="key", ru_text="", ru_baseline="",
    )
    assert out[0] == "Steve on a field."
    assert out[1] == "Steve on a field."  # fingerprint == final English
    assert out.ui["edited"] == [False]
    assert out.ui["ru_auto"] == ["АВТО"]


@pytest.mark.asyncio
async def test_node_unedited_when_ru_matches_auto(monkeypatch):
    _patch_node(monkeypatch, ru_auto="АВТО")
    # User's Russian equals the auto-translation → still a passthrough, no edit.
    out = await EditableRuPromptNode.execute(
        generated_en="Steve on a field.", kind="image", api_key="key", ru_text="АВТО", ru_baseline="АВТО",
    )
    assert out[0] == "Steve on a field."
    assert out.ui["edited"] == [False]


@pytest.mark.asyncio
async def test_node_edit_triggers_rerender(monkeypatch):
    _patch_node(monkeypatch, ru_auto="АВТО", rerender="Steve in a red hat.")
    out = await EditableRuPromptNode.execute(
        generated_en="Steve on a field.",
        kind="image",
        api_key="key",
        ru_text="Стив в красной шляпе",  # differs from auto and baseline → an edit
        ru_baseline="АВТО",
    )
    assert out[0] == "Steve in a red hat."
    assert out[1] == "Steve in a red hat."  # fingerprint tracks the edited English
    assert out.ui["edited"] == [True]


@pytest.mark.asyncio
async def test_node_edit_without_api_key_keeps_english(monkeypatch):
    _patch_node(monkeypatch)
    out = await EditableRuPromptNode.execute(
        generated_en="Steve on a field.",
        kind="image",
        api_key="",  # no key → cannot re-render, must keep original English
        ru_text="Стив в красной шляпе",
        ru_baseline="something-old",
    )
    assert out[0] == "Steve on a field."
    assert "WARNING" in out[2]  # preview warns the edit was ignored
