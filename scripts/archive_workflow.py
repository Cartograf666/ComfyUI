#!/usr/bin/env python3
"""Archive a ComfyUI workflow with a clear dated filename.

Example:
    python scripts/archive_workflow.py user/default/workflows/viral_video_pipeline.json before-major-redesign

Output:
    user/default/workflows/archive/viral_video_pipeline__archived_2026-05-28_1249__before-major-redesign.json
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


def slugify(value: str) -> str:
    safe = []
    prev_dash = False
    for ch in value.strip().lower():
        if ch.isalnum():
            safe.append(ch)
            prev_dash = False
        elif not prev_dash:
            safe.append("-")
            prev_dash = True
    return "".join(safe).strip("-") or "archived"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path, help="Workflow JSON to archive.")
    parser.add_argument(
        "label",
        nargs="?",
        default="previous-pipeline",
        help="Human-readable archive label, e.g. before-subtitles.",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move instead of copy. Default is copy, which keeps the active workflow in place.",
    )
    args = parser.parse_args()

    workflow = args.workflow
    if not workflow.exists():
        raise SystemExit(f"Workflow not found: {workflow}")

    archive_dir = workflow.parent / "archive"
    archive_dir.mkdir(exist_ok=True)

    archived_at = datetime.now().strftime("%Y-%m-%d_%H%M")
    label = slugify(args.label)
    name = f"{workflow.stem}__archived_{archived_at}__{label}.json"
    destination = unique_path(archive_dir / name)

    if args.move:
        shutil.move(str(workflow), str(destination))
    else:
        shutil.copy2(workflow, destination)

    print(destination)


if __name__ == "__main__":
    main()
