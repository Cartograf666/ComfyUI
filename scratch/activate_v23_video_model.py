#!/usr/bin/env python3
"""Activate V2.3 — wire ViralAPIConfigNode.video_model_text (STRING, slot 5) into the
6 BuildVeoPromptNode.video_model inputs so the per-model preset actually fires.

Idempotent: bails if BuildVeo node 184 already has video_model linked.
Backup: .bak_activate_v23
"""
import json
import shutil
import sys

WF = "user/default/workflows/viral_video_pipeline.json"
CONFIG_ID = 311
BUILDVEO = [184, 185, 186, 187, 188, 189]
SLOT = 5  # video_model_text output index

d = json.load(open(WF))
nodes = {n["id"]: n for n in d["nodes"]}
links = d["links"]

cfg = nodes[CONFIG_ID]
assert cfg["type"] == "ViralAPIConfigNode", "config node missing/wrong type"

n184 = nodes[184]
if any(i["name"] == "video_model" and i.get("link") is not None for i in n184["inputs"]):
    sys.exit("BuildVeo already wired to video_model — V2.3 already active. Aborting.")

# Ensure the config node has output slot 5 (video_model_text).
outs = cfg["outputs"]
if len(outs) <= SLOT:
    assert len(outs) == SLOT, f"unexpected output count {len(outs)}"
    outs.append({"localized_name": "video_model_text", "name": "video_model_text",
                 "type": "STRING", "links": []})
vmt = outs[SLOT]
assert vmt["name"] == "video_model_text"
if vmt.get("links") is None:
    vmt["links"] = []

next_link = max(l[0] for l in links) + 1
for nid in BUILDVEO:
    n = nodes[nid]
    slot = next((i for i in n["inputs"] if i["name"] == "video_model"), None)
    if slot is None:
        slot = {"localized_name": "video_model", "name": "video_model", "type": "STRING",
                "widget": {"name": "video_model"}, "link": None}
        n["inputs"].append(slot)
        # video_model is the last schema widget; keep widgets_values count consistent.
        wv = n.setdefault("widgets_values", [])
        wv.append("")
    lid = next_link; next_link += 1
    links.append([lid, CONFIG_ID, SLOT, nid, n["inputs"].index(slot), "STRING"])
    slot["link"] = lid
    slot["type"] = "STRING"
    vmt["links"].append(lid)

if isinstance(d.get("last_link_id"), int):
    d["last_link_id"] = max(d["last_link_id"], next_link - 1)

shutil.copy(WF, WF + ".bak_activate_v23")
json.dump(d, open(WF, "w"), indent=2, ensure_ascii=False)
print(f"OK. video_model_text(slot{SLOT}) → {len(BUILDVEO)} BuildVeo nodes. Backup: {WF}.bak_activate_v23")
