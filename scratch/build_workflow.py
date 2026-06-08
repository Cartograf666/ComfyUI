import json
import os

# Build the interactive comfyui workflow JSON dict with centralized settings
workflow = {
    "id": "storyboard_i2v_pipeline_v4",
    "revision": 4,
    "last_node_id": 71,
    "last_link_id": 200,
    "nodes": [],
    "links": [],
    "groups": [],
    "config": {},
    "extra": {},
    "version": 0.4
}

nodes = []
links = []
link_counter = 1

# Node 48: API Key Primitive
api_key_primitive = {
    "id": 48,
    "type": "PrimitiveNode",
    "pos": [100, -1450],
    "size": [300, 100],
    "flags": {},
    "order": 1,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {
            "name": "STRING",
            "type": "STRING",
            "widget": {"name": "api_key"},
            "slot_index": 0,
            "links": []
        }
    ],
    "title": "CENTRAL API KEY",
    "properties": {"Run widget replace on values": False},
    "widgets_values": [""],
    "color": "#232",
    "bgcolor": "#353"
}
nodes.append(api_key_primitive)

# Node 72: API Provider Primitive (poyo / atlascloud)
provider_primitive = {
    "id": 72,
    "type": "PrimitiveNode",
    "pos": [100, -1300],
    "size": [300, 100],
    "flags": {},
    "order": 1,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {
            "name": "COMBO",
            "type": "COMBO",
            "widget": {"name": "api_provider"},
            "slot_index": 0,
            "links": []
        }
    ],
    "title": "SHARED API PROVIDER",
    "properties": {"Run widget replace on values": False},
    "widgets_values": ["poyo", "fixed", ""],
    "color": "#232",
    "bgcolor": "#353"
}
nodes.append(provider_primitive)

# Node 49: VIDEO_MODEL Primitive
model_primitive = {
    "id": 49,
    "type": "PrimitiveNode",
    "pos": [450, -1450],
    "size": [300, 100],
    "flags": {},
    "order": 2,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {
            "name": "COMBO",
            "type": "COMBO",
            "widget": {"name": "model"},
            "slot_index": 0,
            "links": []
        }
    ],
    "title": "SHARED VIDEO MODEL",
    "properties": {"Run widget replace on values": False},
    "widgets_values": ["veo3.1-lite-official", "fixed", ""],
    "color": "#232",
    "bgcolor": "#353"
}
nodes.append(model_primitive)

# Node 50: VIDEO_RESOLUTION Primitive
resolution_primitive = {
    "id": 50,
    "type": "PrimitiveNode",
    "pos": [800, -1450],
    "size": [300, 100],
    "flags": {},
    "order": 3,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {
            "name": "COMBO",
            "type": "COMBO",
            "widget": {"name": "resolution"},
            "slot_index": 0,
            "links": []
        }
    ],
    "title": "SHARED VIDEO RESOLUTION",
    "properties": {"Run widget replace on values": False},
    "widgets_values": ["720p", "fixed", ""],
    "color": "#232",
    "bgcolor": "#353"
}
nodes.append(resolution_primitive)

# Node 51: VIDEO_ASPECT_RATIO Primitive
aspect_primitive = {
    "id": 51,
    "type": "PrimitiveNode",
    "pos": [1150, -1450],
    "size": [300, 100],
    "flags": {},
    "order": 4,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {
            "name": "COMBO",
            "type": "COMBO",
            "widget": {"name": "aspect_ratio"},
            "slot_index": 0,
            "links": []
        }
    ],
    "title": "SHARED VIDEO ASPECT RATIO",
    "properties": {"Run widget replace on values": False},
    "widgets_values": ["9:16", "fixed", ""],
    "color": "#232",
    "bgcolor": "#353"
}
nodes.append(aspect_primitive)

# Node 52: VIDEO_DURATION Primitive
duration_primitive = {
    "id": 52,
    "type": "PrimitiveNode",
    "pos": [1500, -1450],
    "size": [300, 100],
    "flags": {},
    "order": 5,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {
            "name": "INT",
            "type": "INT",
            "widget": {"name": "duration"},
            "slot_index": 0,
            "links": []
        }
    ],
    "title": "SHARED VIDEO DURATION",
    "properties": {"Run widget replace on values": False},
    "widgets_values": [6, "fixed"],
    "color": "#232",
    "bgcolor": "#353"
}
nodes.append(duration_primitive)

# Node 1: StoryScriptGenerator8Node
script_gen_node = {
    "id": 1,
    "type": "StoryScriptGenerator8Node",
    "pos": [-1600, 100],
    "size": [400, 550],
    "flags": {},
    "order": 6,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {"name": "script_json", "type": "STRING", "links": []},
        {"name": "scene_1_prompt", "type": "STRING", "links": []},
        {"name": "scene_2_prompt", "type": "STRING", "links": []},
        {"name": "scene_3_prompt", "type": "STRING", "links": []},
        {"name": "scene_4_prompt", "type": "STRING", "links": []},
        {"name": "scene_5_prompt", "type": "STRING", "links": []},
        {"name": "scene_6_prompt", "type": "STRING", "links": []},
        {"name": "scene_7_prompt", "type": "STRING", "links": []},
        {"name": "scene_8_prompt", "type": "STRING", "links": []},
        {"name": "motion_prompt_1", "type": "STRING", "links": []},
        {"name": "motion_prompt_2", "type": "STRING", "links": []},
        {"name": "motion_prompt_3", "type": "STRING", "links": []},
        {"name": "motion_prompt_4", "type": "STRING", "links": []},
        {"name": "motion_prompt_5", "type": "STRING", "links": []},
        {"name": "motion_prompt_6", "type": "STRING", "links": []},
        {"name": "motion_prompt_7", "type": "STRING", "links": []},
        {"name": "voiceover_1", "type": "STRING", "links": []},
        {"name": "voiceover_2", "type": "STRING", "links": []},
        {"name": "voiceover_3", "type": "STRING", "links": []},
        {"name": "voiceover_4", "type": "STRING", "links": []},
        {"name": "voiceover_5", "type": "STRING", "links": []},
        {"name": "voiceover_6", "type": "STRING", "links": []},
        {"name": "voiceover_7", "type": "STRING", "links": []},
        {"name": "voiceover_8", "type": "STRING", "links": []},
        {"name": "cast_summary", "type": "STRING", "links": []},
        {"name": "storyboard_grid_prompt", "type": "STRING", "links": []}
    ],
    "title": "1. Story Script Generator",
    "properties": {},
    "widgets_values": [
        "Космическое приключение двух роботов-исследователей на заброшенной станции",
        "",
        "gemini-2.5-flash",
        "cinematic 3D render, retro-futurism, glowing neon lights, highly detailed, octane render style",
        "poyo",
        "story_script_8",
        "auto"
    ]
}
nodes.append(script_gen_node)

# Node 2: PoyoAIImageNode (Grid Generator)
grid_gen_node = {
    "id": 2,
    "type": "PoyoAIImageNode",
    "pos": [-1150, 100],
    "size": [380, 480],
    "flags": {},
    "order": 7,
    "mode": 0,
    "inputs": [
        {"name": "reference_image", "type": "IMAGE", "link": None},
        {"name": "base_scene_image", "type": "IMAGE", "link": None},
        {"name": "api_key", "type": "STRING", "widget": {"name": "api_key"}, "link": None},
        {"name": "api_provider", "type": "COMBO", "widget": {"name": "api_provider"}, "link": None},
        {"name": "model", "type": "STRING", "widget": {"name": "model"}, "link": None},
        {"name": "prompt", "type": "STRING", "widget": {"name": "prompt"}, "link": None},
        {"name": "aspect_ratio", "type": "COMBO", "widget": {"name": "aspect_ratio"}, "link": None},
        {"name": "quality", "type": "COMBO", "widget": {"name": "quality"}, "link": None},
        {"name": "resolution", "type": "COMBO", "widget": {"name": "resolution"}, "link": None},
        {"name": "seed", "type": "INT", "widget": {"name": "seed"}, "link": None},
        {"name": "image_prompt_strength", "type": "FLOAT", "widget": {"name": "image_prompt_strength"}, "link": None}
    ],
    "outputs": [
        {"name": "IMAGE", "type": "IMAGE", "links": []}
    ],
    "title": "2a. Storyboard Grid Generator",
    "properties": {},
    "widgets_values": [
        "",  # API key (ignored due to link)
        "poyo",
        "gpt-image-2",
        "A vertical storyboard sheet with 4 rows and 2 columns, 8 numbered panels in left-to-right row order, showing a sequential story. Style: cinematic 3D render, retro-futurism, glowing neon lights. Robots exploring.",
        "9:16",
        "low",
        "1K",
        42,
        0.25
    ]
}
nodes.append(grid_gen_node)

# Wire Central API key (Node 48) -> Grid Gen (Node 2)
link_id_api_grid = link_counter
link_counter += 1
api_key_primitive["outputs"][0]["links"].append(link_id_api_grid)
grid_gen_node["inputs"][2]["link"] = link_id_api_grid
links.append([link_id_api_grid, 48, 0, 2, 2, "STRING"])

# Wire API Provider (Node 72) -> Grid Gen (Node 2)
link_id_provider_grid = link_counter
link_counter += 1
provider_primitive["outputs"][0]["links"].append(link_id_provider_grid)
grid_gen_node["inputs"][3]["link"] = link_id_provider_grid
links.append([link_id_provider_grid, 72, 0, 2, 3, "COMBO"])

# Wire StoryScript storyboard_grid_prompt -> Grid Gen prompt
link_id_grid_prompt = link_counter
link_counter += 1
script_gen_node["outputs"][25]["links"].append(link_id_grid_prompt)
grid_gen_node["inputs"][5]["link"] = link_id_grid_prompt
links.append([link_id_grid_prompt, 1, 25, 2, 5, "STRING"])

# Node 22: LoadImage (User custom grid upload)
load_image_node = {
    "id": 22,
    "type": "LoadImage",
    "pos": [-1150, 650],
    "size": [380, 350],
    "flags": {},
    "order": 8,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {"name": "IMAGE", "type": "IMAGE", "links": []}
    ],
    "title": "2b. Load Custom Grid (Optional Edit)",
    "properties": {},
    "widgets_values": [
        "example.png",
        "image"
    ]
}
nodes.append(load_image_node)

# Node 55: ImageCacheNode (Storyboard grid cache)
grid_cache_node = {
    "id": 55,
    "type": "ImageCacheNode",
    "pos": [-790, 70],
    "size": [340, 190],
    "flags": {},
    "order": 8,
    "mode": 0,
    "inputs": [
        {"name": "cache_name", "type": "STRING", "widget": {"name": "cache_name"}, "link": None},
        {"name": "mode", "type": "COMBO", "widget": {"name": "mode"}, "link": None},
        {"name": "global_mode", "type": "COMBO", "widget": {"name": "global_mode"}, "link": None},
        {"name": "image", "type": "IMAGE", "link": None},
        {"name": "cache_fingerprint", "type": "STRING", "widget": {"name": "cache_fingerprint"}, "link": None},
    ],
    "outputs": [
        {"name": "IMAGE", "type": "IMAGE", "links": []}
    ],
    "title": "2b. Storyboard Grid Cache",
    "properties": {},
    "widgets_values": [
        "story_i2v_grid",
        "auto",
        "auto",
        ""
    ],
    "color": "#232",
    "bgcolor": "#353"
}
nodes.append(grid_cache_node)

# Link Node 2 (Grid Gen) -> Node 55 (Grid Cache)
link_id = link_counter
link_counter += 1
grid_gen_node["outputs"][0]["links"].append(link_id)
grid_cache_node["inputs"][3]["link"] = link_id
links.append([link_id, 2, 0, 55, 3, "IMAGE"])

# Link StoryScript storyboard_grid_prompt -> Grid Cache fingerprint
link_id = link_counter
link_counter += 1
script_gen_node["outputs"][25]["links"].append(link_id)
grid_cache_node["inputs"][4]["link"] = link_id
links.append([link_id, 1, 25, 55, 4, "STRING"])

# Node 54: PreviewImage (Storyboard Grid Review)
grid_preview_node = {
    "id": 54,
    "type": "PreviewImage",
    "pos": [-700, -80],
    "size": [360, 300],
    "flags": {},
    "order": 8,
    "mode": 0,
    "inputs": [
        {"name": "images", "type": "IMAGE", "link": None}
    ],
    "outputs": [],
    "title": "Preview Storyboard Grid",
    "properties": {},
    "widgets_values": []
}
nodes.append(grid_preview_node)

# Link Node 55 (Grid Cache) -> Node 54 (Grid Preview)
link_id = link_counter
link_counter += 1
grid_cache_node["outputs"][0]["links"].append(link_id)
grid_preview_node["inputs"][0]["link"] = link_id
links.append([link_id, 55, 0, 54, 0, "IMAGE"])

# Node 23: ImageGateNode (Grid approval gate)
grid_gate_node = {
    "id": 23,
    "type": "ImageGateNode",
    "pos": [-700, 300],
    "size": [320, 200],
    "flags": {},
    "order": 9,
    "mode": 0,
    "inputs": [
        {"name": "image", "type": "IMAGE", "link": None}
    ],
    "outputs": [
        {"name": "IMAGE", "type": "IMAGE", "links": []}
    ],
    "title": "2c. Approve Storyboard Grid",
    "properties": {},
    "widgets_values": [
        False  # approved
    ]
}
nodes.append(grid_gate_node)

# Link Node 55 (Grid Cache) -> Node 23 (Grid Gate)
link_id = link_counter
link_counter += 1
grid_cache_node["outputs"][0]["links"].append(link_id)
grid_gate_node["inputs"][0]["link"] = link_id
links.append([link_id, 55, 0, 23, 0, "IMAGE"])

# Node 3: ImageGridSplitterNode
splitter_node = {
    "id": 3,
    "type": "ImageGridSplitterNode",
    "pos": [-320, 300],
    "size": [280, 220],
    "flags": {},
    "order": 10,
    "mode": 0,
    "inputs": [
        {"name": "image", "type": "IMAGE", "link": None}
    ],
    "outputs": [
        {"name": "image_1", "type": "IMAGE", "links": []},
        {"name": "image_2", "type": "IMAGE", "links": []},
        {"name": "image_3", "type": "IMAGE", "links": []},
        {"name": "image_4", "type": "IMAGE", "links": []},
        {"name": "image_5", "type": "IMAGE", "links": []},
        {"name": "image_6", "type": "IMAGE", "links": []},
        {"name": "image_7", "type": "IMAGE", "links": []},
        {"name": "image_8", "type": "IMAGE", "links": []}
    ],
    "title": "3. Image Grid Splitter",
    "properties": {},
    "widgets_values": [4, 2]
}
nodes.append(splitter_node)

# Link Node 23 (Grid Gate) -> Node 3 (Splitter)
link_id = link_counter
link_counter += 1
grid_gate_node["outputs"][0]["links"].append(link_id)
splitter_node["inputs"][0]["link"] = link_id
links.append([link_id, 23, 0, 3, 0, "IMAGE"])

preview_nodes = []
gate_nodes = []
video_nodes = []
video_generation_gate_nodes = []
video_gate_nodes = []
save_nodes = []

# Vertical Scene Rows (1 to 8)
for i in range(1, 9):
    y_pos = -1300 + (i-1)*550
    order_base = 11 + (i-1)*4

    # 1. Preview Sliced Image (Nodes 24-31)
    preview_id = 23 + i
    preview_node = {
        "id": preview_id,
        "type": "PreviewImage",
        "pos": [100, y_pos + 120],
        "size": [280, 250],
        "flags": {},
        "order": order_base,
        "mode": 0,
        "inputs": [
            {"name": "images", "type": "IMAGE", "link": None}
        ],
        "outputs": [],
        "title": f"Preview Scene {i} Image",
        "properties": {}
    }
    nodes.append(preview_node)
    preview_nodes.append(preview_node)

    # Link Splitter -> PreviewImage
    link_id_prev = link_counter
    link_counter += 1
    splitter_node["outputs"][i-1]["links"].append(link_id_prev)
    preview_node["inputs"][0]["link"] = link_id_prev
    links.append([link_id_prev, 3, i-1, preview_id, 0, "IMAGE"])

    # 2. Image Approval Gate (Nodes 32-39)
    gate_id = 31 + i
    gate_node = {
        "id": gate_id,
        "type": "ImageGateNode",
        "pos": [430, y_pos + 150],
        "size": [280, 160],
        "flags": {},
        "order": order_base + 1,
        "mode": 0,
        "inputs": [
            {"name": "image", "type": "IMAGE", "link": None}
        ],
        "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "links": []}
        ],
        "title": f"Approve Scene {i} Image",
        "properties": {},
        "widgets_values": [
            False  # approved
        ]
    }
    nodes.append(gate_node)
    gate_nodes.append(gate_node)

    # Link Splitter -> Gate
    link_id_gate = link_counter
    link_counter += 1
    splitter_node["outputs"][i-1]["links"].append(link_id_gate)
    gate_node["inputs"][0]["link"] = link_id_gate
    links.append([link_id_gate, 3, i-1, gate_id, 0, "IMAGE"])

    # 3. Video Generator (Nodes 4-11)
    video_id = 3 + i
    video_inputs = [
        {"name": "images.image_1", "type": "IMAGE", "link": None},
        {"name": "images.image_2", "type": "IMAGE", "link": None},
        {"name": "video_1", "type": "VIDEO", "link": None},
        {"name": "audio_1", "type": "AUDIO", "link": None},
        {"name": "api_key", "type": "STRING", "widget": {"name": "api_key"}, "link": None},
        {"name": "api_provider", "type": "COMBO", "widget": {"name": "api_provider"}, "link": None},
        {"name": "prompt", "type": "STRING", "widget": {"name": "prompt"}, "link": None},
        {"name": "model", "type": "COMBO", "widget": {"name": "model"}, "link": None},
        {"name": "resolution", "type": "COMBO", "widget": {"name": "resolution"}, "link": None},
        {"name": "aspect_ratio", "type": "COMBO", "widget": {"name": "aspect_ratio"}, "link": None},
        {"name": "duration", "type": "INT", "widget": {"name": "duration"}, "link": None},
        {"name": "generate_audio", "type": "BOOLEAN", "widget": {"name": "generate_audio"}, "link": None},
        {"name": "auto_upscale", "type": "BOOLEAN", "widget": {"name": "auto_upscale"}, "link": None},
        {"name": "seed", "type": "INT", "widget": {"name": "seed"}, "link": None}
    ]

    video_node = {
        "id": video_id,
        "type": "PoyoAISeedanceVideoNode",
        "pos": [1070, y_pos],
        "size": [380, 480],
        "flags": {},
        "order": order_base + 2,
        "mode": 0,
        "inputs": video_inputs,
        "outputs": [
            {"name": "VIDEO", "type": "VIDEO", "links": []}
        ],
        "title": f"Video Scene {i}" if i == 8 else f"Transition {i} ({i}->{i+1})",
        "properties": {"Node name for S&R": "PoyoAISeedanceVideoNode"},
        "widgets_values": [
            "", # api_key (linked)
            "poyo", # api_provider (linked)
            "", # prompt (linked)
            "veo3.1-lite-official", # model (linked)
            "720p", # resolution (linked)
            "9:16", # aspect_ratio (linked)
            6, # duration (linked)
            False,
            False,
            12345 + i,
            "randomize"
        ],
        "color": "#432" if i == 8 else "#134",
        "bgcolor": "#653" if i == 8 else "#256"
    }
    nodes.append(video_node)
    video_nodes.append(video_node)

    # 3b. Video generation approval gate (Nodes 64-71)
    # This sits before the paid video API call, so disabled means no video spend.
    video_generation_gate_id = 63 + i
    video_generation_gate_node = {
        "id": video_generation_gate_id,
        "type": "ImageGateNode",
        "pos": [750, y_pos + 150],
        "size": [280, 160],
        "flags": {},
        "order": order_base + 2,
        "mode": 0,
        "inputs": [
            {"name": "image", "type": "IMAGE", "link": None}
        ],
        "outputs": [
            {"name": "IMAGE", "type": "IMAGE", "links": []}
        ],
        "title": f"Approve Generate Scene {i} Video",
        "properties": {},
        "widgets_values": [
            False
        ],
        "color": "#232",
        "bgcolor": "#353"
    }
    nodes.append(video_generation_gate_node)
    video_generation_gate_nodes.append(video_generation_gate_node)

    # Wire Scene Image Gate -> Video Generation Gate
    link_id_gen_gate_in = link_counter
    link_counter += 1
    gate_node["outputs"][0]["links"].append(link_id_gen_gate_in)
    video_generation_gate_node["inputs"][0]["link"] = link_id_gen_gate_in
    links.append([link_id_gen_gate_in, gate_id, 0, video_generation_gate_id, 0, "IMAGE"])

    # Wire Video Generation Gate -> Video `images.image_1` (slot 0)
    link_id_v1 = link_counter
    link_counter += 1
    video_generation_gate_node["outputs"][0]["links"].append(link_id_v1)
    video_node["inputs"][0]["link"] = link_id_v1
    links.append([link_id_v1, video_generation_gate_id, 0, video_id, 0, "IMAGE"])

    # Wire centralized primitives (API Key, Model, Resolution, Aspect, Duration)
    # 1. API Key (Node 48) -> slot index 4
    link_id_api = link_counter
    link_counter += 1
    api_key_primitive["outputs"][0]["links"].append(link_id_api)
    video_node["inputs"][4]["link"] = link_id_api
    links.append([link_id_api, 48, 0, video_id, 4, "STRING"])

    # 1b. API Provider (Node 72) -> slot index 5
    link_id_provider = link_counter
    link_counter += 1
    provider_primitive["outputs"][0]["links"].append(link_id_provider)
    video_node["inputs"][5]["link"] = link_id_provider
    links.append([link_id_provider, 72, 0, video_id, 5, "COMBO"])

    # 2. Model (Node 49) -> slot index 7
    link_id_model = link_counter
    link_counter += 1
    model_primitive["outputs"][0]["links"].append(link_id_model)
    video_node["inputs"][7]["link"] = link_id_model
    links.append([link_id_model, 49, 0, video_id, 7, "COMBO"])

    # 3. Resolution (Node 50) -> slot index 8
    link_id_res = link_counter
    link_counter += 1
    resolution_primitive["outputs"][0]["links"].append(link_id_res)
    video_node["inputs"][8]["link"] = link_id_res
    links.append([link_id_res, 50, 0, video_id, 8, "COMBO"])

    # 4. Aspect Ratio (Node 51) -> slot index 9
    link_id_aspect = link_counter
    link_counter += 1
    aspect_primitive["outputs"][0]["links"].append(link_id_aspect)
    video_node["inputs"][9]["link"] = link_id_aspect
    links.append([link_id_aspect, 51, 0, video_id, 9, "COMBO"])

    # 5. Duration (Node 52) -> slot index 10
    link_id_dur = link_counter
    link_counter += 1
    duration_primitive["outputs"][0]["links"].append(link_id_dur)
    video_node["inputs"][10]["link"] = link_id_dur
    links.append([link_id_dur, 52, 0, video_id, 10, "INT"])

    # 4. Save Video / Player Preview (Nodes 40-47)
    save_id = 39 + i
    save_node = {
        "id": save_id,
        "type": "SaveVideo",
        "pos": [1500, y_pos],
        "size": [350, 450],
        "flags": {},
        "order": order_base + 3,
        "mode": 0,
        "inputs": [
            {"name": "video", "type": "VIDEO", "link": None}
        ],
        "outputs": [],
        "title": f"Play Scene {i} Clip",
        "properties": {},
        "widgets_values": [
            f"video/story_i2v_scene_{i}",
            "auto",
            "auto"
        ]
    }
    nodes.append(save_node)
    save_nodes.append(save_node)

    # Link Video -> SaveVideo
    link_id_sv = link_counter
    link_counter += 1
    video_node["outputs"][0]["links"].append(link_id_sv)
    save_node["inputs"][0]["link"] = link_id_sv
    links.append([link_id_sv, video_id, 0, save_id, 0, "VIDEO"])

    # 5. Video Approval Gate (Nodes 56-63)
    video_gate_id = 55 + i
    video_gate_node = {
        "id": video_gate_id,
        "type": "VideoGateNode",
        "pos": [1880, y_pos + 120],
        "size": [320, 160],
        "flags": {},
        "order": order_base + 4,
        "mode": 0,
        "inputs": [
            {"name": "video", "type": "VIDEO", "link": None}
        ],
        "outputs": [
            {"name": "VIDEO", "type": "VIDEO", "links": []}
        ],
        "title": f"Approve Scene {i} Video",
        "properties": {},
        "widgets_values": [
            False
        ],
        "color": "#232",
        "bgcolor": "#353"
    }
    nodes.append(video_gate_node)
    video_gate_nodes.append(video_gate_node)

    # Link Video -> Video Approval Gate
    link_id_vg = link_counter
    link_counter += 1
    video_node["outputs"][0]["links"].append(link_id_vg)
    video_gate_node["inputs"][0]["link"] = link_id_vg
    links.append([link_id_vg, video_id, 0, video_gate_id, 0, "VIDEO"])

# Wire transition inputs (Gates for image_2 & prompts)
for i in range(1, 8):
    video_node = video_nodes[i-1]
    video_id = 3 + i

    # Gate i+1 -> Video images.image_2 (slot 1)
    gate_next_id = 31 + i + 1
    link_id_v2 = link_counter
    link_counter += 1
    gate_nodes[i]["outputs"][0]["links"].append(link_id_v2)
    video_node["inputs"][1]["link"] = link_id_v2
    links.append([link_id_v2, gate_next_id, 0, video_id, 1, "IMAGE"])

    # Script Gen motion prompt -> Video prompt (slot 6)
    # motion_prompt_1 is slot index 9, ..., motion_prompt_7 is slot index 15
    link_id_prompt = link_counter
    link_counter += 1
    script_gen_node["outputs"][8+i]["links"].append(link_id_prompt)
    video_node["inputs"][6]["link"] = link_id_prompt
    links.append([link_id_prompt, 1, 8+i, video_id, 6, "STRING"])

# Wire scene_8_prompt to Video Scene 8 prompt
# scene_8_prompt is slot index 8
link_id_prompt_8 = link_counter
link_counter += 1
script_gen_node["outputs"][8]["links"].append(link_id_prompt_8)
video_nodes[7]["inputs"][6]["link"] = link_id_prompt_8
links.append([link_id_prompt_8, 1, 8, 11, 6, "STRING"])

# Node 20: VideoConcat8FFmpegNode
concat_inputs = []
for i in range(1, 9):
    concat_inputs.append({"name": f"video_{i}", "type": "VIDEO", "link": None})
for i in range(1, 9):
    concat_inputs.append({"name": f"audio_{i}", "type": "AUDIO", "link": None})
concat_inputs.extend([
    {"name": "filename_prefix", "type": "STRING", "link": None},
    {"name": "mode", "type": "COMBO", "link": None},
    {"name": "subtitles", "type": "COMBO", "link": None},
    {"name": "subtitle_text_1", "type": "STRING", "link": None},
    {"name": "subtitle_text_2", "type": "STRING", "link": None},
    {"name": "subtitle_text_3", "type": "STRING", "link": None},
    {"name": "subtitle_text_4", "type": "STRING", "link": None},
    {"name": "subtitle_text_5", "type": "STRING", "link": None},
    {"name": "subtitle_text_6", "type": "STRING", "link": None},
    {"name": "subtitle_text_7", "type": "STRING", "link": None},
    {"name": "subtitle_text_8", "type": "STRING", "link": None},
    {"name": "subtitle_style", "type": "STRING", "link": None}
])

concat_node = {
    "id": 20,
    "type": "VideoConcat8FFmpegNode",
    "pos": [2300, 500],
    "size": [450, 550],
    "flags": {},
    "order": 45,
    "mode": 0,
    "inputs": concat_inputs,
    "outputs": [
        {"name": "video", "type": "VIDEO", "links": []}
    ],
    "title": "5. Video Concat (8 Scenes / 7 Transitions)",
    "properties": {},
    "widgets_values": [
        "story_i2v_final",
        "stream copy (fast)",
        "off",
        "", "", "", "", "", "", "", "",
        "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=40"
    ]
}

# Wire approved video inputs to Stitcher
for i in range(1, 9):
    link_id_c = link_counter
    link_counter += 1
    video_gate_nodes[i-1]["outputs"][0]["links"].append(link_id_c)
    concat_node["inputs"][i-1]["link"] = link_id_c
    links.append([link_id_c, 55+i, 0, 20, i-1, "VIDEO"])

nodes.append(concat_node)

# Node 21: SaveVideo (Final stitched player)
final_save_node = {
    "id": 21,
    "type": "SaveVideo",
    "pos": [2800, 550],
    "size": [450, 450],
    "flags": {},
    "order": 46,
    "mode": 0,
    "inputs": [
        {"name": "video", "type": "VIDEO", "link": None}
    ],
    "outputs": [],
    "title": "6. Play Final stitched Video",
    "properties": {},
    "widgets_values": [
        "story_i2v_final_output",
        "auto",
        "auto"
    ]
}

# Link Stitcher -> Final SaveVideo
link_id_final = link_counter
link_counter += 1
concat_node["outputs"][0]["links"].append(link_id_final)
final_save_node["inputs"][0]["link"] = link_id_final
links.append([link_id_final, 20, 0, 21, 0, "VIDEO"])

nodes.append(final_save_node)

workflow["nodes"] = nodes
workflow["links"] = links
workflow["last_node_id"] = 72
workflow["last_link_id"] = link_counter - 1

# Write to workflows path
target_path = "/Users/alex/code/ComfyUI/user/default/workflows/story_i2v_pipeline.json"
with open(target_path, "w", encoding="utf-8") as f:
    json.dump(workflow, f, ensure_ascii=False, indent=2)

print(f"Generated visual interactive workflow JSON with centralized settings at: {target_path}")
