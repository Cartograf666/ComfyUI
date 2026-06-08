import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import utils
import utils.install_util
from comfy.cli_args import args

args.cpu = True

import custom_nodes.comfyui_story_i2v as story
from comfy_api_nodes.nodes_scene_parser import _processor_cache_fingerprint


def _script_json():
    return json.dumps(
        {
            "title_ru": "Тест",
            "character_bible_ru": "Робот А: серебристый корпус",
            "scenes": [
                {
                    "scene_index": 1,
                    "title_ru": "Старт",
                    "visual_description_ru": "Робот у двери",
                    "action_ru": "Робот открывает дверь",
                    "camera_ru": "медленный наезд",
                    "narration_ru": "Робот услышал сигнал за дверью.",
                }
            ],
        },
        ensure_ascii=False,
    )


def test_story_prompt_pack_injects_bible_negative_and_model_hints(monkeypatch, tmp_path):
    monkeypatch.setattr(story, "cache_dir", str(tmp_path))

    def fake_call(api_provider, api_key, model, system_instruction, user_prompt, max_tokens=16384):
        return json.dumps(
            {
                "global_style_prompt": "cinematic neon station",
                "character_bible_en": "Robot A has a silver body and blue eye light",
                "negative_prompt": "deformed limbs, warped face",
                "storyboard_grid_prompt": "8 panel storyboard sheet",
                "scenes": [
                    {
                        "scene_index": 1,
                        "image_prompt": "Robot A opens a metal door",
                        "video_prompt": "Robot A slowly opens the door, camera pushes in",
                    }
                ],
            }
        )

    monkeypatch.setattr(story, "_story_model_call", fake_call)

    out = story.StoryEnPromptPackNode().translate(
        approved_script_json=_script_json(),
        api_key="key",
        model="gemini-2.5-flash",
        api_provider="atlascloud",
        project_slug="unit_story",
        cache_mode="regenerate",
        video_model="kling-v2.0",
    )

    image_prompt = out[2]
    video_prompt = out[10]
    grid_prompt = out[1]

    assert "Character identity bible: Robot A has a silver body" in image_prompt
    assert "Avoid: deformed limbs, warped face" in image_prompt
    assert "Kling I2V instructions" in video_prompt
    assert "Avoid: deformed limbs, warped face" in grid_prompt


def test_story_narration_pack_extracts_ru_and_estimates_duration():
    out = story.StoryNarrationPackNode().extract(
        approved_script_json=_script_json(),
        min_scene_seconds=4,
        max_scene_seconds=8,
        padding_seconds=1.0,
        words_per_minute=120,
    )

    assert out[0] == "Робот услышал сигнал за дверью."
    assert 4 <= out[8] <= 8
    assert "1:" in out[-1]


def test_story_character_sheet_prompt_uses_style_bible_and_negative():
    pack = json.dumps({"global_style_prompt": "soft cinematic lighting"})
    prompt, fingerprint = story.StoryCharacterSheetPromptNode().build(
        prompt_pack_json=pack,
        character_bible_en="Robot A is silver; Robot B is matte black",
        negative_prompt="identity drift",
    )

    assert "character reference sheet" in prompt
    assert "Robot A is silver" in prompt
    assert "Global style: soft cinematic lighting" in prompt
    assert "Avoid: identity drift" in prompt
    assert fingerprint


def test_processor_cache_fingerprint_includes_settings_when_explicit_missing():
    fp = _processor_cache_fingerprint(
        "story_scene_video",
        "",
        prompt="move",
        model="kling-v2.0",
        duration=6,
    )

    assert "story_scene_video" in fp
    assert "kling-v2.0" in fp
    assert _processor_cache_fingerprint("story_scene_video", "explicit", model="x") == "explicit"
