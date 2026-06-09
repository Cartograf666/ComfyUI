import json
import os
import sys
import inspect

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import utils
import utils.install_util
from comfy.cli_args import args

args.cpu = True

import custom_nodes.comfyui_story_i2v as story
from comfy_api_nodes.nodes_poyo_ai import PoyoAIImageNode
from comfy_api_nodes.nodes_scene_parser import _processor_cache_fingerprint
from comfy_api_nodes.nodes_scene_parser import BuildVeoPromptNode
from comfy_api_nodes.nodes_scene_parser import StorySceneVideoProcessorNode
from comfy_api_nodes.nodes_scene_parser import StoryboardGridProcessorNode
from comfy_api_nodes.nodes_scene_parser import ViralSceneImageProcessorNode


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
    assert "Kling I2V preset" in video_prompt
    assert "Avoid: deformed limbs, warped face" in grid_prompt


def test_story_video_prompt_seedance_uses_named_preset():
    prompt = story._model_video_prompt(
        prompt="Robot crosses the corridor.",
        video_model="seedance-2",
        global_style="cinematic",
        character_bible="Robot A has a silver body",
        negative_prompt="identity drift",
    )

    assert "Seedance I2V preset" in prompt
    assert "START, ACTION, END, CAMERA" in prompt
    assert "Avoid: identity drift" in prompt


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


def _schema_inputs(node_cls):
    return {item.id: item for item in node_cls.define_schema().inputs}


def test_poyo_image_execute_signature_matches_schema_order():
    schema_names = [item.id for item in PoyoAIImageNode.define_schema().inputs]
    execute_names = [
        name
        for name in inspect.signature(PoyoAIImageNode.execute).parameters
        if name != "cls"
    ]

    assert execute_names == schema_names


def test_story_i2v_processor_defaults_are_practical_not_phase0_placeholders():
    scene_image = _schema_inputs(ViralSceneImageProcessorNode)
    grid = _schema_inputs(StoryboardGridProcessorNode)
    video = _schema_inputs(StorySceneVideoProcessorNode)

    assert scene_image["api_provider"].default == "atlascloud"
    assert scene_image["model"].default == "black-forest-labs/flux-1-dev"
    assert scene_image["image_prompt_strength"].default == 0.3
    assert grid["api_provider"].default == "atlascloud"
    assert grid["model"].default == "black-forest-labs/flux-1-dev"
    assert grid["image_prompt_strength"].default == 0.3
    assert video["api_provider"].default == "atlascloud"
    assert video["model"].default == "kling-v2.0"


def test_story_concat_supports_optional_background_music():
    inputs = {item.id: item for item in story.VideoConcat8FFmpegNode.define_schema().inputs}

    assert "background_music" in inputs
    assert inputs["background_music_volume"].default == 0.08


@pytest.mark.asyncio
async def test_build_veo_prompt_uses_stable_fallback_when_video_prompt_empty():
    out = await BuildVeoPromptNode.execute(
        scene_number=2,
        video_prompt="",
        signature_object_bible="Scene 2: flickers with a blue pulse.",
        setting_short="Inside a quiet neon hallway.",
        video_model="seedance-2",
    )

    prompt = out[0]
    assert "START:" in prompt
    assert "scene 2" in prompt
    assert "CAMERA:" in prompt
    assert "flickers with a blue pulse" in prompt
