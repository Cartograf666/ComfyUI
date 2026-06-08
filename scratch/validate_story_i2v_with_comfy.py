import asyncio
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.argv = [sys.argv[0], "--cpu", "--disable-auto-launch"]

import comfy.options

comfy.options.enable_args_parsing()

import execution
import nodes


def restore_root_utils_package():
    utils_init = ROOT / "utils/__init__.py"
    spec = importlib.util.spec_from_file_location(
        "utils",
        utils_init,
        submodule_search_locations=[str(ROOT / "utils")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["utils"] = module
    spec.loader.exec_module(module)


WORKFLOW = ROOT / "user/default/workflows/story_i2v_pipeline.json"
CONTROL_AFTER_GENERATE_VALUES = {
    "fixed",
    "increment",
    "decrement",
    "randomize",
    "random",
}


def build_api_prompt(workflow):
    node_by_id = {node["id"]: node for node in workflow["nodes"]}
    link_by_id = {link[0]: link for link in workflow["links"]}
    prompt = {}

    for node in workflow["nodes"]:
        widget_values = node.get("widgets_values") or []
        widget_index = 0
        inputs = {}

        for input_info in node.get("inputs") or []:
            name = input_info["name"]
            link_id = input_info.get("link")
            widget = input_info.get("widget")

            if widget is not None:
                if widget_index >= len(widget_values):
                    raise RuntimeError(
                        f"Node {node['id']} {node['type']} is missing widget value for input {name!r}"
                    )
                widget_value = widget_values[widget_index]
                widget_index += 1
                if name == "seed" and widget_index < len(widget_values):
                    if widget_values[widget_index] in CONTROL_AFTER_GENERATE_VALUES:
                        widget_index += 1
            else:
                widget_value = None

            if link_id is not None:
                link = link_by_id[link_id]
                src_id = str(link[1])
                src_slot = link[2]
                inputs[name] = [src_id, src_slot]
            elif widget is not None:
                inputs[name] = widget_value

        prompt[str(node["id"])] = {
            "class_type": node["type"],
            "inputs": inputs,
            "_meta": {"title": node.get("title", node["type"])},
        }

    return prompt


async def main():
    restore_root_utils_package()
    modules = [
        (ROOT / "comfy_extras/nodes_video.py", "comfy_extras"),
        (ROOT / "comfy_api_nodes/nodes_scene_parser.py", "comfy_api_nodes"),
        (ROOT / "custom_nodes/comfyui_story_i2v.py", "custom_nodes"),
    ]
    for module_path, module_parent in modules:
        ok = await nodes.load_custom_node(str(module_path), module_parent=module_parent)
        if not ok:
            raise RuntimeError(f"Failed to load {module_path}")

    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    prompt = build_api_prompt(workflow)
    ok, error, outputs, node_errors = await execution.validate_prompt(
        "story_i2v_dry_run",
        prompt,
        None,
    )

    if not ok:
        print("FAILED: ComfyUI validate_prompt rejected story_i2v workflow")
        print(json.dumps(error, ensure_ascii=False, indent=2))
        if node_errors:
            print(json.dumps(node_errors, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    print(f"SUCCESS: ComfyUI validate_prompt accepted story_i2v workflow ({len(outputs)} output nodes).")


if __name__ == "__main__":
    asyncio.run(main())
