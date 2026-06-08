import sys
import os
# Prepend project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import the root utils package first to cache it in sys.modules before nodes.py can shadow it
import utils
import utils.install_util
from comfy.cli_args import args
args.cpu = True

import asyncio
import pytest
from comfy_api_nodes.nodes_scene_parser import ImageCacheNode, SceneParserNode

MOCK_SCRIPT = """VISUAL STYLE: cubic voxel aesthetic
CHARACTER BIBLE: Steve: cube limbs
SETTING BIBLE: green grass plains
GOAL: build a house
OBSTACLE: zombies
STAKES: survival
STORY SPINE:
Scene 1 - Once upon a time...
Scene 2 - But one day...
SIGNATURE OBJECT: A block of wood

SCENE 1: The Beginning
Continuity: OPENING
Visual: Steve is walking on the grass field. He spots some wood.
Image Prompt: Steve walking on grass field holding a block of wood.
Video Prompt: START: Steve walks. ACTION: Steve picks up wood. END: Steve has wood. CAMERA: static. ATMOSPHERE: sunny.
Voiceover: I need wood.

---

SCENE 2: The Attack
Continuity: BECAUSE Steve is picking up wood, he doesn't see the zombie.
Visual: A zombie sneaks up behind Steve. Steve turns around in panic.
Image Prompt: Steve holding a block of wood looking surprised.
Video Prompt: START: Steve holds wood. ACTION: Steve turns around quickly. END: Steve faces zombie. CAMERA: slow zoom. ATMOSPHERE: dark.
Voiceover: Oh no, a zombie!
"""

async def run_test():
    output = await SceneParserNode.execute(MOCK_SCRIPT)

    image_prompt_1 = output[0]
    video_prompt_1 = output[1]
    voiceover_1 = output[2]

    image_prompt_2 = output[3]
    video_prompt_2 = output[4]
    voiceover_2 = output[5]

    # Assert Scene 1 has no previous context injected
    assert "previous scene context" not in image_prompt_1
    assert "following the previous scene" not in video_prompt_1
    assert voiceover_1 == "I need wood."

    # Assert Scene 2 has previous context injected (from Scene 1 Visual)
    assert "previous scene context: Steve is walking on the grass field. He spots some wood" in image_prompt_2
    assert "START: following the previous scene where Steve is walking on the grass field. He spots some wood, Steve holds wood" in video_prompt_2
    assert voiceover_2 == "Oh no, a zombie!"
    print("All test assertions passed successfully!")

@pytest.mark.asyncio
async def test_scene_parser_node_injects_previous_scene_context():
    await run_test()


@pytest.mark.asyncio
async def test_image_cache_skips_empty_character_slot_without_upstream(monkeypatch, tmp_path):
    cache_file = tmp_path / "character_1.png"
    monkeypatch.setattr(ImageCacheNode, "_cache_path", classmethod(lambda cls, cache_name: str(cache_file)))

    assert ImageCacheNode.check_lazy_status("character_1", "auto", cache_fingerprint="") == []

    output = await ImageCacheNode.execute("character_1", "auto", cache_fingerprint="", image=None)
    placeholder = output[0]

    assert placeholder.shape == (1, 64, 64, 3)
    assert placeholder.min().item() == pytest.approx(1.0)
    assert placeholder.max().item() == pytest.approx(1.0)


def test_image_cache_recognizes_legacy_empty_character_ref_name():
    assert ImageCacheNode._is_empty_character_ref("character_1_ref", "") is True

if __name__ == "__main__":
    asyncio.run(run_test())
