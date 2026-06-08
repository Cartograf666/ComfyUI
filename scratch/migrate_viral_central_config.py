#!/usr/bin/env python3
"""V1.3/V1.4 — central provider/model config for the Viral pipeline.

Replaces the scattered PrimitiveNodes (91 api_key, 164 image_model, 193 video_model)
with a single ViralAPIConfigNode, and fans its outputs into every Poyo image/video
node — including the `api_provider` input that was missing from all 14 saved nodes
(so they silently defaulted to "poyo" and could not be switched to atlascloud).

Idempotency: refuses to run twice (bails if a ViralAPIConfigNode already exists).
Writes a .bak_central_config backup before saving.
"""
import json
import shutil
import sys

WF = "user/default/workflows/viral_video_pipeline.json"
IMG_NODES = [10, 20, 30, 40, 50, 60, 232, 236]   # PoyoAIImageNode
VID_NODES = [15, 25, 35, 45, 55, 65]             # PoyoAISeedanceVideoNode
PRIM_KEY, PRIM_IMG, PRIM_VID = 91, 164, 193

d = json.load(open(WF))
nodes = {n["id"]: n for n in d["nodes"]}
links = d["links"]  # [id, origin_node, origin_slot, target_node, target_slot, type]

if any(n["type"] == "ViralAPIConfigNode" for n in d["nodes"]):
    sys.exit("ViralAPIConfigNode already present — migration already applied. Aborting.")

# Preserve current behavior: seed the config node with the existing widget values.
cur_key = nodes[PRIM_KEY]["widgets_values"][0]
cur_img = nodes[PRIM_IMG]["widgets_values"][0]
cur_vid = nodes[PRIM_VID]["widgets_values"][0]

next_link = max(l[0] for l in links) + 1
def new_link_id():
    global next_link
    v = next_link
    next_link += 1
    return v

NEW_ID = max(nodes) + 1

# ── 1. Build the config node ────────────────────────────────────────────────────
# Output slots: 0 api_provider(COMBO) 1 api_key(STRING) 2 image_model(STRING)
#               3 video_model(COMBO)  4 status(STRING)
prov_links, key_links, imgm_links, vidm_links = [], [], [], []
px, py = nodes[PRIM_KEY]["pos"][0] - 360, nodes[PRIM_KEY]["pos"][1]
config = {
    "id": NEW_ID,
    "type": "ViralAPIConfigNode",
    "pos": [px, py],
    "size": [320, 130],
    "flags": {},
    "order": 0,
    "mode": 0,
    "inputs": [],
    "outputs": [
        {"localized_name": "api_provider", "name": "api_provider", "type": "COMBO", "links": prov_links},
        {"localized_name": "api_key", "name": "api_key", "type": "STRING", "links": key_links},
        {"localized_name": "image_model", "name": "image_model", "type": "STRING", "links": imgm_links},
        {"localized_name": "video_model", "name": "video_model", "type": "COMBO", "links": vidm_links},
        {"localized_name": "status", "name": "status", "type": "STRING", "links": []},
    ],
    "properties": {"Node name for S&R": "ViralAPIConfigNode"},
    "widgets_values": ["poyo", cur_key, cur_img, cur_vid],
}

# ── 2. Repoint existing links from the primitives to the config node ─────────────
def repoint(prim_id, out_slot, collector):
    for l in links:
        if l[1] == prim_id:                # origin is the primitive
            l[1] = NEW_ID                  # → config node
            l[2] = out_slot                # → correct output slot
            collector.append(l[0])

repoint(PRIM_KEY, 1, key_links)            # api_key  → slot 1
repoint(PRIM_IMG, 2, imgm_links)           # image_model → slot 2
repoint(PRIM_VID, 3, vidm_links)           # video_model → slot 3

# ── 3. Helper to add a brand-new link feeding a node input ───────────────────────
def add_input_link(target_id, input_name, out_slot, link_type, collector, *, is_widget=True):
    """Create a link config.out[out_slot] → target.input[input_name]."""
    lid = new_link_id()
    links.append([lid, NEW_ID, out_slot, target_id, 0, link_type])  # target_slot fixed below
    n = nodes[target_id]
    # find or create the input slot
    slot = next((i for i in n["inputs"] if i["name"] == input_name), None)
    if slot is None:
        slot = {"localized_name": input_name, "name": input_name, "type": link_type, "link": None}
        if is_widget:
            slot["widget"] = {"name": input_name}
        n["inputs"].append(slot)
    slot["link"] = lid
    slot["type"] = link_type
    if is_widget and "widget" not in slot:
        slot["widget"] = {"name": input_name}
    # fix target_slot index in the link to this slot's position
    links[-1][4] = n["inputs"].index(slot)
    collector.append(lid)

# ── 4. api_provider → all 14 nodes (+ canonical widgets_values insert at idx 1) ──
for nid in IMG_NODES + VID_NODES:
    n = nodes[nid]
    if any(i["name"] == "api_provider" for i in n["inputs"]):
        continue  # already wired (shouldn't happen pre-migration)
    add_input_link(nid, "api_provider", 0, "COMBO", prov_links)
    wv = n["widgets_values"]
    # api_provider is the 2nd widget (index 1) in both node schemas; insert if absent.
    if not (len(wv) > 1 and wv[1] in ("poyo", "atlascloud")):
        wv.insert(1, "poyo")

# ── 5. Wire the two orphaned image nodes (232, 236) to key + image_model ─────────
for nid in (232, 236):
    n = nodes[nid]
    ak = next(i for i in n["inputs"] if i["name"] == "api_key")
    if ak.get("link") is None:
        lid = new_link_id()
        links.append([lid, NEW_ID, 1, nid, n["inputs"].index(ak), "STRING"])
        ak["link"] = lid
        key_links.append(lid)
    mk = next(i for i in n["inputs"] if i["name"] == "model")
    if mk.get("link") is None:
        lid = new_link_id()
        links.append([lid, NEW_ID, 2, nid, n["inputs"].index(mk), "STRING"])
        mk["link"] = lid
        imgm_links.append(lid)

# ── 6. Delete the three primitives, add the config node ──────────────────────────
d["nodes"] = [n for n in d["nodes"] if n["id"] not in (PRIM_KEY, PRIM_IMG, PRIM_VID)]
d["nodes"].append(config)
if isinstance(d.get("last_node_id"), int):
    d["last_node_id"] = max(d["last_node_id"], NEW_ID)
if isinstance(d.get("last_link_id"), int):
    d["last_link_id"] = max(d["last_link_id"], next_link - 1)

shutil.copy(WF, WF + ".bak_central_config")
json.dump(d, open(WF, "w"), indent=2, ensure_ascii=False)
print(f"OK. config node id={NEW_ID}; provider→{len(prov_links)} key→{len(key_links)} "
      f"image_model→{len(imgm_links)} video_model→{len(vidm_links)} nodes. Backup: {WF}.bak_central_config")
