import json
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_PATH = os.path.join(ROOT, "user", "default", "workflows", "story_i2v_pipeline.json")


workflow = {
    "id": "story_i2v_pipeline_v5_combo_processors",
    "revision": 5,
    "last_node_id": 0,
    "last_link_id": 0,
    "nodes": [],
    "links": [],
    "groups": [],
    "config": {},
    "extra": {},
    "version": 0.4,
}

nodes = workflow["nodes"]
links = workflow["links"]
link_counter = 1


def node(node_id, node_type, title, pos, size, inputs, outputs, widgets_values=None, color=None, bgcolor=None):
    item = {
        "id": node_id,
        "type": node_type,
        "pos": pos,
        "size": size,
        "flags": {},
        "order": len(nodes) + 1,
        "mode": 0,
        "inputs": inputs,
        "outputs": outputs,
        "title": title,
        "properties": {},
        "widgets_values": widgets_values or [],
    }
    if color:
        item["color"] = color
    if bgcolor:
        item["bgcolor"] = bgcolor
    nodes.append(item)
    workflow["last_node_id"] = max(workflow["last_node_id"], node_id)
    return item


def inp(name, typ, widget=None, link=None):
    data = {"name": name, "type": typ, "link": link}
    if widget:
        data["widget"] = {"name": widget}
    return data


def out(name, typ):
    return {"name": name, "type": typ, "links": [], "slot_index": 0}


def connect(src, src_slot, dst, dst_slot, typ):
    global link_counter
    link_id = link_counter
    link_counter += 1
    src["outputs"][src_slot].setdefault("links", []).append(link_id)
    dst["inputs"][dst_slot]["link"] = link_id
    links.append([link_id, src["id"], src_slot, dst["id"], dst_slot, typ])
    workflow["last_link_id"] = link_id
    return link_id


# ---------------------------------------------------------------------------
# Central project/provider/settings
# ---------------------------------------------------------------------------
global_api = node(
    1,
    "GlobalAPIConfigNode",
    "0. Global API Provider / Key",
    [100, -1600],
    [340, 120],
    [
        inp("api_provider", "COMBO", "api_provider"),
        inp("api_key", "STRING", "api_key"),
    ],
    [out("api_provider", "COMBO"), out("api_key", "STRING")],
    ["atlascloud", ""],
    "#232",
    "#353",
)

project = node(
    2,
    "StoryProjectConfig8Node",
    "0. Story Project Config",
    [500, -1600],
    [360, 170],
    [
        inp("project_slug", "STRING", "project_slug"),
        inp("cache_mode", "COMBO", "cache_mode"),
    ],
    [
        out("project_slug", "STRING"),
        out("cache_mode", "COMBO"),
        out("ru_script_cache", "STRING"),
        out("en_pack_cache", "STRING"),
        out("grid_cache", "STRING"),
        *[out(f"scene_{i}_image_cache", "STRING") for i in range(1, 9)],
        *[out(f"scene_{i}_video_cache", "STRING") for i in range(1, 9)],
    ],
    ["story_i2v_phase0", "auto"],
    "#232",
    "#353",
)

video_settings = node(
    3,
    "StoryVideoSettingsNode",
    "0. Story Video Settings",
    [900, -1600],
    [420, 220],
    [
        inp("api_provider", "COMBO", "api_provider"),
        inp("api_key", "STRING", "api_key"),
        inp("video_model", "STRING", "video_model"),
        inp("output_size", "COMBO", "output_size"),
        inp("duration", "INT", "duration"),
    ],
    [
        out("api_provider", "COMBO"),
        out("api_key", "STRING"),
        out("video_model", "STRING"),
        out("video_resolution", "COMBO"),
        out("aspect_ratio", "COMBO"),
        out("duration", "INT"),
        out("status", "STRING"),
    ],
    ["kling-v2.0", "1080x1920 vertical", 6],
    "#232",
    "#353",
)

image_settings = node(
    4,
    "StoryImageSettingsNode",
    "0. Story Image Settings",
    [1360, -1600],
    [480, 300],
    [
        inp("api_provider", "COMBO", "api_provider"),
        inp("api_key", "STRING", "api_key"),
        inp("text_to_image_model", "STRING", "text_to_image_model"),
        inp("image_edit_model", "STRING", "image_edit_model"),
        inp("output_size", "COMBO", "output_size"),
        inp("grid_quality", "COMBO", "grid_quality"),
        inp("scene_quality", "COMBO", "scene_quality"),
        inp("image_prompt_strength", "FLOAT", "image_prompt_strength"),
    ],
    [
        out("api_provider", "COMBO"),
        out("api_key", "STRING"),
        out("text_to_image_model", "STRING"),
        out("image_edit_model", "STRING"),
        out("resolution", "COMBO"),
        out("aspect_ratio", "COMBO"),
        out("grid_quality", "COMBO"),
        out("scene_quality", "COMBO"),
        out("image_prompt_strength", "FLOAT"),
        out("status", "STRING"),
    ],
    [
        "black-forest-labs/flux-1-dev",
        "black-forest-labs/flux-1-dev",
        "2K 9:16 1440x2560",
        "medium",
        "high",
        0.3,
    ],
    "#232",
    "#353",
)

connect(global_api, 0, video_settings, 0, "COMBO")
connect(global_api, 1, video_settings, 1, "STRING")
connect(global_api, 0, image_settings, 0, "COMBO")
connect(global_api, 1, image_settings, 1, "STRING")


# ---------------------------------------------------------------------------
# Script, approval, EN production prompts, narration timing
# ---------------------------------------------------------------------------
ru_script = node(
    10,
    "StoryRuScriptGeneratorNode",
    "1. RU Story Script Generator",
    [-1600, -900],
    [520, 420],
    [
        inp("idea", "STRING", "idea"),
        inp("style_description", "STRING", "style_description"),
        inp("story_direction", "STRING", "story_direction"),
        inp("scene_count", "INT", "scene_count"),
        inp("api_key", "STRING", "api_key"),
        inp("model", "STRING", "model"),
        inp("api_provider", "COMBO", "api_provider"),
        inp("project_slug", "STRING", "project_slug"),
        inp("cache_mode", "COMBO", "cache_mode"),
    ],
    [out("script_json", "STRING"), out("script_text_ru", "STRING"), out("fingerprint", "STRING")],
    [
        "Космическое приключение двух роботов-исследователей на заброшенной станции",
        "cinematic 3D render, retro-futurism, glowing neon lights, highly detailed",
        "Начать с загадки, усилить напряжение в середине, закончить эмоциональным открытием.",
        8,
        "gemini-2.5-flash",
    ],
)

approval = node(
    11,
    "StoryScriptApprovalNode",
    "1a. Approve / Edit RU Script",
    [-1030, -900],
    [480, 320],
    [
        inp("generated_script_json", "STRING", "generated_script_json"),
        inp("manual_script_json", "STRING", "manual_script_json"),
        inp("approved", "BOOLEAN", "approved"),
    ],
    [
        out("approved_script_json", "STRING"),
        out("approved_script_text_ru", "STRING"),
        out("fingerprint", "STRING"),
    ],
    ["", False],
    "#432",
    "#653",
)

prompt_pack = node(
    12,
    "StoryEnPromptPackNode",
    "2. EN Production Prompt Pack",
    [-500, -900],
    [520, 430],
    [
        inp("approved_script_json", "STRING", "approved_script_json"),
        inp("api_key", "STRING", "api_key"),
        inp("model", "STRING", "model"),
        inp("api_provider", "COMBO", "api_provider"),
        inp("project_slug", "STRING", "project_slug"),
        inp("cache_mode", "COMBO", "cache_mode"),
        inp("video_model", "STRING", "video_model"),
    ],
    [
        out("prompt_pack_json", "STRING"),
        out("storyboard_grid_prompt", "STRING"),
        *[out(f"scene_{i}_image_prompt", "STRING") for i in range(1, 9)],
        *[out(f"scene_{i}_video_prompt", "STRING") for i in range(1, 9)],
        out("character_bible_en", "STRING"),
        out("negative_prompt", "STRING"),
        out("fingerprint", "STRING"),
    ],
    ["gemini-2.5-flash"],
)

narration = node(
    13,
    "StoryNarrationPackNode",
    "2a. RU Narration + Timing",
    [80, -900],
    [460, 320],
    [
        inp("approved_script_json", "STRING", "approved_script_json"),
        inp("min_scene_seconds", "INT", "min_scene_seconds"),
        inp("max_scene_seconds", "INT", "max_scene_seconds"),
        inp("padding_seconds", "FLOAT", "padding_seconds"),
        inp("words_per_minute", "INT", "words_per_minute"),
    ],
    [
        *[out(f"narration_{i}", "STRING") for i in range(1, 9)],
        *[out(f"duration_{i}", "INT") for i in range(1, 9)],
        out("fingerprint", "STRING"),
        out("status", "STRING"),
    ],
    [4, 8, 1.0, 145],
)

connect(global_api, 1, ru_script, 4, "STRING")
connect(global_api, 0, ru_script, 6, "COMBO")
connect(project, 0, ru_script, 7, "STRING")
connect(project, 1, ru_script, 8, "COMBO")
connect(ru_script, 0, approval, 0, "STRING")
connect(approval, 0, prompt_pack, 0, "STRING")
connect(global_api, 1, prompt_pack, 1, "STRING")
connect(global_api, 0, prompt_pack, 3, "COMBO")
connect(project, 0, prompt_pack, 4, "STRING")
connect(project, 1, prompt_pack, 5, "COMBO")
connect(video_settings, 2, prompt_pack, 6, "STRING")
connect(approval, 0, narration, 0, "STRING")


# ---------------------------------------------------------------------------
# Storyboard grid and character sheet
# ---------------------------------------------------------------------------
grid = node(
    20,
    "StoryboardGridProcessorNode",
    "3. Storyboard Grid Processor",
    [-1500, -250],
    [520, 430],
    [
        inp("prompt", "STRING", "prompt"),
        inp("api_provider", "COMBO", "api_provider"),
        inp("model", "COMBO", "model"),
        inp("resolution", "COMBO", "resolution"),
        inp("aspect_ratio", "COMBO", "aspect_ratio"),
        inp("quality", "COMBO", "quality"),
        inp("seed", "INT", "seed"),
        inp("image_prompt_strength", "FLOAT", "image_prompt_strength"),
        inp("base_scene_image", "IMAGE"),
        inp("reference_image", "IMAGE"),
        inp("api_key", "STRING"),
        inp("cache_name", "STRING", "cache_name"),
        inp("mode", "COMBO", "mode"),
        inp("global_mode", "COMBO", "global_mode"),
        inp("cache_fingerprint", "STRING"),
        inp("approved", "BOOLEAN", "approved"),
    ],
    [out("grid_image", "IMAGE"), *[out(f"image_{i}", "IMAGE") for i in range(1, 9)]],
    [101, "global", True],
)

char_prompt = node(
    21,
    "StoryCharacterSheetPromptNode",
    "3a. Character Sheet Prompt",
    [-920, -250],
    [420, 220],
    [
        inp("prompt_pack_json", "STRING", "prompt_pack_json"),
        inp("character_bible_en", "STRING", "character_bible_en"),
        inp("negative_prompt", "STRING", "negative_prompt"),
    ],
    [out("character_sheet_prompt", "STRING"), out("fingerprint", "STRING")],
)

char_sheet = node(
    22,
    "ViralSceneImageProcessorNode",
    "3b. Character Sheet Image",
    [-460, -250],
    [520, 430],
    [
        inp("prompt", "STRING", "prompt"),
        inp("api_provider", "COMBO", "api_provider"),
        inp("model", "COMBO", "model"),
        inp("resolution", "COMBO", "resolution"),
        inp("aspect_ratio", "COMBO", "aspect_ratio"),
        inp("quality", "COMBO", "quality"),
        inp("seed", "INT", "seed"),
        inp("image_prompt_strength", "FLOAT", "image_prompt_strength"),
        inp("base_scene_image", "IMAGE"),
        inp("reference_image", "IMAGE"),
        inp("api_key", "STRING"),
        inp("cache_name", "STRING", "cache_name"),
        inp("mode", "COMBO", "mode"),
        inp("global_mode", "COMBO", "global_mode"),
        inp("cache_fingerprint", "STRING"),
        inp("approved", "BOOLEAN", "approved"),
    ],
    [out("image", "IMAGE")],
    [777, 0.0, "story_i2v_phase0.character_sheet", "global", True],
)

connect(prompt_pack, 1, grid, 0, "STRING")
connect(image_settings, 0, grid, 1, "COMBO")
connect(image_settings, 2, grid, 2, "COMBO")
connect(image_settings, 4, grid, 3, "COMBO")
connect(image_settings, 5, grid, 4, "COMBO")
connect(image_settings, 6, grid, 5, "COMBO")
connect(image_settings, 8, grid, 7, "FLOAT")
connect(image_settings, 1, grid, 10, "STRING")
connect(project, 4, grid, 11, "STRING")
connect(project, 1, grid, 13, "COMBO")

connect(prompt_pack, 0, char_prompt, 0, "STRING")
connect(prompt_pack, 18, char_prompt, 1, "STRING")
connect(prompt_pack, 19, char_prompt, 2, "STRING")
connect(char_prompt, 0, char_sheet, 0, "STRING")
connect(image_settings, 0, char_sheet, 1, "COMBO")
connect(image_settings, 2, char_sheet, 2, "COMBO")
connect(image_settings, 4, char_sheet, 3, "COMBO")
connect(image_settings, 5, char_sheet, 4, "COMBO")
connect(image_settings, 7, char_sheet, 5, "COMBO")
connect(image_settings, 1, char_sheet, 10, "STRING")
connect(project, 1, char_sheet, 13, "COMBO")
connect(char_prompt, 1, char_sheet, 14, "STRING")


# ---------------------------------------------------------------------------
# Eight scenes: panel + character sheet -> high-res scene image -> FLF video.
# ---------------------------------------------------------------------------
scene_images = []
image_gates = []
scene_videos = []
voiceovers = []
video_gates = []

for i in range(1, 9):
    x = -1500 + (i - 1) * 430
    img = node(
        30 + i,
        "ViralSceneImageProcessorNode",
        f"4.{i} Scene {i} Image",
        [x, 320],
        [400, 420],
        [
            inp("prompt", "STRING", "prompt"),
            inp("api_provider", "COMBO", "api_provider"),
            inp("model", "COMBO", "model"),
            inp("resolution", "COMBO", "resolution"),
            inp("aspect_ratio", "COMBO", "aspect_ratio"),
            inp("quality", "COMBO", "quality"),
            inp("seed", "INT", "seed"),
            inp("image_prompt_strength", "FLOAT", "image_prompt_strength"),
            inp("base_scene_image", "IMAGE"),
            inp("reference_image", "IMAGE"),
            inp("api_key", "STRING"),
            inp("cache_name", "STRING", "cache_name"),
            inp("mode", "COMBO", "mode"),
            inp("global_mode", "COMBO", "global_mode"),
            inp("cache_fingerprint", "STRING"),
            inp("approved", "BOOLEAN", "approved"),
        ],
        [out("image", "IMAGE")],
        [1000 + i, "global", True],
    )
    gate = node(
        50 + i,
        "ImageGateNode",
        f"4.{i} Approve Scene {i} Image",
        [x, 780],
        [320, 130],
        [inp("image", "IMAGE"), inp("approved", "BOOLEAN", "approved")],
        [out("image", "IMAGE")],
        [True],
    )
    scene_images.append(img)
    image_gates.append(gate)

    connect(prompt_pack, 1 + i, img, 0, "STRING")
    connect(image_settings, 0, img, 1, "COMBO")
    connect(image_settings, 3, img, 2, "COMBO")
    connect(image_settings, 4, img, 3, "COMBO")
    connect(image_settings, 5, img, 4, "COMBO")
    connect(image_settings, 7, img, 5, "COMBO")
    connect(image_settings, 8, img, 7, "FLOAT")
    connect(grid, i, img, 8, "IMAGE")
    connect(char_sheet, 0, img, 9, "IMAGE")
    connect(image_settings, 1, img, 10, "STRING")
    connect(project, 4 + i, img, 11, "STRING")
    connect(project, 1, img, 13, "COMBO")
    connect(img, 0, gate, 0, "IMAGE")

for i in range(1, 9):
    x = -1500 + (i - 1) * 430
    vid = node(
        70 + i,
        "StorySceneVideoProcessorNode",
        f"5.{i} Scene {i} Video I2V",
        [x, 980],
        [400, 440],
        [
            inp("prompt", "STRING", "prompt"),
            inp("api_provider", "COMBO", "api_provider"),
            inp("model", "STRING", "model"),
            inp("resolution", "COMBO", "resolution"),
            inp("aspect_ratio", "COMBO", "aspect_ratio"),
            inp("duration", "INT", "duration"),
            inp("generate_audio", "BOOLEAN", "generate_audio"),
            inp("auto_upscale", "BOOLEAN", "auto_upscale"),
            inp("seed", "INT", "seed"),
            inp("image_1", "IMAGE"),
            inp("image_2", "IMAGE"),
            inp("api_key", "STRING"),
            inp("cache_name", "STRING", "cache_name"),
            inp("mode", "COMBO", "mode"),
            inp("global_mode", "COMBO", "global_mode"),
            inp("cache_fingerprint", "STRING"),
            inp("approved", "BOOLEAN", "approved"),
        ],
        [out("video", "VIDEO")],
        [False, False, 2000 + i, "global", True],
    )
    audio = node(
        90 + i,
        "ViralSceneAudioProcessorNode",
        f"5.{i} Scene {i} Narration TTS",
        [x, 1460],
        [400, 420],
        [
            inp("text", "STRING", "text"),
            inp("engine", "COMBO", "engine"),
            inp("voice", "COMBO", "voice"),
            inp("model", "COMBO", "model"),
            inp("stability", "FLOAT", "stability"),
            inp("similarity_boost", "FLOAT", "similarity_boost"),
            inp("style", "FLOAT", "style"),
            inp("speed", "FLOAT", "speed"),
            inp("language_code", "STRING", "language_code"),
            inp("custom_voice_id", "STRING", "custom_voice_id"),
            inp("output_format", "COMBO", "output_format"),
            inp("api_key", "STRING", "api_key"),
            inp("cache_name", "STRING", "cache_name"),
            inp("mode", "COMBO", "mode"),
            inp("global_mode", "COMBO", "global_mode"),
            inp("cache_fingerprint", "STRING"),
            inp("enabled", "BOOLEAN", "enabled"),
        ],
        [out("audio", "AUDIO")],
        ["EdgeTTS", "ru-RU-DmitryNeural", "eleven_multilingual_v2", 0.5, 0.75, 0.0, 1.0, "ru", "", "mp3_44100_192", f"story_i2v_phase0.scene_{i:02d}_voice", "global", True],
    )
    gate = node(
        110 + i,
        "VideoGateNode",
        f"5.{i} Approve Scene {i} Video",
        [x, 1900],
        [320, 130],
        [inp("video", "VIDEO"), inp("approved", "BOOLEAN", "approved")],
        [out("video", "VIDEO")],
        [True],
    )
    scene_videos.append(vid)
    voiceovers.append(audio)
    video_gates.append(gate)

    connect(prompt_pack, 9 + i, vid, 0, "STRING")
    connect(video_settings, 0, vid, 1, "COMBO")
    connect(video_settings, 2, vid, 2, "STRING")
    connect(video_settings, 3, vid, 3, "COMBO")
    connect(video_settings, 4, vid, 4, "COMBO")
    connect(narration, 7 + i, vid, 5, "INT")
    connect(image_gates[i - 1], 0, vid, 9, "IMAGE")
    if i < 8:
        connect(image_gates[i], 0, vid, 10, "IMAGE")
    connect(video_settings, 1, vid, 11, "STRING")
    connect(project, 12 + i, vid, 12, "STRING")
    connect(project, 1, vid, 14, "COMBO")
    connect(vid, 0, gate, 0, "VIDEO")

    connect(narration, i - 1, audio, 0, "STRING")
    connect(global_api, 1, audio, 11, "STRING")
    connect(project, 1, audio, 14, "COMBO")
    connect(narration, 16, audio, 15, "STRING")


# ---------------------------------------------------------------------------
# Final concat + save
# ---------------------------------------------------------------------------
concat = node(
    130,
    "VideoConcat8FFmpegNode",
    "6. Final Video Concat + Narration Mix",
    [100, 2220],
    [620, 560],
    [
        *[inp(f"video_{i}", "VIDEO") for i in range(1, 9)],
        *[inp(f"audio_{i}", "AUDIO") for i in range(1, 9)],
        inp("background_music", "AUDIO"),
        inp("background_music_volume", "FLOAT", "background_music_volume"),
        inp("filename_prefix", "STRING", "filename_prefix"),
        inp("mode", "COMBO", "mode"),
        inp("subtitles", "COMBO", "subtitles"),
        *[inp(f"subtitle_text_{i}", "STRING") for i in range(1, 9)],
        inp("subtitle_style", "STRING", "subtitle_style"),
    ],
    [out("video", "VIDEO")],
    [
        0.08,
        "story_i2v_final",
        "stream copy (fast)",
        "burned in only",
        "", "", "", "", "", "", "", "",
        "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=40",
    ],
)

save = node(
    131,
    "SaveVideo",
    "7. Save Final Video",
    [800, 2220],
    [360, 160],
    [inp("video", "VIDEO")],
    [],
    ["story_i2v_final_output", "auto", "auto"],
)

for i in range(1, 9):
    connect(video_gates[i - 1], 0, concat, i - 1, "VIDEO")
    connect(voiceovers[i - 1], 0, concat, 7 + i, "AUDIO")
    connect(narration, i - 1, concat, 20 + i, "STRING")
connect(concat, 0, save, 0, "VIDEO")


os.makedirs(os.path.dirname(TARGET_PATH), exist_ok=True)
with open(TARGET_PATH, "w", encoding="utf-8") as f:
    json.dump(workflow, f, ensure_ascii=False, indent=2)

print(f"Generated Story I2V canonical combo workflow at: {TARGET_PATH}")
print(f"Nodes: {len(nodes)}  Links: {len(links)}")
