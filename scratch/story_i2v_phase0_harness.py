import argparse
import datetime as dt
import json
import os
from itertools import product


DEFAULT_LEDGER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "story_i2v_phase0_ledger.jsonl",
)

STRENGTHS = [0.1, 0.3, 0.5]
PANEL_STRATEGIES = ["reference", "keyframe"]
IMAGE_MODELS = [
    "gpt-image-2",
    "black-forest-labs/flux-1-dev",
    "black-forest-labs/flux-1-pro",
]
VIDEO_MODELS = ["seedance-2", "kling-v2.0"]


def _now():
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def experiment_matrix(project_slug):
    for idx, (strength, panel_strategy, image_model, video_model) in enumerate(
        product(STRENGTHS, PANEL_STRATEGIES, IMAGE_MODELS, VIDEO_MODELS),
        start=1,
    ):
        image_provider = "atlascloud" if image_model.startswith("black-forest-labs/") else "poyo"
        video_provider = "atlascloud" if video_model.startswith("kling") else "poyo"
        yield {
            "experiment_id": f"s0-{idx:03d}",
            "created_at": _now(),
            "project_slug": project_slug,
            "cache_mode": "regenerate",
            "seed": 101,
            "text_provider": image_provider,
            "image_provider": image_provider,
            "image_model": image_model,
            "video_provider": video_provider,
            "video_model": video_model,
            "grid_resolution": "2K 9:16 1440x2560",
            "panel_strategy": panel_strategy,
            "image_prompt_strength": strength,
            "negative_prompt_enabled": True,
            "character_bible_injected": True,
            "flf_enabled": True,
            "outputs": {},
            "human_score": {
                "character": None,
                "background": None,
                "motion": None,
                "cuts": None,
                "narration_readiness": None,
            },
            "notes": "",
        }


def init_ledger(path, project_slug, force=False):
    if os.path.exists(path) and not force:
        raise SystemExit(f"Ledger already exists: {path}")
    with open(path, "w", encoding="utf-8") as f:
        for row in experiment_matrix(project_slug):
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def summarize(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    done = 0
    for row in rows:
        scores = row.get("human_score", {})
        if all(scores.get(k) is not None for k in ("character", "background", "motion", "cuts")):
            done += 1
    return {
        "ledger": path,
        "experiments": len(rows),
        "scored": done,
        "pending": len(rows) - done,
    }


def main():
    parser = argparse.ArgumentParser(description="Story I2V Phase 0 experiment ledger helper.")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--project-slug", default="story_i2v_phase0")
    parser.add_argument("--init", action="store_true", help="Create a JSONL ledger with the Phase 0 matrix.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing ledger when used with --init.")
    parser.add_argument("--summary", action="store_true", help="Print ledger summary.")
    args = parser.parse_args()

    if args.init:
        path = init_ledger(args.ledger, args.project_slug, force=args.force)
        print(f"Created Phase 0 ledger: {path}")
    if args.summary:
        print(json.dumps(summarize(args.ledger), ensure_ascii=False, indent=2))
    if not args.init and not args.summary:
        parser.print_help()


if __name__ == "__main__":
    main()
