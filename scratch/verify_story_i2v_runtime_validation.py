import json
import sys


WORKFLOW = "user/default/workflows/story_i2v_pipeline.json"


def main():
    with open(WORKFLOW, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = {node["id"]: node for node in data["nodes"]}
    errors = []

    links = {link[0]: link for link in data["links"]}
    compatible = {
        ("COMBO", "STRING"),
        ("STRING", "*"),
        ("COMBO", "*"),
        ("IMAGE", "*"),
        ("VIDEO", "*"),
        ("FLOAT", "*"),
        ("INT", "*"),
    }

    for link in data["links"]:
        lid, src, src_slot, dst, dst_slot, _typ = link
        src_out = nodes[src]["outputs"][src_slot]
        dst_in = nodes[dst]["inputs"][dst_slot]
        src_type = src_out.get("type")
        dst_type = dst_in.get("type")
        if src_type != dst_type and (src_type, dst_type) not in compatible:
            errors.append(
                f"Link {lid}: {src}:{nodes[src]['type']}.{src_out.get('name')} "
                f"({src_type}) -> {dst}:{nodes[dst]['type']}.{dst_in.get('name')} ({dst_type})"
            )

    for node_id in (201, 203):
        node = nodes.get(node_id)
        if not node:
            continue
        for inp in node.get("inputs") or []:
            if inp.get("name") == "cache_mode" and inp.get("link") is not None:
                errors.append(
                    f"Node {node_id} {node['type']}: cache_mode must stay as a widget, "
                    "not a linked value; old-style combo validation rejects generic outputs."
                )

    expected_image_inputs = [
        "prompt",
        "api_provider",
        "model",
        "resolution",
        "aspect_ratio",
        "quality",
        "seed",
        "image_prompt_strength",
        "cache_name",
        "mode",
        "approved",
        "base_scene_image",
        "reference_image",
        "api_key",
        "global_mode",
        "cache_fingerprint",
    ]
    expected_video_inputs = [
        "prompt",
        "api_provider",
        "model",
        "resolution",
        "aspect_ratio",
        "duration",
        "generate_audio",
        "auto_upscale",
        "seed",
        "cache_name",
        "mode",
        "approved",
        "image_1",
        "image_2",
        "api_key",
        "global_mode",
        "cache_fingerprint",
    ]

    grid = nodes.get(2)
    if grid:
        input_names = [inp.get("name") for inp in grid.get("inputs") or []]
        if input_names != expected_image_inputs:
            errors.append(f"StoryboardGridProcessorNode input order is {input_names!r}.")
        widgets = grid.get("widgets_values") or []
        if len(widgets) != 15:
            errors.append(f"StoryboardGridProcessorNode widgets length is {len(widgets)}, expected 15.")
        else:
            if widgets[13] not in ("auto", "use cached", "regenerate"):
                errors.append(f"StoryboardGridProcessorNode global_mode is {widgets[13]!r}.")
            if not isinstance(widgets[8], (int, float)):
                errors.append(f"StoryboardGridProcessorNode image_prompt_strength is {widgets[8]!r}.")
            if not isinstance(widgets[11], bool):
                errors.append(f"StoryboardGridProcessorNode approved is {widgets[11]!r}.")

    for node_id in range(204, 212):
        node = nodes.get(node_id)
        if not node:
            continue
        input_names = [inp.get("name") for inp in node.get("inputs") or []]
        if input_names != expected_image_inputs:
            errors.append(f"ViralSceneImageProcessorNode {node_id} input order is {input_names!r}.")
        widgets = node.get("widgets_values") or []
        if len(widgets) != 15:
            errors.append(f"ViralSceneImageProcessorNode {node_id} widgets length is {len(widgets)}, expected 15.")
            continue
        if widgets[13] not in ("auto", "use cached", "regenerate"):
            errors.append(f"ViralSceneImageProcessorNode {node_id} global_mode is {widgets[13]!r}.")
        if not isinstance(widgets[8], (int, float)):
            errors.append(f"ViralSceneImageProcessorNode {node_id} image_prompt_strength is {widgets[8]!r}.")
        if not isinstance(widgets[11], bool):
            errors.append(f"ViralSceneImageProcessorNode {node_id} approved is {widgets[11]!r}.")

    for node_id in (4, 5, 6, 7, 8, 9, 10, 11):
        node = nodes.get(node_id)
        if not node:
            continue
        input_names = [inp.get("name") for inp in node.get("inputs") or []]
        if input_names != expected_video_inputs:
            errors.append(f"StorySceneVideoProcessorNode {node_id} input order is {input_names!r}.")
        widgets = node.get("widgets_values") or []
        if len(widgets) != 16:
            errors.append(f"StorySceneVideoProcessorNode {node_id} widgets length is {len(widgets)}, expected 16.")
            continue
        if widgets[14] not in ("auto", "use cached", "regenerate"):
            errors.append(f"StorySceneVideoProcessorNode {node_id} global_mode is {widgets[14]!r}.")
        if not isinstance(widgets[12], bool):
            errors.append(f"StorySceneVideoProcessorNode {node_id} approved is {widgets[12]!r}.")

    if errors:
        print("FAILED story_i2v runtime validation:")
        for error in errors:
            print(f"- {error}")
        sys.exit(1)

    print("SUCCESS: story_i2v runtime validation checks passed.")


if __name__ == "__main__":
    main()
