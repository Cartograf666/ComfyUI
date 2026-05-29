"""Scene parser and audio concat utilities for the viral video pipeline.

SceneParserNode  — Parses a structured 6-scene script into individual fields
                   (image_prompt, video_prompt, voiceover) per scene.
AudioConcatNode  — Concatenates up to 6 audio clips in sequence.

Expected script format (one SCENE block per scene, separated by ---):

  SCENE 1: Title
  Visual: Two-sentence cinematic description.
  Image Prompt: detailed single-line prompt
  Video Prompt: camera motion description for Seedance
  Voiceover: One sentence, max 8 words.
  ---
  SCENE 2: ...
"""

import asyncio
import json
import re
from collections import Counter, defaultdict
from urllib.parse import parse_qs, urlparse

import aiohttp
import torch

from comfy_api.latest import IO, ComfyExtension
from comfy_api_nodes.util import audio_bytes_to_audio_input, validate_string

_MAX_SCENES = 6


# ══════════════════════════════════════════════════════════════════════════════
# RUN-SCOPED USAGE LOG (consumed by CostTrackerNode)
# ══════════════════════════════════════════════════════════════════════════════
# All revenue-relevant nodes (Poyo Image/Video, ElevenLabs TTS, Gemini text)
# append a dict per call into this in-memory list. CostTrackerNode reads + clears
# it at the end of a workflow run. Cache nodes log cache_hit=True entries with
# zero cost so we can show "saved by cache" stats.
_RUN_USAGE_LOG: list[dict] = []


def log_usage(category: str, **kwargs) -> None:
    """Lightweight, fire-and-forget logger called by API and cache nodes.

    category ∈ {"image", "video", "audio", "text"}
    kwargs convention:
      - cache_hit (bool)       — True if this entry is a cache hit (no cost)
      - model (str)            — e.g. "gpt-image-2", "veo3.1-lite-official"
      - resolution (str)       — e.g. "720p", "1K"
      - duration (float)       — seconds (for video / audio)
      - chars (int)            — characters processed (for TTS)
      - tokens_in / tokens_out — for LLM calls
      - scene (str)            — optional scene tag for breakdown
    """
    entry = {"category": category, **kwargs}
    _RUN_USAGE_LOG.append(entry)


def get_and_clear_usage_log() -> list[dict]:
    entries = list(_RUN_USAGE_LOG)
    _RUN_USAGE_LOG.clear()
    return entries


def _effective_cache_mode(mode: str, global_mode: str = "auto") -> str:
    local = (mode or "auto").strip()
    global_value = (global_mode or "auto").strip()
    if local == "global":
        return global_value if global_value in {"auto", "use cached", "regenerate"} else "auto"
    return local if local in {"auto", "use cached", "regenerate"} else "auto"


_VEO_PROMPT_MAX_CHARS = 950


def _strip_markdown(text: str) -> str:
    """Remove markdown bold/italic markers that Gemini sometimes adds."""
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)
    return text


def _parse_field(script: str, scene_n: int, field: str) -> str:
    """Extract a single named field from scene N of a structured script.

    The colon after the scene number is REQUIRED — this is how we distinguish a
    real scene header (`SCENE 1: Title`) from a list item that references scenes
    by number (`- Scene 1 = HOOK`, which the model often echoes from the system
    prompt's STORY ARC section).
    """
    clean = _strip_markdown(script)
    # Match scene N block up to scene N+1 or end of string. The `\s*:` after the
    # scene number is what filters out story-arc list items.
    block = re.search(
        rf"SCENE\s+{scene_n}\s*:.*?(?=SCENE\s+{scene_n + 1}\s*:|$)",
        clean,
        re.DOTALL | re.IGNORECASE,
    )
    if not block:
        return ""
    block_text = block.group(0)

    # Field-name suffix tolerance: matches "Field:", "Field (note):", "Field - detail:",
    # "Field — long form:" etc. Anything between the field name and the colon that isn't
    # itself a colon or newline is swallowed so the parser survives Gemini decorating
    # the label, which she does often.
    suffix = r"(?:\s*[\(\[\-\—\–][^:\n]*)?"

    # Primary: single-line value after "Field[…]:" on its own line
    match = re.search(
        rf"^\s*{re.escape(field)}{suffix}\s*:\s*(.+)$",
        block_text,
        re.MULTILINE | re.IGNORECASE,
    )
    if match:
        value = match.group(1).strip()
        if value:
            return value

    # Fallback: capture until next field header or separator
    match = re.search(
        rf"{re.escape(field)}{suffix}\s*:\s*(.+?)(?=\n\s*(?:Visual|Image Prompt|Video Prompt|Voiceover|Continuity|SCENE){suffix}\s*:?|---|\Z)",
        block_text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return " ".join(match.group(1).strip().split())

    return ""


def _parse_global_field(script: str, field: str) -> str:
    """Extract a top-level bible/header field before the first scene block."""
    clean = _strip_markdown(script)
    first_scene = re.search(r"^\s*SCENE\s+\d+\s*:", clean, re.MULTILINE | re.IGNORECASE)
    header = clean[:first_scene.start()] if first_scene else clean

    suffix = r"(?:\s*[\(\[\-\—\–][^:\n]*)?"
    match = re.search(
        rf"^\s*{re.escape(field)}{suffix}\s*:\s*(.+?)(?=\n\s*(?:VISUAL STYLE|CHARACTER BIBLE|SETTING BIBLE|STORY SPINE|SIGNATURE OBJECT|SCENE)\s*:|\Z)",
        header,
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return " ".join(match.group(1).strip().split())


def _short_character_bible(character_bible: str) -> str:
    """Keep identity continuity without letting it dominate every image prompt."""
    text = character_bible.strip().rstrip(".")
    if not text:
        return ""

    # Keep the recognizable appearance tokens, but cap the repeated boilerplate.
    words = text.split()
    if len(words) > 34:
        text = " ".join(words[:34]).rstrip(" ,;:")
    return f"same recurring character: {text}"


def _dedupe_join(parts: list[str]) -> str:
    seen = set()
    clean_parts = []
    for part in parts:
        text = " ".join((part or "").strip().split())
        if not text:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        clean_parts.append(text.rstrip(" ,;:."))
    return ", ".join(clean_parts)


def _clip_text(text: str, max_chars: int) -> str:
    """Trim text without leaving a ragged half-word at the end."""
    clean = " ".join((text or "").strip().split())
    if len(clean) <= max_chars:
        return clean
    cut = clean[:max_chars].rstrip()
    for sep in (". ", ", ", "; ", " "):
        idx = cut.rfind(sep)
        if idx >= max_chars * 0.65:
            cut = cut[:idx]
            break
    return cut.rstrip(" ,;:.") + "."


def _limit_video_prompt(text: str) -> str:
    """VEO 3.1 rejects prompts above 1000 chars; keep margin for safety."""
    return _clip_text(text, _VEO_PROMPT_MAX_CHARS)


def _deep_get(data, keys: list[str], default=""):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def _first_value(data: dict, paths: list[list[str]], default=""):
    for path in paths:
        value = _deep_get(data, path, None)
        if value not in (None, "", [], {}):
            return value
    return default


def _coerce_number(value, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    multiplier = 1.0
    if text.lower().endswith("k"):
        multiplier = 1_000.0
        text = text[:-1]
    elif text.lower().endswith("m"):
        multiplier = 1_000_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return default


def _extract_apify_items(raw_json: str) -> list[dict]:
    raw_json = (raw_json or "").strip()
    if not raw_json:
        return []
    data = json.loads(raw_json)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "results", "videos"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_apify_notice(raw_json: str) -> str:
    raw_json = (raw_json or "").strip()
    if not raw_json:
        return ""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        return str(data.get("notice") or data.get("error") or "").strip()
    return ""


def _tokenize_topic(text: str) -> list[str]:
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "you", "your", "are", "was", "were",
        "как", "это", "что", "для", "или", "его", "она", "они", "про", "без", "все", "ещё",
        "when", "then", "just", "into", "over", "video", "short", "reel", "tiktok", "youtube",
    }
    words = re.findall(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9_-]{2,}", text.lower())
    return [word for word in words if word not in stop and not word.isdigit()]


def _normalize_video_item(item: dict) -> dict:
    text = str(_first_value(item, [
        ["text"], ["caption"], ["description"], ["desc"], ["title"], ["videoMeta", "description"],
    ], "") or "")
    author = str(_first_value(item, [
        ["authorMeta", "name"], ["author", "nickname"], ["author", "uniqueId"], ["channelName"], ["ownerUsername"],
    ], "") or "")
    url = str(_first_value(item, [["webVideoUrl"], ["url"], ["videoUrl"], ["link"], ["shareUrl"]], "") or "")
    views = _coerce_number(_first_value(item, [
        ["playCount"], ["views"], ["viewCount"], ["stats", "playCount"], ["videoMeta", "playCount"],
    ]))
    likes = _coerce_number(_first_value(item, [["diggCount"], ["likes"], ["likeCount"], ["stats", "diggCount"]]))
    comments = _coerce_number(_first_value(item, [["commentCount"], ["comments"], ["stats", "commentCount"]]))
    shares = _coerce_number(_first_value(item, [["shareCount"], ["shares"], ["stats", "shareCount"]]))
    duration = _coerce_number(_first_value(item, [["duration"], ["videoMeta", "duration"], ["lengthSeconds"]]))
    hashtags_raw = _first_value(item, [["hashtags"], ["challenges"], ["tags"]], [])
    hashtags = []
    if isinstance(hashtags_raw, list):
        for tag in hashtags_raw:
            if isinstance(tag, dict):
                value = tag.get("name") or tag.get("title") or tag.get("hashtag")
            else:
                value = tag
            if value:
                hashtags.append(str(value).strip("# "))
    hashtags.extend(re.findall(r"#([A-Za-zА-Яа-я0-9_]+)", text))
    return {
        "text": text.strip(),
        "author": author.strip(),
        "url": url.strip(),
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "duration": duration,
        "hashtags": sorted(set(tag for tag in hashtags if tag)),
        "raw": item,
    }


def _ai_feasibility_score(video: dict) -> tuple[int, list[str], list[str]]:
    text = f"{video['text']} {' '.join(video['hashtags'])}".lower()
    easy = []
    hard = []
    easy_terms = {
        "pov": "POV/inner monologue is easy to stage",
        "story": "story format maps cleanly to 6 scenes",
        "animation": "animation/fiction fits AI generation",
        "ai": "AI-native visual language",
        "minecraft": "blocky game-like style is easy to translate safely",
        "game": "stylized game worlds are easy to generate",
        "satisfying": "satisfying transformation loops well",
        "before": "before/after gives clear visual contrast",
        "after": "before/after gives clear visual contrast",
        "трансформа": "transformation arc is strong for AI video",
        "история": "story format maps cleanly to 6 scenes",
    }
    hard_terms = {
        "celebrity": "real/celebrity likeness risk",
        "interview": "talking-head realism is harder",
        "prank": "requires real social context",
        "dance": "precise choreography is harder",
        "sport": "fast precise body physics is harder",
        "news": "current real-world references age quickly",
        "полит": "politics/religion risk",
    }
    for term, reason in easy_terms.items():
        if term in text:
            easy.append(reason)
    for term, reason in hard_terms.items():
        if term in text:
            hard.append(reason)
    if video["duration"] and 12 <= video["duration"] <= 55:
        easy.append("duration already matches Shorts/Reels")
    score = 55 + min(30, len(easy) * 7) - min(35, len(hard) * 12)
    if video["views"] >= 500_000:
        score += 5
    if video["shares"] >= 5_000:
        score += 5
    return max(0, min(100, score)), sorted(set(easy)), sorted(set(hard))


def _format_trend_brief(items: list[dict], source_label: str, target_language: str, max_examples: int) -> tuple[str, str]:
    normalized = [_normalize_video_item(item) for item in items]
    normalized = [item for item in normalized if item["text"] or item["hashtags"]]
    if not normalized:
        return _empty_trend_brief(
            "No usable Apify video items found. Check that the dataset contains captions, titles, descriptions, or hashtags.",
            source_label,
            target_language,
        )

    topic_counter = Counter()
    hashtag_counter = Counter()
    clusters: dict[str, list[dict]] = defaultdict(list)

    for video in normalized:
        text_blob = f"{video['text']} {' '.join(video['hashtags'])}"
        topic_counter.update(_tokenize_topic(text_blob))
        hashtag_counter.update(tag.lower() for tag in video["hashtags"])
        key_terms = _tokenize_topic(text_blob)[:5] or ["misc"]
        key = " / ".join(key_terms[:3])
        clusters[key].append(video)

    ranked_videos = []
    for video in normalized:
        engagement = video["likes"] + 2 * video["comments"] + 3 * video["shares"]
        rate = engagement / max(video["views"], 1.0)
        feasibility, easy, hard = _ai_feasibility_score(video)
        trend_score = min(100, video["views"] / 25_000 + rate * 700 + feasibility * 0.45)
        ranked_videos.append((trend_score, feasibility, easy, hard, video))
    ranked_videos.sort(key=lambda row: row[0], reverse=True)

    cluster_rows = []
    for name, videos in clusters.items():
        views = sum(v["views"] for v in videos)
        engagement = sum(v["likes"] + 2 * v["comments"] + 3 * v["shares"] for v in videos)
        feasibility_values = [_ai_feasibility_score(v)[0] for v in videos]
        cluster_rows.append((views + engagement * 8, name, videos, sum(feasibility_values) / len(feasibility_values)))
    cluster_rows.sort(reverse=True)

    lines = [
        "APIFY TREND INTELLIGENCE",
        f"Source: {source_label or 'Apify dataset'}",
        f"Videos analyzed: {len(normalized)}",
        f"Target output language: {target_language}",
        "",
        "MOST RELEVANT THEMES",
    ]
    for idx, (_, name, videos, feasibility_avg) in enumerate(cluster_rows[:8], start=1):
        examples = "; ".join(_clip_text(v["text"], 90) for v in videos[:2] if v["text"])
        lines.append(f"{idx}. {name} | videos: {len(videos)} | views: {int(sum(v['views'] for v in videos))} | AI feasibility: {feasibility_avg:.0f}/100")
        if examples:
            lines.append(f"   Evidence: {examples}")

    lines.extend(["", "TOP HASHTAGS"])
    lines.append(", ".join(f"#{tag}" for tag, _ in hashtag_counter.most_common(18)) or "No hashtags found")

    lines.extend(["", "BEST SOURCE EXAMPLES"])
    for idx, (trend_score, feasibility, easy, hard, video) in enumerate(ranked_videos[:max_examples], start=1):
        lines.append(f"{idx}. score {trend_score:.0f}/100 | AI feasibility {feasibility}/100 | views {int(video['views'])} | shares {int(video['shares'])}")
        if video["text"]:
            lines.append(f"   Caption: {_clip_text(video['text'], 180)}")
        if video["hashtags"]:
            lines.append(f"   Tags: {', '.join('#' + tag for tag in video['hashtags'][:10])}")
        if easy:
            lines.append(f"   Easy for AI: {'; '.join(easy[:3])}")
        if hard:
            lines.append(f"   Watch-outs: {'; '.join(hard[:3])}")
        if video["url"]:
            lines.append(f"   URL: {video['url']}")

    top_terms = ", ".join(word for word, _ in topic_counter.most_common(25))
    top_cluster = cluster_rows[0][1] if cluster_rows else "high-retention short-form story"
    seed = (
        "Use the following trend intelligence as the market research layer for create-scenario.\n\n"
        f"Relevant topic cluster to prioritize: {top_cluster}\n"
        f"High-signal terms: {top_terms}\n\n"
        + "\n".join(lines)
        + "\n\nCreate a complete, production-ready 6-scene scenario that is inspired by the patterns, "
        "not copied from any one video. Prioritize concepts that are easy to generate with AI video: "
        "stylized characters, clear transformations, one protagonist, one signature object, simple locations, "
        "strong visual causality, no real people, no brand names, no logos."
    )
    return "\n".join(lines), seed


def _empty_trend_brief(notice: str, source_label: str, target_language: str) -> tuple[str, str]:
    reason = notice or "No usable Apify video items found. Check dataset contents and actor output fields."
    brief = (
        "APIFY TREND INTELLIGENCE\n"
        f"Source: {source_label or 'Apify dataset'}\n"
        f"Target output language: {target_language}\n"
        "Videos analyzed: 0\n\n"
        f"Status: {reason}\n\n"
        "Next step: paste a finished Apify dataset URL/id or actor run URL/id into Apify Dataset Fetch. "
        "If you paste a run id, enable treat_as_run_id unless the URL already contains /runs/."
    )
    seed = (
        brief
        + "\n\nNo trend data is available yet. Create a conservative original short-form concept from the user's manual idea "
        "or ask for Apify data before final production. Keep it AI-producible: one protagonist, one signature object, "
        "stylized visuals, simple locations, strong transformation, no real people or brands."
    )
    return brief, seed


class ApifyDatasetFetchNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ApifyDatasetFetchNode",
            display_name="Apify Dataset Fetch",
            category="viral_pipeline/research",
            description="Fetch recent video items from an Apify dataset URL, dataset id, or actor run id.",
            inputs=[
                IO.String.Input("api_token", multiline=False, default="", tooltip="Apify API token."),
                IO.String.Input("dataset_or_run", multiline=False, default="", tooltip="Dataset id, run id, or Apify dataset/run URL."),
                IO.Int.Input("limit", default=80, min=1, max=1000, tooltip="Maximum items to fetch."),
                IO.Boolean.Input("treat_as_run_id", default=False, tooltip="Enable when dataset_or_run is an actor run id."),
            ],
            outputs=[IO.String.Output("raw_json", display_name="raw_json")],
        )

    @staticmethod
    def _extract_id(value: str) -> tuple[str, bool]:
        text = value.strip()
        parsed = urlparse(text)
        if parsed.netloc:
            path = [part for part in parsed.path.split("/") if part]
            qs = parse_qs(parsed.query)
            
            # Check query params for explicit overrides first
            for key in ("runId", "actorRunId"):
                if key in qs:
                    return qs[key][0], True
            for key in ("datasetId", "defaultDatasetId"):
                if key in qs:
                    return qs[key][0], False
                    
            # Check path segments. If "/runs/" or "/actor-runs/" is anywhere in the path, it is a run.
            for idx, part in enumerate(path):
                if part in {"runs", "run", "actor-runs", "actor-run"} and idx + 1 < len(path):
                    # Strip any query parameters or anchors just in case
                    clean_id = path[idx + 1].split("?")[0].split("#")[0]
                    return clean_id, True
                    
            for idx, part in enumerate(path):
                if part in {"datasets", "dataset"} and idx + 1 < len(path):
                    clean_id = path[idx + 1].split("?")[0].split("#")[0]
                    return clean_id, False
                if part in {"storage"} and idx + 2 < len(path) and path[idx + 1] in {"datasets", "dataset"}:
                    clean_id = path[idx + 2].split("?")[0].split("#")[0]
                    return clean_id, False
        return text, False

    @classmethod
    async def execute(cls, api_token: str, dataset_or_run: str, limit: int, treat_as_run_id: bool) -> IO.NodeOutput:
        source_text = (dataset_or_run or "").strip()
        if not source_text or source_text == "PASTE_APIFY_DATASET_OR_RUN_URL_HERE":
            return IO.NodeOutput(json.dumps({
                "items": [],
                "notice": (
                    "Apify source is not configured. Paste a dataset URL/id or actor run URL/id "
                    "into Apify Dataset Fetch."
                ),
            }))
        validate_string(api_token, strip_whitespace=True, min_length=1)
        item_id, is_run_url = cls._extract_id(dataset_or_run)
        validate_string(item_id, strip_whitespace=True, min_length=1)
        is_run = bool(treat_as_run_id or is_run_url)
        headers = {"Authorization": f"Bearer {api_token.strip()}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            dataset_id = item_id
            if is_run:
                run_url = f"https://api.apify.com/v2/actor-runs/{item_id}"
                async with session.get(run_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        return IO.NodeOutput(json.dumps({
                            "items": [],
                            "notice": f"Apify run lookup failed {resp.status}: {await resp.text()}",
                        }))
                    payload = await resp.json()
                    dataset_id = _deep_get(payload, ["data", "defaultDatasetId"], "")
                    if not dataset_id:
                        return IO.NodeOutput(json.dumps({
                            "items": [],
                            "notice": "Apify run has no defaultDatasetId yet. Wait for the actor run to finish.",
                        }))
            url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
            params = {"clean": "true", "format": "json", "limit": str(int(limit))}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    return IO.NodeOutput(json.dumps({
                        "items": [],
                        "notice": f"Apify dataset fetch failed {resp.status}: {await resp.text()}",
                    }))
                text = await resp.text()
        return IO.NodeOutput(text)


class ApifyTrendAnalyzerNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ApifyTrendAnalyzerNode",
            display_name="Apify Trend Analyzer",
            category="viral_pipeline/research",
            description=(
                "Rank Apify video data by relevance and AI-producibility, then produce "
                "a research brief plus a scenario seed prompt for create-scenario."
            ),
            inputs=[
                IO.String.Input("raw_json", multiline=True, default="", tooltip="Raw JSON array from Apify Dataset Fetch."),
                IO.Int.Input("max_examples", default=8, min=3, max=20),
                IO.String.Input("source_label", multiline=False, default="TikTok / Reels trends", optional=True),
                IO.String.Input("target_language", multiline=False, default="English", optional=True),
            ],
            outputs=[
                IO.String.Output("trend_brief", display_name="trend_brief"),
                IO.String.Output("scenario_seed", display_name="scenario_seed"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        raw_json: str,
        max_examples: int = 8,
        source_label: str = "TikTok / Reels trends",
        target_language: str = "English",
    ) -> IO.NodeOutput:
        items = _extract_apify_items(raw_json)
        if not items:
            brief, seed = _empty_trend_brief(_extract_apify_notice(raw_json), source_label, target_language)
            return IO.NodeOutput(brief, seed)
        brief, seed = _format_trend_brief(items, source_label, target_language, int(max_examples))
        return IO.NodeOutput(brief, seed)


def _parse_visual_style(script: str) -> str:
    return _parse_global_field(script, "VISUAL STYLE")


def _parse_setting_bible(script: str) -> str:
    return _parse_global_field(script, "SETTING BIBLE")


def _parse_signature_object(script: str) -> str:
    return _parse_global_field(script, "SIGNATURE OBJECT")


def _scene_action(script: str, scene_n: int) -> str:
    visual = _parse_field(script, scene_n, "Visual")
    if visual:
        return visual
    return ""


def _remove_leading_character_bible(prompt: str, character_bible: str) -> str:
    """Remove an exact or near-exact Character Bible pasted at prompt start."""
    if not prompt or not character_bible:
        return prompt

    variants = {
        character_bible.strip(),
        character_bible.strip().rstrip("."),
        character_bible.strip().rstrip(".,;:"),
    }
    repaired = prompt
    for variant in sorted(variants, key=len, reverse=True):
        if not variant:
            continue
        pattern = re.escape(variant).replace(r"\ ", r"\s+")
        new_value = re.sub(
            rf"^\s*{pattern}\s*[,.;:—–-]*\s*",
            "",
            repaired,
            count=1,
            flags=re.IGNORECASE,
        )
        if new_value != repaired:
            repaired = new_value
            break
    return repaired.strip()


def _repair_image_prompt(script: str, scene_n: int, prompt: str) -> str:
    """Force scene-specific action to the front and enrich weak image prompts."""
    prompt = " ".join((prompt or "").strip().split())
    if not prompt:
        return prompt

    character_bible = _parse_global_field(script, "CHARACTER BIBLE")
    visual = _scene_action(script, scene_n)
    continuity = _parse_field(script, scene_n, "Continuity")
    setting = _parse_setting_bible(script)
    style = _parse_visual_style(script)
    signature_object = _parse_signature_object(script)

    without_bible = _remove_leading_character_bible(prompt, character_bible)
    without_bible = without_bible.rstrip(" ,;:.")
    short_bible = _short_character_bible(character_bible)

    # Put the scene action first because image models overweight early tokens.
    parts = [
        visual,
        f"continuity cue: {continuity}" if continuity and continuity.upper() != "OPENING" else "",
        without_bible,
        short_bible,
        f"signature object visible and scene-appropriate: {signature_object}" if signature_object else "",
        f"setting: {setting}" if setting else "",
        f"visual style: {style}" if style else "",
        (
            "single coherent cinematic frame, clear subject-action relationship, "
            "specific props and background elements matching this scene, no generic portrait, "
            "no unrelated fashion-stage imagery, high detail, 16:9 composition"
        ),
    ]
    enriched = _dedupe_join(parts)

    # If the model gave us almost nothing, at least fall back to the original prompt.
    return enriched or prompt


def _repair_video_prompt(script: str, scene_n: int, prompt: str) -> str:
    """Pass the LLM-written video prompt through with minimal touching.

    The LLM is instructed to emit a strict START/ACTION/END/CAMERA/ATMOSPHERE
    block. An I2V model already gets character, setting, and style from the
    keyframe image — re-injecting Character Bible / Setting Bible / Visual
    Style here only dilutes attention away from the one thing text controls:
    the motion. Boilerplate cinematic phrases ("parallax", "particles",
    "emotional beat") were doing the same and the video model often
    interpreted them literally as the action.

    Only fall back to enrichment if the LLM emitted nothing for this scene.
    """
    prompt = " ".join((prompt or "").strip().split())
    if prompt:
        return _limit_video_prompt(prompt)

    visual = _scene_action(script, scene_n)
    signature_object = _parse_signature_object(script)
    parts = [
        visual,
        f"signature object visible: {signature_object}" if signature_object else "",
    ]
    return _limit_video_prompt(_dedupe_join(parts))


class SceneParserNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SceneParserNode",
            display_name="Scene Parser (6 scenes)",
            category="viral_pipeline/text",
            description=(
                "Parse a structured 6-scene script into individual image prompts, "
                "video prompts, and voiceover lines. "
                "Expected format per scene: Visual / Image Prompt / Video Prompt / Voiceover "
                "blocks separated by ---."
            ),
            inputs=[
                IO.String.Input(
                    "script",
                    multiline=True,
                    default="",
                    tooltip="Full structured script from the script-writing Gemini node.",
                ),
            ],
            outputs=[
                # Scene 1
                IO.String.Output(display_name="image_prompt_1"),
                IO.String.Output(display_name="video_prompt_1"),
                IO.String.Output(display_name="voiceover_1"),
                # Scene 2
                IO.String.Output(display_name="image_prompt_2"),
                IO.String.Output(display_name="video_prompt_2"),
                IO.String.Output(display_name="voiceover_2"),
                # Scene 3
                IO.String.Output(display_name="image_prompt_3"),
                IO.String.Output(display_name="video_prompt_3"),
                IO.String.Output(display_name="voiceover_3"),
                # Scene 4
                IO.String.Output(display_name="image_prompt_4"),
                IO.String.Output(display_name="video_prompt_4"),
                IO.String.Output(display_name="voiceover_4"),
                # Scene 5
                IO.String.Output(display_name="image_prompt_5"),
                IO.String.Output(display_name="video_prompt_5"),
                IO.String.Output(display_name="voiceover_5"),
                # Scene 6
                IO.String.Output(display_name="image_prompt_6"),
                IO.String.Output(display_name="video_prompt_6"),
                IO.String.Output(display_name="voiceover_6"),
            ],
        )

    @classmethod
    async def execute(cls, script: str) -> IO.NodeOutput:
        results = []
        for n in range(1, _MAX_SCENES + 1):
            image_prompt = _parse_field(script, n, "Image Prompt")
            results.append(_repair_image_prompt(script, n, image_prompt))
            video_prompt = _parse_field(script, n, "Video Prompt")
            results.append(_repair_video_prompt(script, n, video_prompt))
            results.append(_parse_field(script, n, "Voiceover"))
        return IO.NodeOutput(*results)


def _parse_voiceover_rewrite(voiceovers: str) -> list[str]:
    """Parse six rewritten voiceover lines from either numbered lines or scene blocks."""
    lines: list[str] = []
    for n in range(1, _MAX_SCENES + 1):
        value = _parse_field(voiceovers, n, "Voiceover")
        if not value:
            patterns = [
                rf"^\s*(?:Voiceover\s*)?{n}\s*[\:\-\.\)]\s*(.+)$",
                rf"^\s*Scene\s*{n}\s*(?:Voiceover)?\s*[\:\-]\s*(.+)$",
            ]
            for pattern in patterns:
                match = re.search(pattern, voiceovers, re.IGNORECASE | re.MULTILINE)
                if match:
                    value = match.group(1).strip()
                    break
        value = re.sub(r"^\s*Voiceover\s*:\s*", "", value, flags=re.IGNORECASE).strip()
        value = value.strip(" \t-–—\"'[]")
        lines.append(value)
    return lines


def _replace_scene_voiceover(script: str, scene_n: int, voiceover: str) -> str:
    block_match = re.search(
        rf"SCENE\s+{scene_n}\s*:.*?(?=SCENE\s+{scene_n + 1}\s*:|$)",
        script,
        re.DOTALL | re.IGNORECASE,
    )
    if not block_match:
        return script

    block = block_match.group(0)
    voice_line = re.compile(
        r"(^\s*Voiceover(?:\s*[\(\[\-\—\–][^:\n]*)?\s*:\s*).+$",
        re.IGNORECASE | re.MULTILINE,
    )
    if voice_line.search(block):
        new_block = voice_line.sub(rf"\1{voiceover}", block, count=1)
    else:
        new_block = block.rstrip() + f"\n\nVoiceover: {voiceover}\n"
    return script[:block_match.start()] + new_block + script[block_match.end():]


class VoiceoverRewriteApplyNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="VoiceoverRewriteApplyNode",
            display_name="Apply Voiceover Rewrite",
            category="viral_pipeline/text",
            description=(
                "Takes the original 6-scene script plus a six-line voiceover rewrite "
                "and returns the same script with only Voiceover fields replaced."
            ),
            inputs=[
                IO.String.Input("script", multiline=True, default="", tooltip="Original full 6-scene script."),
                IO.String.Input(
                    "voiceovers",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip="Six rewritten lines: Voiceover 1: ..., Voiceover 2: ..., etc.",
                ),
                IO.String.Input("voiceover_1", multiline=True, default="", optional=True, tooltip="Optional replacement for scene 1 only."),
                IO.String.Input("voiceover_2", multiline=True, default="", optional=True, tooltip="Optional replacement for scene 2 only."),
                IO.String.Input("voiceover_3", multiline=True, default="", optional=True, tooltip="Optional replacement for scene 3 only."),
                IO.String.Input("voiceover_4", multiline=True, default="", optional=True, tooltip="Optional replacement for scene 4 only."),
                IO.String.Input("voiceover_5", multiline=True, default="", optional=True, tooltip="Optional replacement for scene 5 only."),
                IO.String.Input("voiceover_6", multiline=True, default="", optional=True, tooltip="Optional replacement for scene 6 only."),
            ],
            outputs=[IO.String.Output(display_name="script_with_new_voiceovers")],
        )

    @classmethod
    async def execute(
        cls,
        script: str,
        voiceovers: str = "",
        voiceover_1: str = "",
        voiceover_2: str = "",
        voiceover_3: str = "",
        voiceover_4: str = "",
        voiceover_5: str = "",
        voiceover_6: str = "",
    ) -> IO.NodeOutput:
        parsed = _parse_voiceover_rewrite(voiceovers) if voiceovers.strip() else [""] * _MAX_SCENES
        per_scene = [voiceover_1, voiceover_2, voiceover_3, voiceover_4, voiceover_5, voiceover_6]
        for idx, line in enumerate(per_scene):
            if line and line.strip():
                parsed[idx] = line.strip().strip(" \t-–—\"'[]")

        missing = [str(i + 1) for i, line in enumerate(parsed) if not line]
        if len(missing) == _MAX_SCENES:
            raise ValueError(
                "VoiceoverRewriteApply: provide either a six-line voiceovers block "
                "or at least one per-scene voiceover input."
            )
        if voiceovers.strip() and missing:
            raise ValueError(
                "VoiceoverRewriteApply: missing rewritten voiceover lines for scenes "
                + ", ".join(missing)
                + ". Expected 'Voiceover 1: ...' through 'Voiceover 6: ...'."
            )

        rewritten = script
        for idx, line in enumerate(parsed, start=1):
            if line:
                rewritten = _replace_scene_voiceover(rewritten, idx, line)
        return IO.NodeOutput(rewritten)


class AudioConcatNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="AudioConcatNode",
            display_name="Audio Concat (up to 6)",
            category="viral_pipeline/audio",
            description=(
                "Concatenate up to 6 audio clips in order (scene 1 → scene 6). "
                "Skips any unconnected inputs. All clips are resampled to the "
                "sample rate of the first connected clip."
            ),
            inputs=[
                IO.Audio.Input("audio_1", optional=True, tooltip="Scene 1 voiceover."),
                IO.Audio.Input("audio_2", optional=True, tooltip="Scene 2 voiceover."),
                IO.Audio.Input("audio_3", optional=True, tooltip="Scene 3 voiceover."),
                IO.Audio.Input("audio_4", optional=True, tooltip="Scene 4 voiceover."),
                IO.Audio.Input("audio_5", optional=True, tooltip="Scene 5 voiceover."),
                IO.Audio.Input("audio_6", optional=True, tooltip="Scene 6 voiceover."),
            ],
            outputs=[IO.Audio.Output()],
        )

    @classmethod
    async def execute(
        cls,
        audio_1=None,
        audio_2=None,
        audio_3=None,
        audio_4=None,
        audio_5=None,
        audio_6=None,
    ) -> IO.NodeOutput:
        clips = [a for a in [audio_1, audio_2, audio_3, audio_4, audio_5, audio_6] if a is not None]
        if not clips:
            raise ValueError("AudioConcatNode: at least one audio input must be connected.")

        sample_rate: int = clips[0]["sample_rate"]
        waveforms = []
        for clip in clips:
            wf = clip["waveform"]
            # Ensure shape [1, channels, samples]; broadcast mono if needed
            if wf.dim() == 2:
                wf = wf.unsqueeze(0)
            # Normalise to [1, 1, samples] (mix to mono if multi-channel)
            if wf.shape[1] > 1:
                wf = wf.mean(dim=1, keepdim=True)
            waveforms.append(wf)

        combined = torch.cat(waveforms, dim=-1)
        return IO.NodeOutput({"waveform": combined, "sample_rate": sample_rate})


_EL_CONCURRENCY: asyncio.Semaphore | None = None
_EL_CONCURRENCY_LOOP: asyncio.AbstractEventLoop | None = None
_EL_MAX_RETRIES = 5
_EL_RETRY_STATUSES = {409, 429, 500, 502, 503, 504}


def _el_semaphore() -> asyncio.Semaphore:
    global _EL_CONCURRENCY, _EL_CONCURRENCY_LOOP
    loop = asyncio.get_running_loop()
    if _EL_CONCURRENCY is None or _EL_CONCURRENCY_LOOP is not loop:
        # Custom ElevenLabs voices can conflict when multiple requests for the
        # same voice run concurrently. Keep TTS serial; AudioCache prevents
        # repeat calls after the first successful fill.
        _EL_CONCURRENCY = asyncio.Semaphore(1)
        _EL_CONCURRENCY_LOOP = loop
    return _EL_CONCURRENCY

_EL_API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

_EL_VOICES = [
    ("Rachel", "21m00Tcm4TlvDq8ikWAM"),
    ("Charlie", "IKne3meq5aSn9XLyUdCD"),
    ("George", "JBFqnCBsd6RMkjVDRZzb"),
    ("Callum", "N2lVS1w4EtoT3dr4eOWO"),
    ("River", "SAz9YHcvj6GT2YYXdXww"),
    ("Harry", "SOYHLrjzK2X1ezoPC6cr"),
    ("Liam", "TX3LPaxmHKxFdv7VOQHJ"),
    ("Alice", "Xb7hH8MSUJpSbSDYk0k2"),
    ("Matilda", "XrExE9yKIg1WjnnlVkGX"),
    ("Will", "bIHbv24MWmeRgasZH58o"),
    ("Jessica", "cgSgspJ2msm6clMCkdW9"),
    ("Eric", "cjVigY5qzO86Huf0OWal"),
    ("Chris", "iP95p4xoKVk53GoZ742B"),
    ("Brian", "nPczCjzI2devNBz1zQrb"),
    ("Daniel", "onwK4e9ZLuTAKqWW03F9"),
    ("Lily", "pFZP5JQG7iQjIQuC4Bku"),
    ("Sarah", "EXAVITQu4vr4xnSDxMaL"),
    ("Laura", "FGY2WhTYpPnrIDTdsKH5"),
    ("Roger", "CwhRBWXzGAHq8TQ4Fs17"),
    ("Bill", "pqHfZKP75CvOlQylNhV4"),
]
_EL_VOICE_NAMES = [name for name, _ in _EL_VOICES]
_EL_VOICE_IDS = {name: vid for name, vid in _EL_VOICES}


class ElevenLabsTTSDirectNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ElevenLabsTTSDirectNode",
            display_name="ElevenLabs TTS (Direct API)",
            category="viral_pipeline/audio",
            description=(
                "Text-to-speech via ElevenLabs using your own API key. "
                "Get a key at elevenlabs.io/app/settings/api-keys."
            ),
            inputs=[
                IO.String.Input(
                    "api_key",
                    multiline=False,
                    default="",
                    tooltip="Your ElevenLabs API key. Keep this private!",
                ),
                IO.String.Input(
                    "text",
                    multiline=True,
                    default="",
                    tooltip="Text to convert to speech.",
                ),
                IO.Combo.Input(
                    "voice",
                    options=_EL_VOICE_NAMES,
                    default="Rachel",
                    tooltip="Voice to use. For custom voice ID, use the 'custom_voice_id' field.",
                ),
                IO.Combo.Input(
                    "model",
                    options=["eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5", "eleven_turbo_v2_5"],
                    default="eleven_multilingual_v2",
                    tooltip="TTS model.",
                ),
                IO.Float.Input(
                    "stability",
                    default=0.5, min=0.0, max=1.0, step=0.01,
                    tooltip="Voice stability (0=expressive, 1=stable).",
                ),
                IO.Float.Input(
                    "similarity_boost",
                    default=0.75, min=0.0, max=1.0, step=0.01,
                    tooltip="Similarity to original voice.",
                    advanced=True,
                ),
                IO.Float.Input(
                    "style",
                    default=0.0, min=0.0, max=1.0, step=0.01,
                    tooltip="Style exaggeration (0=neutral).",
                    advanced=True,
                ),
                IO.Float.Input(
                    "speed",
                    default=1.0, min=0.7, max=1.3, step=0.01,
                    tooltip="Speech speed (1.0 = normal).",
                    advanced=True,
                ),
                IO.String.Input(
                    "language_code",
                    default="",
                    tooltip="ISO language code, e.g. 'en', 'ru'. Leave empty for auto-detect.",
                    advanced=True,
                ),
                IO.String.Input(
                    "custom_voice_id",
                    default="",
                    tooltip="Overrides voice combo. Paste any ElevenLabs voice ID here.",
                    advanced=True,
                ),
                IO.Combo.Input(
                    "output_format",
                    options=[
                        "mp3_44100_192",
                        "mp3_44100_128",
                        "mp3_44100_64",
                        "mp3_44100_32",
                        "pcm_44100",
                    ],
                    default="mp3_44100_192",
                    tooltip=(
                        "Audio output format. mp3_44100_192/128 and pcm_44100 require Creator tier or above. "
                        "mp3_44100_64/32 work on Starter and above."
                    ),
                    advanced=True,
                ),
            ],
            outputs=[IO.Audio.Output()],
        )

    @classmethod
    async def execute(
        cls,
        api_key: str,
        text: str,
        voice: str,
        model: str,
        stability: float,
        similarity_boost: float,
        style: float,
        speed: float,
        language_code: str = "",
        custom_voice_id: str = "",
        output_format: str = "mp3_44100_192",
    ) -> IO.NodeOutput:
        validate_string(api_key, strip_whitespace=True, min_length=1, field_name="api_key")
        if not text.strip():
            raise ValueError("ElevenLabs TTS: text is empty — check the SceneParser voiceover output.")

        voice_id = custom_voice_id.strip() or _EL_VOICE_IDS.get(voice, _EL_VOICE_IDS["Rachel"])

        body = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity_boost,
                "style": style,
                "speed": speed,
            },
            "apply_text_normalization": "auto",
        }
        lang = language_code.strip()
        if lang:
            # ElevenLabs expects ISO 639-1 codes (2 letters, e.g. "ru", "en", "de").
            # Common mistake: typing the full language name like "russian" instead of "ru".
            _LANG_NAMES = {
                "russian": "ru", "english": "en", "spanish": "es", "french": "fr",
                "german": "de", "italian": "it", "portuguese": "pt", "chinese": "zh",
                "japanese": "ja", "korean": "ko", "arabic": "ar", "hindi": "hi",
                "turkish": "tr", "polish": "pl", "dutch": "nl", "swedish": "sv",
            }
            lang = _LANG_NAMES.get(lang.lower(), lang)
            body["language_code"] = lang

        url = _EL_API_URL.format(voice_id=voice_id)
        headers = {
            "xi-api-key": api_key.strip(),
            "Content-Type": "application/json",
        }
        async with _el_semaphore():
            async with aiohttp.ClientSession() as session:
                last_error = ""
                for attempt in range(_EL_MAX_RETRIES + 1):
                    async with session.post(
                        url,
                        json=body,
                        headers=headers,
                        params={"output_format": output_format},
                        timeout=aiohttp.ClientTimeout(total=90),
                    ) as resp:
                        if resp.status == 200:
                            audio_bytes = await resp.read()
                            break

                        err = await resp.text()
                        last_error = f"ElevenLabs TTS error {resp.status}: {err}"
                        should_retry = resp.status in _EL_RETRY_STATUSES
                        if not should_retry or attempt >= _EL_MAX_RETRIES:
                            raise Exception(last_error)

                        wait_s = min(2.0 * (attempt + 1), 10.0)
                        print(
                            f"[ElevenLabsTTS] retrying after {resp.status} in {wait_s:.1f}s "
                            f"(attempt {attempt + 1}/{_EL_MAX_RETRIES})",
                            flush=True,
                        )
                    await asyncio.sleep(wait_s)
                else:
                    raise Exception(last_error or "ElevenLabs TTS failed without a response.")

        log_usage("audio", cache_hit=False, model=model, chars=len(text))
        return IO.NodeOutput(audio_bytes_to_audio_input(audio_bytes))


class StringGateNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="StringGateNode",
            display_name="Pipeline Gate — Text",
            category="viral_pipeline/control",
            description=(
                "Passes text through only when 'approved' is enabled. "
                "Disable to pause the pipeline here; review the preview above, "
                "then enable and re-run to continue."
            ),
            inputs=[
                IO.String.Input("text", multiline=True, default=""),
                IO.Boolean.Input(
                    "approved",
                    default=True,
                    tooltip="Enable to let this stage proceed. Disable to pause here.",
                ),
            ],
            outputs=[IO.String.Output()],
        )

    @classmethod
    async def execute(cls, text: str, approved: bool) -> IO.NodeOutput:
        if not approved:
            raise RuntimeError(
                "Pipeline paused at text stage — review the script above, "
                "then check 'approved' and re-run."
            )
        return IO.NodeOutput(text)


class VideoConcatFFmpegNode(IO.ComfyNode):
    """Merge up to 6 video clips into a single MP4 via ffmpeg.

    Uses `ffmpeg -f concat -c copy` so video streams are concatenated WITHOUT
    re-decoding/re-encoding — the operation takes seconds and uses minimal disk
    space (only the final output is written). Falls back to re-encoding if
    stream-copy fails (codec/dimension mismatch).

    This replaces VHS_VideoCombine for the final merge step, which decoded all
    clips into frames in RAM and produced multi-GB intermediate files that
    exhaust the disk on a 16GB Mac.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="VideoConcatFFmpegNode",
            display_name="Video Concat (ffmpeg stream copy)",
            category="viral_pipeline/video",
            description=(
                "Concatenate up to 6 video clips into one MP4 using ffmpeg stream copy. "
                "Optionally attaches scene-specific audio clips before the final merge. "
                "Fast and disk-light — replaces VHS_VideoCombine for the merge step."
            ),
            inputs=[
                IO.Video.Input("video_1", optional=True, tooltip="Scene 1 video."),
                IO.Video.Input("video_2", optional=True, tooltip="Scene 2 video."),
                IO.Video.Input("video_3", optional=True, tooltip="Scene 3 video."),
                IO.Video.Input("video_4", optional=True, tooltip="Scene 4 video."),
                IO.Video.Input("video_5", optional=True, tooltip="Scene 5 video."),
                IO.Video.Input("video_6", optional=True, tooltip="Scene 6 video."),
                IO.Audio.Input("audio_1", optional=True, tooltip="Scene 1 voiceover. Padded/trimmed to video_1 duration."),
                IO.Audio.Input("audio_2", optional=True, tooltip="Scene 2 voiceover. Padded/trimmed to video_2 duration."),
                IO.Audio.Input("audio_3", optional=True, tooltip="Scene 3 voiceover. Padded/trimmed to video_3 duration."),
                IO.Audio.Input("audio_4", optional=True, tooltip="Scene 4 voiceover. Padded/trimmed to video_4 duration."),
                IO.Audio.Input("audio_5", optional=True, tooltip="Scene 5 voiceover. Padded/trimmed to video_5 duration."),
                IO.Audio.Input("audio_6", optional=True, tooltip="Scene 6 voiceover. Padded/trimmed to video_6 duration."),
                IO.Audio.Input(
                    "audio",
                    optional=True,
                    tooltip=(
                        "Legacy fallback: one already-concatenated audio track for the whole video. "
                        "Scene audio inputs above take priority when connected."
                    ),
                ),
                IO.String.Input(
                    "filename_prefix",
                    multiline=False,
                    default="viral_video_final",
                    tooltip="Output filename prefix in <ComfyUI>/output/. A counter is appended.",
                ),
                IO.Combo.Input(
                    "mode",
                    options=["stream copy (fast)", "re-encode (compatible)"],
                    default="stream copy (fast)",
                    tooltip=(
                        "stream copy = no decoding, seconds to merge, requires matching codecs (works for clips from one Poyo model).\n"
                        "re-encode = slower but works across mixed codecs/resolutions."
                    ),
                ),
                IO.Combo.Input(
                    "subtitles",
                    options=["off", "burned in only", "both (extra file with subs)"],
                    default="off",
                    tooltip=(
                        "off = no subtitles (default).\n"
                        "burned in only = subtitles permanently rendered into the single output file.\n"
                        "both = saves TWO files — one clean, one with burned-in subs (extra suffix _subs).\n"
                        "Uses ffmpeg locally. No API calls, no tokens spent."
                    ),
                ),
                IO.String.Input(
                    "subtitle_text_1", multiline=True, default="", optional=True,
                    tooltip="Voiceover text for scene 1 to render as subtitle. Leave empty to skip."),
                IO.String.Input("subtitle_text_2", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_3", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_4", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_5", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_6", multiline=True, default="", optional=True),
                IO.String.Input(
                    "subtitle_style",
                    multiline=False,
                    default="FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=40",
                    tooltip=(
                        "ASS/SRT force_style string. Default: white text, black outline, bottom-centered, ~40px from bottom. "
                        "Change FontSize / MarginV to taste; full reference: ffmpeg.org/ffmpeg-filters.html#subtitles"
                    ),
                ),
            ],
            outputs=[IO.Video.Output()],
        )

    @classmethod
    async def execute(
        cls,
        video_1=None, video_2=None, video_3=None,
        video_4=None, video_5=None, video_6=None,
        audio_1=None, audio_2=None, audio_3=None,
        audio_4=None, audio_5=None, audio_6=None,
        audio=None,
        filename_prefix: str = "viral_video_final",
        mode: str = "stream copy (fast)",
        subtitles: str = "off",
        subtitle_text_1: str = "", subtitle_text_2: str = "", subtitle_text_3: str = "",
        subtitle_text_4: str = "", subtitle_text_5: str = "", subtitle_text_6: str = "",
        subtitle_style: str = "",
    ) -> IO.NodeOutput:
        import io
        import os
        import shutil
        import subprocess
        import tempfile
        import folder_paths
        from comfy_api.latest import _input_impl as _ii

        ffmpeg_bin = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
        ffprobe_bin = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
        if not os.path.exists(ffmpeg_bin):
            ffmpeg_bin = "ffmpeg"
        if not os.path.exists(ffprobe_bin):
            ffprobe_bin = "ffprobe"

        scene_pairs = [
            (video_1, audio_1), (video_2, audio_2), (video_3, audio_3),
            (video_4, audio_4), (video_5, audio_5), (video_6, audio_6),
        ]
        connected_pairs = [(v, a) for v, a in scene_pairs if v is not None]
        if not connected_pairs:
            raise ValueError("VideoConcatFFmpeg: connect at least one video input.")

        out_dir = folder_paths.get_output_directory()
        os.makedirs(out_dir, exist_ok=True)
        full_folder, filename, counter, subfolder, fname_prefix = folder_paths.get_save_image_path(
            filename_prefix, out_dir
        )
        out_path = os.path.join(full_folder, f"{filename}_{counter:05d}.mp4")

        with tempfile.TemporaryDirectory() as tmpdir:
            def run_ffmpeg(cmd: list[str], label: str) -> None:
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode != 0:
                    raise RuntimeError(f"{label} failed:\n{proc.stderr}")

            def media_duration(path: str) -> float:
                proc = subprocess.run(
                    [
                        ffprobe_bin, "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        path,
                    ],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"ffprobe duration failed:\n{proc.stderr}")
                try:
                    return max(0.01, float(proc.stdout.strip()))
                except ValueError as exc:
                    raise RuntimeError(f"ffprobe returned invalid duration: {proc.stdout!r}") from exc

            def atempo_chain(speed_ratio: float) -> str:
                # atempo is safest in small-ish steps. Values >1 speed audio up.
                parts: list[str] = []
                ratio = max(0.5, speed_ratio)
                while ratio > 2.0:
                    parts.append("atempo=2.0")
                    ratio /= 2.0
                while ratio < 0.5:
                    parts.append("atempo=0.5")
                    ratio /= 0.5
                parts.append(f"atempo={ratio:.5f}")
                return ",".join(parts)

            def write_audio_wav(audio_obj, path: str) -> None:
                import wave
                import numpy as np

                waveform = audio_obj["waveform"]
                if waveform.dim() == 3:
                    waveform = waveform[0]
                waveform = waveform.detach().cpu()
                if waveform.dim() == 1:
                    waveform = waveform.unsqueeze(0)
                samples = waveform.transpose(0, 1).numpy()
                samples = np.clip(samples, -1.0, 1.0)
                pcm = (samples * 32767.0).astype(np.int16)
                with wave.open(path, "wb") as wav:
                    wav.setnchannels(pcm.shape[1])
                    wav.setsampwidth(2)
                    wav.setframerate(int(audio_obj["sample_rate"]))
                    wav.writeframes(pcm.tobytes())

            def write_silence_wav(path: str, duration: float, sample_rate: int = 44100) -> None:
                import wave

                frame_count = max(1, int(duration * sample_rate))
                with wave.open(path, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(sample_rate)
                    wav.writeframes(b"\x00\x00" * frame_count)

            # Stage each clip on disk — videos arriving as BytesIO get dumped first.
            clip_paths: list[str] = []
            clip_audios = []
            for idx, (vid, scene_audio) in enumerate(connected_pairs):
                src = vid.get_stream_source()
                if isinstance(src, str) and os.path.exists(src):
                    clip_paths.append(src)
                else:
                    p = os.path.join(tmpdir, f"clip_{idx}.mp4")
                    if isinstance(src, io.BytesIO):
                        src.seek(0)
                        with open(p, "wb") as f:
                            shutil.copyfileobj(src, f)
                    else:
                        # Fall back to the Video object's save_to API
                        vid.save_to(p)
                    clip_paths.append(p)
                clip_audios.append(scene_audio)

            scene_audio_connected = any(a is not None for a in clip_audios)
            if scene_audio_connected:
                muxed_clip_paths: list[str] = []
                for idx, (clip_path, scene_audio) in enumerate(zip(clip_paths, clip_audios, strict=False)):
                    duration = media_duration(clip_path)
                    audio_path = os.path.join(tmpdir, f"scene_audio_{idx}.wav")
                    if scene_audio is None:
                        write_silence_wav(audio_path, duration)
                        audio_filter = "apad"
                    else:
                        write_audio_wav(scene_audio, audio_path)
                        audio_duration = media_duration(audio_path)
                        target_audio_duration = max(0.5, duration - 0.2)
                        if audio_duration > target_audio_duration:
                            speed_ratio = audio_duration / target_audio_duration
                            audio_filter = f"{atempo_chain(speed_ratio)},apad"
                            print(
                                f"[VideoConcatFFmpeg] scene {idx + 1}: audio {audio_duration:.2f}s "
                                f"> target {target_audio_duration:.2f}s, speeding up x{speed_ratio:.2f}",
                                flush=True,
                            )
                        else:
                            audio_filter = "apad"
                    muxed_path = os.path.join(tmpdir, f"scene_muxed_{idx}.mp4")
                    scene_mux_cmd = [
                        ffmpeg_bin, "-y", "-loglevel", "error",
                        "-i", clip_path,
                        "-i", audio_path,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k",
                        "-af", audio_filter,
                        "-t", f"{duration:.3f}",
                        "-movflags", "+faststart",
                        muxed_path,
                    ]
                    try:
                        run_ffmpeg(scene_mux_cmd, f"ffmpeg scene {idx + 1} audio mux")
                    except RuntimeError:
                        # Some inputs do not tolerate stream-copy with -t; re-encode only that segment.
                        scene_mux_fallback = [
                            ffmpeg_bin, "-y", "-loglevel", "error",
                            "-i", clip_path,
                            "-i", audio_path,
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k",
                            "-af", audio_filter,
                            "-t", f"{duration:.3f}",
                            "-movflags", "+faststart",
                            muxed_path,
                        ]
                        run_ffmpeg(scene_mux_fallback, f"ffmpeg scene {idx + 1} audio mux fallback")
                    muxed_clip_paths.append(muxed_path)
                clip_paths = muxed_clip_paths

            # Build the concat-demuxer listing
            list_path = os.path.join(tmpdir, "concat.txt")
            with open(list_path, "w") as f:
                for p in clip_paths:
                    f.write(f"file '{p.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n")

            # Stage legacy whole-video audio if provided — write as 16-bit WAV.
            # Per-scene audio takes priority because it preserves scene timing.
            audio_path = None
            if audio is not None and not scene_audio_connected:
                audio_path = os.path.join(tmpdir, "audio.wav")
                write_audio_wav(audio, audio_path)

            # Merge step
            concat_only = os.path.join(tmpdir, "concat.mp4")
            use_copy = mode.startswith("stream")

            concat_cmd = [
                ffmpeg_bin, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
            ]
            if use_copy:
                concat_cmd += ["-c", "copy"]
            else:
                concat_cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k"]
            concat_cmd += [concat_only]

            proc = subprocess.run(concat_cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                if use_copy:
                    # Stream copy failed — auto-retry with re-encode so the run doesn't die.
                    fallback = [
                        ffmpeg_bin, "-y", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", list_path,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "192k",
                        concat_only,
                    ]
                    proc = subprocess.run(fallback, capture_output=True, text=True)
                    if proc.returncode != 0:
                        raise RuntimeError(f"ffmpeg concat failed (both modes):\n{proc.stderr}")
                else:
                    raise RuntimeError(f"ffmpeg concat failed:\n{proc.stderr}")

            # Mux in the external audio track if provided
            if audio_path:
                mux_cmd = [
                    ffmpeg_bin, "-y", "-loglevel", "error",
                    "-i", concat_only, "-i", audio_path,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-map", "0:v:0", "-map", "1:a:0",
                    out_path,
                ]
                proc = subprocess.run(mux_cmd, capture_output=True, text=True)
                if proc.returncode != 0:
                    raise RuntimeError(f"ffmpeg audio mux failed:\n{proc.stderr}")
            else:
                shutil.move(concat_only, out_path)

            # ─── Subtitles burn-in (ffmpeg only, no API cost) ──────────────────
            if subtitles != "off":
                subtitle_texts = [subtitle_text_1, subtitle_text_2, subtitle_text_3,
                                  subtitle_text_4, subtitle_text_5, subtitle_text_6]
                # Match scene texts to the videos we actually used (skip texts whose video was None)
                kept_texts = []
                for (vid, _), txt in zip(scene_pairs, subtitle_texts, strict=False):
                    if vid is not None:
                        kept_texts.append(txt.strip())

                # Need at least one non-empty text to bother
                if any(kept_texts):
                    # Use per-clip durations from clip_paths (these were finalized after audio mux)
                    durations = [media_duration(p) for p in clip_paths]
                    srt_lines = []
                    t = 0.0
                    def split_caption_chunks(text: str) -> list[str]:
                        raw = text.replace("\r", "\n")
                        if "|" in raw:
                            parts = raw.split("|")
                        else:
                            parts = raw.split("\n")
                        chunks = [p.strip() for p in parts if p.strip()]
                        return chunks or [raw.strip()]

                    sub_idx = 1
                    for text, dur in zip(kept_texts, durations, strict=False):
                        start, end = t, t + dur
                        t = end
                        if not text:
                            continue
                        def fmt(ts: float) -> str:
                            h, rem = divmod(ts, 3600)
                            m, s = divmod(rem, 60)
                            ms = int((s - int(s)) * 1000)
                            return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"
                        chunks = split_caption_chunks(text)
                        chunk_dur = max(0.25, dur / max(1, len(chunks)))
                        for chunk_i, chunk in enumerate(chunks):
                            chunk_start = start + chunk_i * chunk_dur
                            chunk_end = end if chunk_i == len(chunks) - 1 else min(end, chunk_start + chunk_dur)
                            wrapped = chunk.replace("\n", " ").strip()
                            srt_lines.append(f"{sub_idx}\n{fmt(chunk_start)} --> {fmt(chunk_end)}\n{wrapped}\n")
                            sub_idx += 1

                    srt_path = os.path.join(tmpdir, "subs.srt")
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(srt_lines))

                    # Subtitles filter needs re-encode (it touches every frame). Use a fast preset.
                    # Escape values for ffmpeg filter option parsing. In `subtitles=...`
                    # both ':' and '\' are special, and quoting the whole path is not
                    # reliable on macOS temp paths.
                    def escape_filter_value(value: str) -> str:
                        return (
                            value
                            .replace("\\", "\\\\")
                            .replace(":", "\\:")
                            .replace("'", "\\'")
                            .replace(",", "\\,")
                            .replace("[", "\\[")
                            .replace("]", "\\]")
                        )

                    srt_escaped = escape_filter_value(srt_path)
                    style = subtitle_style or "Alignment=2,FontSize=18,Outline=2"
                    style_escaped = escape_filter_value(style)
                    vf = f"subtitles=filename={srt_escaped}:force_style={style_escaped}"

                    # Decide output naming
                    if subtitles == "burned in only":
                        # Overwrite the single out_path with subtitled version
                        subbed_path = os.path.join(full_folder, f"{filename}_{counter:05d}_subs.mp4")
                        burn_cmd = [
                            ffmpeg_bin, "-y", "-loglevel", "error",
                            "-i", out_path,
                            "-vf", vf,
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-c:a", "copy",
                            "-movflags", "+faststart",
                            subbed_path,
                        ]
                        run_ffmpeg(burn_cmd, "ffmpeg subtitle burn-in")
                        os.remove(out_path)
                        os.rename(subbed_path, out_path)
                        subbed_path = out_path
                        print(f"[VideoConcatFFmpeg] burned-in subtitles → {out_path}", flush=True)
                    else:  # "both (extra file with subs)"
                        subbed_path = os.path.join(full_folder, f"{filename}_{counter:05d}_subs.mp4")
                        burn_cmd = [
                            ffmpeg_bin, "-y", "-loglevel", "error",
                            "-i", out_path,
                            "-vf", vf,
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-c:a", "copy",
                            "-movflags", "+faststart",
                            subbed_path,
                        ]
                        run_ffmpeg(burn_cmd, "ffmpeg subtitle burn-in (extra file)")
                        print(f"[VideoConcatFFmpeg] clean → {out_path}", flush=True)
                        print(f"[VideoConcatFFmpeg] subtitled → {subbed_path}", flush=True)

        return IO.NodeOutput(_ii.VideoFromFile(out_path))


class VideoCacheNode(IO.ComfyNode):
    """Cache-aware video passthrough (same idea as ImageCacheNode, but for VIDEO).

    Place between a video-generator node (e.g. PoyoAISeedanceVideoNode) and the
    downstream consumer. With `mode = "use cached"` the upstream is NOT
    evaluated (no API call, no Poyo credits burned). With `mode = "auto"` the
    cache is used when present, otherwise the video is generated and saved.

    Cache directory: <ComfyUI output_dir>/video_cache/<cache_name>.mp4
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="VideoCacheNode",
            display_name="Video Cache (skip regen)",
            category="viral_pipeline/control",
            description=(
                "Cache a generated video to disk; on subsequent runs, optionally bypass the "
                "video generator to save API credits. mode=auto reuses the cached file if present."
            ),
            inputs=[
                IO.String.Input(
                    "cache_name",
                    multiline=False,
                    default="scene_1",
                    tooltip=(
                        "Filename (no extension) inside <output>/video_cache/. "
                        "Use a unique name per scene: scene_1, scene_2, ..."
                    ),
                ),
                IO.Combo.Input(
                    "mode",
                    options=["global", "auto", "use cached", "regenerate"],
                    default="auto",
                    tooltip=(
                        "global = follow RunModeNode/global_mode.\n"
                        "auto = use cached file if exists, else generate & save.\n"
                        "use cached = NEVER call upstream (saves credits; errors if cache missing).\n"
                        "regenerate = always call upstream and overwrite cache."
                    ),
                ),
                IO.Combo.Input(
                    "global_mode",
                    options=["auto", "use cached", "regenerate"],
                    default="auto",
                    optional=True,
                    tooltip="Connect RunModeNode.video_mode here. Used only when local mode = global.",
                ),
                IO.Video.Input(
                    "video",
                    optional=True,
                    lazy=True,
                    tooltip="Upstream video source. Skipped entirely on cache hit.",
                ),
            ],
            outputs=[IO.Video.Output()],
        )

    @classmethod
    def _cache_path(cls, cache_name: str) -> str:
        import os
        import folder_paths
        cache_dir = os.path.join(folder_paths.get_output_directory(), "video_cache")
        os.makedirs(cache_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in cache_name.strip()) or "untitled"
        return os.path.join(cache_dir, f"{safe}.mp4")

    @classmethod
    def check_lazy_status(cls, cache_name: str, mode: str, global_mode: str = "auto", video=None) -> list[str]:
        import os
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        if effective_mode == "regenerate":
            decision = ["video"]
        elif effective_mode == "use cached":
            decision = []
        elif exists:
            decision = []
        else:
            decision = ["video"]
        print(f"[VideoCache] check_lazy_status name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} → upstream_needed={decision}", flush=True)
        return decision

    @classmethod
    async def execute(cls, cache_name: str, mode: str, global_mode: str = "auto", video=None) -> IO.NodeOutput:
        import io
        import os
        import shutil
        from comfy_api.latest import _input_impl as _ii
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        print(f"[VideoCache] execute name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} got_video={video is not None}", flush=True)

        if effective_mode == "use cached" or (effective_mode == "auto" and exists and video is None):
            if not exists:
                raise RuntimeError(
                    f"VideoCache: mode='{effective_mode}' but no cached file at {path}. "
                    f"Run once with mode='regenerate' or 'auto' (with video connected) first."
                )
            print(f"[VideoCache] ← read from disk: {path}", flush=True)
            log_usage("video", cache_hit=True, scene=cache_name)
            return IO.NodeOutput(_ii.VideoFromFile(path))

        # Fresh video from upstream — write to cache, return wrapper around the cached file.
        if video is None:
            raise RuntimeError(
                "VideoCache: mode requires upstream video but none was provided. "
                "Connect the generator's output to the 'video' input."
            )
        src = video.get_stream_source()
        if isinstance(src, str) and os.path.exists(src):
            # Upstream gave us a path — copy bytes to the cache location.
            shutil.copy(src, path)
        elif isinstance(src, io.BytesIO):
            src.seek(0)
            with open(path, "wb") as f:
                shutil.copyfileobj(src, f)
        else:
            # Fall back to the Video object's save_to API
            video.save_to(path)
        print(f"[VideoCache] → wrote to disk: {path}", flush=True)
        return IO.NodeOutput(_ii.VideoFromFile(path))


class AudioCacheNode(IO.ComfyNode):
    """Cache-aware audio passthrough for per-scene voiceover clips.

    Place this immediately after a TTS node. With `mode = "use cached"` the
    upstream TTS node is NOT evaluated, so ElevenLabs credits are not spent when
    re-merging or iterating on downstream video assembly.

    Cache directory: <ComfyUI output_dir>/audio_cache/<cache_name>.wav
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="AudioCacheNode",
            display_name="Audio Cache (skip regen)",
            category="viral_pipeline/control",
            description=(
                "Cache a generated audio clip to disk. mode=use cached skips the "
                "upstream TTS call entirely, useful for per-scene voiceovers."
            ),
            inputs=[
                IO.Audio.Input(
                    "audio",
                    optional=True,
                    lazy=True,
                    tooltip="Upstream audio source. Skipped entirely on cache hit.",
                ),
                IO.String.Input(
                    "cache_name",
                    multiline=False,
                    default="scene_1_voice",
                    tooltip="Filename (no extension) inside <output>/audio_cache/.",
                ),
                IO.Combo.Input(
                    "mode",
                    options=["global", "auto", "use cached", "regenerate"],
                    default="auto",
                    tooltip=(
                        "global = follow RunModeNode/global_mode.\n"
                        "auto = use cached file if exists, else generate & save.\n"
                        "use cached = NEVER call upstream (saves TTS credits; errors if cache missing).\n"
                        "regenerate = always call upstream and overwrite cache."
                    ),
                ),
                IO.Combo.Input(
                    "global_mode",
                    options=["auto", "use cached", "regenerate"],
                    default="auto",
                    optional=True,
                    tooltip="Connect RunModeNode.audio_mode here. Used only when local mode = global.",
                ),
                IO.String.Input(
                    "cache_fingerprint",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip=(
                        "Optional text/key that must match the cached audio. "
                        "Connect the voiceover text here so auto mode regenerates "
                        "TTS when the spoken line changes."
                    ),
                ),
            ],
            outputs=[IO.Audio.Output()],
        )

    @classmethod
    def _cache_path(cls, cache_name: str) -> str:
        import os
        import folder_paths
        cache_dir = os.path.join(folder_paths.get_output_directory(), "audio_cache")
        os.makedirs(cache_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in cache_name.strip()) or "untitled"
        return os.path.join(cache_dir, f"{safe}.wav")

    @classmethod
    def _meta_path(cls, cache_name: str) -> str:
        return cls._cache_path(cache_name) + ".fingerprint"

    @staticmethod
    def _fingerprint(value: str | None) -> str:
        import hashlib
        text = (value or "").strip()
        if not text:
            return ""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _fingerprint_matches(cls, cache_name: str, cache_fingerprint: str | None) -> bool:
        import os
        expected = cls._fingerprint(cache_fingerprint)
        if not expected:
            return True
        meta_path = cls._meta_path(cache_name)
        if not os.path.exists(meta_path):
            return False
        with open(meta_path, "r", encoding="utf-8") as f:
            return f.read().strip() == expected

    @classmethod
    def _write_fingerprint(cls, cache_name: str, cache_fingerprint: str | None) -> None:
        expected = cls._fingerprint(cache_fingerprint)
        if not expected:
            return
        with open(cls._meta_path(cache_name), "w", encoding="utf-8") as f:
            f.write(expected)

    @staticmethod
    def _write_wav(audio_obj, path: str) -> None:
        import wave
        import numpy as np

        waveform = audio_obj["waveform"]
        if waveform.dim() == 3:
            waveform = waveform[0]
        waveform = waveform.detach().cpu()
        if waveform.dim() == 1:
            waveform = waveform.unsqueeze(0)
        samples = waveform.transpose(0, 1).numpy()
        samples = np.clip(samples, -1.0, 1.0)
        pcm = (samples * 32767.0).astype(np.int16)
        with wave.open(path, "wb") as wav:
            wav.setnchannels(pcm.shape[1])
            wav.setsampwidth(2)
            wav.setframerate(int(audio_obj["sample_rate"]))
            wav.writeframes(pcm.tobytes())

    @staticmethod
    def _read_wav(path: str):
        import wave
        import numpy as np

        with wave.open(path, "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        pcm = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            pcm = pcm.reshape(-1, channels).T
        else:
            pcm = pcm.reshape(1, -1)
        waveform = torch.from_numpy(pcm.astype(np.float32) / 32767.0).unsqueeze(0).contiguous()
        return {"waveform": waveform, "sample_rate": sample_rate}

    @classmethod
    def check_lazy_status(
        cls,
        audio=None,
        cache_name: str = "scene_1_voice",
        mode: str = "auto",
        global_mode: str = "auto",
        cache_fingerprint: str = "",
    ) -> list[str]:
        import os
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        fingerprint_ok = cls._fingerprint_matches(cache_name, cache_fingerprint)
        if effective_mode == "regenerate":
            decision = ["audio"]
        elif effective_mode == "use cached":
            decision = []
        elif exists and fingerprint_ok:
            decision = []
        else:
            decision = ["audio"]
        print(f"[AudioCache] check_lazy_status name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} fingerprint_ok={fingerprint_ok} → upstream_needed={decision}", flush=True)
        return decision

    @classmethod
    async def execute(
        cls,
        audio=None,
        cache_name: str = "scene_1_voice",
        mode: str = "auto",
        global_mode: str = "auto",
        cache_fingerprint: str = "",
    ) -> IO.NodeOutput:
        import os
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        fingerprint_ok = cls._fingerprint_matches(cache_name, cache_fingerprint)
        print(f"[AudioCache] execute name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} fingerprint_ok={fingerprint_ok} got_audio={audio is not None}", flush=True)

        if effective_mode == "use cached" or (effective_mode == "auto" and exists and audio is None):
            if not exists:
                raise RuntimeError(
                    f"AudioCache: mode='{effective_mode}' but no cached file at {path}. "
                    f"Run once with mode='regenerate' or 'auto' (with audio connected) first."
                )
            if not fingerprint_ok:
                raise RuntimeError(
                    f"AudioCache: cached audio at {path} was made from different voiceover text. "
                    "Switch mode to 'auto' or 'regenerate' so TTS can update it."
                )
            print(f"[AudioCache] ← read from disk: {path}", flush=True)
            log_usage("audio", cache_hit=True, scene=cache_name)
            return IO.NodeOutput(cls._read_wav(path))

        if audio is None:
            raise RuntimeError(
                "AudioCache: mode requires upstream audio but none was provided. "
                "Connect the TTS output to the 'audio' input."
            )
        cls._write_wav(audio, path)
        cls._write_fingerprint(cache_name, cache_fingerprint)
        print(f"[AudioCache] → wrote to disk: {path}", flush=True)
        return IO.NodeOutput(audio)


class RunModeNode(IO.ComfyNode):
    """One top-level switch that emits cache modes for the whole pipeline."""

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="RunModeNode",
            display_name="Run Mode Controller",
            category="viral_pipeline/control",
            description=(
                "Central cache-mode router. Connect outputs to cache mode inputs when "
                "you want one switch to control the whole workflow."
            ),
            inputs=[
                IO.Combo.Input(
                    "run_mode",
                    options=[
                        "auto (use cache when valid, generate misses)",
                        "final from cache",
                        "draft cheap",
                        "regenerate scenario",
                        "regenerate voiceover text",
                        "regenerate audio",
                        "regenerate images",
                        "regenerate videos",
                        "full regenerate",
                    ],
                    default="auto (use cache when valid, generate misses)",
                    tooltip="Choose what should be regenerated globally.",
                ),
            ],
            outputs=[
                IO.Custom("COMBO").Output("scenario_mode", display_name="scenario_mode"),
                IO.Custom("COMBO").Output("voice_text_mode", display_name="voice_text_mode"),
                IO.Custom("COMBO").Output("image_mode", display_name="image_mode"),
                IO.Custom("COMBO").Output("video_mode", display_name="video_mode"),
                IO.Custom("COMBO").Output("audio_mode", display_name="audio_mode"),
                IO.Custom("COMBO").Output("publish_copy_mode", display_name="publish_copy_mode"),
                IO.String.Output("summary", display_name="summary"),
            ],
        )

    @classmethod
    async def execute(cls, run_mode: str) -> IO.NodeOutput:
        mapping = {
            "auto (use cache when valid, generate misses)": ("auto", "auto", "auto", "auto", "auto", "auto"),
            "final from cache": ("use cached", "use cached", "use cached", "use cached", "use cached", "use cached"),
            "draft cheap": ("auto", "auto", "use cached", "use cached", "use cached", "auto"),
            "regenerate scenario": ("regenerate", "auto", "auto", "auto", "auto", "auto"),
            "regenerate voiceover text": ("use cached", "regenerate", "use cached", "use cached", "auto", "auto"),
            "regenerate audio": ("use cached", "use cached", "use cached", "use cached", "regenerate", "use cached"),
            "regenerate images": ("use cached", "use cached", "regenerate", "use cached", "use cached", "use cached"),
            "regenerate videos": ("use cached", "use cached", "use cached", "regenerate", "use cached", "use cached"),
            "full regenerate": ("regenerate", "regenerate", "regenerate", "regenerate", "regenerate", "regenerate"),
        }
        scenario, voice, image, video, audio, publish = mapping.get(run_mode, mapping["auto (use cache when valid, generate misses)"])
        summary = (
            f"RUN MODE: {run_mode}\n"
            f"- scenario text: {scenario}\n"
            f"- voiceover text: {voice}\n"
            f"- images: {image}\n"
            f"- videos: {video}\n"
            f"- audio: {audio}\n"
            f"- publish copy: {publish}\n\n"
            "Tip: leave outputs unconnected for per-node manual control, or connect them to cache mode inputs for global control."
        )
        return IO.NodeOutput(scenario, voice, image, video, audio, publish, summary)


class CacheNamespaceNode(IO.ComfyNode):
    """Generate a stable cache namespace / run id for versioned experiments."""

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="CacheNamespaceNode",
            display_name="Cache Namespace / Run ID",
            category="viral_pipeline/control",
            inputs=[
                IO.String.Input("project_name", multiline=False, default="viral_video"),
                IO.String.Input("label", multiline=False, default="main"),
                IO.Combo.Input(
                    "mode",
                    options=["stable label", "timestamped run"],
                    default="stable label",
                    tooltip="stable label reuses a namespace; timestamped run creates a fresh run id.",
                ),
            ],
            outputs=[
                IO.String.Output("cache_prefix", display_name="cache_prefix"),
                IO.String.Output("run_id", display_name="run_id"),
            ],
        )

    @classmethod
    async def execute(cls, project_name: str, label: str, mode: str) -> IO.NodeOutput:
        from datetime import datetime
        safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "_", (project_name or "viral_video").strip()).strip("_")
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", (label or "main").strip()).strip("_")
        if mode == "timestamped run":
            run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        else:
            run_id = safe_label or "main"
        prefix = f"{safe_project}/{run_id}"
        return IO.NodeOutput(prefix, run_id)


class CaptionChunkerNode(IO.ComfyNode):
    """Split per-scene voiceover into shorter caption chunks for Shorts-style subs."""

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="CaptionChunkerNode",
            display_name="Caption Chunker (per scene)",
            category="viral_pipeline/subtitles",
            inputs=[
                IO.String.Input("voiceover_1", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_2", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_3", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_4", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_5", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_6", multiline=True, default="", optional=True),
                IO.Int.Input("max_words_per_chunk", default=4, min=2, max=8, step=1),
                IO.String.Input(
                    "separator",
                    multiline=False,
                    default=" | ",
                    tooltip="VideoConcat uses this separator to time chunks inside each scene.",
                ),
            ],
            outputs=[
                IO.String.Output("caption_1", display_name="caption_1"),
                IO.String.Output("caption_2", display_name="caption_2"),
                IO.String.Output("caption_3", display_name="caption_3"),
                IO.String.Output("caption_4", display_name="caption_4"),
                IO.String.Output("caption_5", display_name="caption_5"),
                IO.String.Output("caption_6", display_name="caption_6"),
                IO.String.Output("preview", display_name="preview"),
            ],
        )

    @staticmethod
    def _chunk(text: str, max_words: int, separator: str) -> str:
        words = re.findall(r"\S+", (text or "").replace("\n", " ").strip())
        if not words:
            return ""
        chunks = []
        for i in range(0, len(words), max(1, max_words)):
            chunks.append(" ".join(words[i:i + max_words]))
        return separator.join(chunks)

    @classmethod
    async def execute(
        cls,
        voiceover_1: str = "", voiceover_2: str = "", voiceover_3: str = "",
        voiceover_4: str = "", voiceover_5: str = "", voiceover_6: str = "",
        max_words_per_chunk: int = 4,
        separator: str = " | ",
    ) -> IO.NodeOutput:
        chunks = [
            cls._chunk(t, max_words_per_chunk, separator)
            for t in [voiceover_1, voiceover_2, voiceover_3, voiceover_4, voiceover_5, voiceover_6]
        ]
        preview = "\n".join(f"{i}. {text}" for i, text in enumerate(chunks, start=1))
        return IO.NodeOutput(*chunks, preview)


class SceneStatusPanelNode(IO.ComfyNode):
    """Read local cache files and report what the pipeline will reuse."""

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SceneStatusPanelNode",
            display_name="Scene Status Panel",
            category="viral_pipeline/diagnostics",
            inputs=[
                IO.Video.Input("trigger", optional=True, lazy=True),
                IO.String.Input("cache_prefix", multiline=False, default="", optional=True),
            ],
            outputs=[IO.String.Output("status", display_name="status")],
        )

    @staticmethod
    def _file_info(path: str) -> str:
        import os
        if not os.path.exists(path):
            return "missing"
        size = os.path.getsize(path)
        if size >= 1024 * 1024:
            return f"cache {size / (1024 * 1024):.1f} MB"
        return f"cache {size / 1024:.1f} KB"

    @classmethod
    async def execute(cls, trigger=None, cache_prefix: str = "") -> IO.NodeOutput:
        import os
        import folder_paths
        out = folder_paths.get_output_directory()

        def p(kind: str, name: str, ext: str) -> str:
            if cache_prefix.strip():
                name = f"{cache_prefix.strip().rstrip('/')}/{name}"
            return os.path.join(out, kind, f"{name}.{ext}")

        lines = ["PIPELINE STATUS", "================"]
        for cache_name in ["scenario_1_concept", "scenario_2_script", "publish_copy"]:
            lines.append(f"text/{cache_name}: {cls._file_info(os.path.join(out, 'text_cache', cache_name + '.txt'))}")
        lines.append("")
        lines.append("SCENES")
        for i in range(1, _MAX_SCENES + 1):
            image = cls._file_info(p("image_cache", f"scene_{i}", "png"))
            video = cls._file_info(p("video_cache", f"scene_{i}", "mp4"))
            voice_text = cls._file_info(os.path.join(out, "text_cache", f"voiceover_scene_{i}.txt"))
            audio = cls._file_info(p("audio_cache", f"scene_{i}_voice", "wav"))
            lines.append(f"{i}. image={image} | video={video} | voice_text={voice_text} | audio={audio}")
        return IO.NodeOutput("\n".join(lines))


_GOOGLE_AI_BASE = "https://generativelanguage.googleapis.com"


def _gemini_supports_system_instruction(model: str) -> bool:
    """Gemma models on AI Studio reject `systemInstruction`; only Gemini accepts it."""
    return model.lower().startswith("gemini")


async def _gemini_generate_text(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 60000,
) -> str:
    """Minimal one-shot Gemini text call.

    Self-contained (does not import GoogleGemmaNode) to avoid a circular import:
    nodes_google_gemma already imports log_usage from this module.
    """
    model_id = (model or "").strip() or "gemini-2.5-flash-lite"
    sys_text = (system_prompt or "").strip()
    parts = []
    if sys_text and not _gemini_supports_system_instruction(model_id):
        parts.append({"text": f"[SYSTEM INSTRUCTIONS]\n{sys_text}\n\n[USER REQUEST]\n{user_prompt}"})
    else:
        parts.append({"text": user_prompt})

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if sys_text and _gemini_supports_system_instruction(model_id):
        body["systemInstruction"] = {"parts": [{"text": sys_text}]}

    url = f"{_GOOGLE_AI_BASE}/v1beta/models/{model_id}:generateContent"
    headers = {"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"}

    last_err = None
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=body, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status >= 500 and attempt < 2:
                        last_err = Exception(f"Gemini error {resp.status}: {await resp.text()}")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if resp.status != 200:
                        raise Exception(f"Gemini error {resp.status}: {await resp.text()}")
                    data = await resp.json()
                    break
        except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as exc:
            last_err = exc
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
    else:
        raise last_err or RuntimeError("Gemini call failed")

    candidates = data.get("candidates", [])
    if not candidates:
        raise Exception("Gemini returned no candidates — check API key and model name.")
    text = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []))
    usage = data.get("usageMetadata", {})
    log_usage(
        "text", cache_hit=False, model=model_id,
        tokens_in=usage.get("promptTokenCount", 0),
        tokens_out=usage.get("candidatesTokenCount", 0),
    )
    return text


def _opening_words(text: str, n: int = 10) -> str:
    return " ".join(re.findall(r"[A-Za-zА-Яа-я0-9']+", (text or "").lower())[:n])


def _field_is_blank(script: str, scene_n: int, field: str) -> bool:
    """True when scene N has a `Field:` header whose value is empty on its own line.

    Guards the empty-field case _parse_field cannot see: with a blank value the
    `\\s*` after the colon spills onto the next line and returns the following
    field's text, so a missing Video Prompt would otherwise pass undetected.
    """
    clean = _strip_markdown(script)
    block = re.search(
        rf"SCENE\s+{scene_n}\s*:.*?(?=SCENE\s+{scene_n + 1}\s*:|$)",
        clean, re.DOTALL | re.IGNORECASE,
    )
    if not block:
        return False
    suffix = r"(?:\s*[\(\[\-\—\–][^:\n]*)?"
    return re.search(
        rf"^\s*{re.escape(field)}{suffix}\s*:[^\S\r\n]*$",
        block.group(0), re.MULTILINE | re.IGNORECASE,
    ) is not None


def _script_issues(script: str, max_video_chars: int = 950, max_voiceover_words: int = 12) -> list[str]:
    """Deterministic, no-API issue scan over the parsed scenes of a script.

    Mirrors the checks in PipelineQualityCheckNode but works straight off the
    raw script text so the repair node can decide whether an LLM fix is needed
    BEFORE any image/video money is spent.
    """
    issues: list[str] = []
    image_prompts, video_prompts, voiceovers = [], [], []
    for n in range(1, _MAX_SCENES + 1):
        image_prompts.append(_parse_field(script, n, "Image Prompt"))
        video_prompts.append(_parse_field(script, n, "Video Prompt"))
        voiceovers.append(_parse_field(script, n, "Voiceover"))

    starts: dict[str, int] = {}
    for i, prompt in enumerate(image_prompts, start=1):
        if not prompt.strip() or _field_is_blank(script, i, "Image Prompt"):
            issues.append(f"Scene {i}: missing Image Prompt")
            continue
        if len(prompt.split()) < 45:
            issues.append(f"Scene {i}: Image Prompt too short ({len(prompt.split())} words; want 45+)")
        start = _opening_words(prompt)
        if start and start in starts:
            issues.append(f"Scenes {starts[start]} and {i}: Image Prompts start too similarly — make scene {i}'s opening visually unique")
        starts[start] = i
        if "character bible" in prompt.lower():
            issues.append(f"Scene {i}: Image Prompt leaks 'CHARACTER BIBLE' label — open with the scene action instead")

    for i, prompt in enumerate(video_prompts, start=1):
        if not prompt.strip() or _field_is_blank(script, i, "Video Prompt"):
            issues.append(f"Scene {i}: missing Video Prompt")
        elif len(prompt) > max_video_chars:
            issues.append(f"Scene {i}: Video Prompt {len(prompt)} chars > {max_video_chars} — compress to START/ACTION/END/CAMERA/ATMOSPHERE")

    for i, text in enumerate(voiceovers, start=1):
        wc = len(re.findall(r"[A-Za-zА-Яа-я0-9']+", text or ""))
        if wc == 0 or _field_is_blank(script, i, "Voiceover"):
            issues.append(f"Scene {i}: missing Voiceover")
        elif wc > max_voiceover_words:
            issues.append(f"Scene {i}: Voiceover {wc} words > {max_voiceover_words} — shorten so it fits inside its scene")

    return issues


_REPAIR_SYSTEM_PROMPT = (
    "You are a script repair tool for a 6-scene short-form video pipeline. "
    "You receive a structured script and a numbered list of concrete problems found by an automated checker. "
    "Fix ONLY those problems. Do not invent a new story, do not change the concept, characters, setting, "
    "signature object, or the scene order. Preserve every field and the exact structure: the header bible "
    "(VISUAL STYLE / CHARACTER BIBLE / SETTING BIBLE / GOAL / OBSTACLE / STAKES / STORY SPINE / SIGNATURE OBJECT) "
    "and 6 SCENE blocks separated by --- with Continuity / Visual / Image Prompt / Video Prompt / Voiceover.\n\n"
    "Repair rules:\n"
    "- Image Prompt: single line, 90-140 words, MUST open with the scene-specific action (never CHARACTER BIBLE text), "
    "first 15-20 words visually unique per scene, signature object in its scene-current state, name the emotional beat.\n"
    "- Video Prompt: ONE single line in the exact format 'START: ... ACTION: ... END: ... CAMERA: ... ATMOSPHERE: ...', "
    "400-700 chars, no Character/Setting/Style repetition, no filler adjectives. The START of scene N must match the END of scene N-1.\n"
    "- Voiceover: 7-12 words, fits inside its scene.\n"
    "Output ONLY the full corrected script — no preamble, no explanations, no markdown fences."
)


class SceneScriptRepairNode(IO.ComfyNode):
    """In-graph self-healing for the script TEXT (cheap, runs before image/video spend).

    Runs the same deterministic checks as PipelineQualityCheckNode directly on
    the script. If there are NO issues, the script passes through unchanged with
    zero API calls. If issues exist, it makes ONE Gemini repair pass that fixes
    only the flagged problems and returns the corrected script.

    ComfyUI is a single-pass DAG — this is a forward-pass repair, not an
    iterate-until-perfect loop. It catches text-level problems (missing/short/
    duplicate prompts, bible leakage, over-length video prompts and voiceovers)
    before they propagate into expensive image/video generation.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SceneScriptRepairNode",
            display_name="Scene Script Auto-Repair",
            category="viral_pipeline/text",
            description=(
                "Deterministically QA a 6-scene script and, only if problems are found, "
                "run one Gemini pass to fix them. Passes the script through untouched "
                "(no API call) when it is already clean. Insert between the script writer "
                "and the SceneParser."
            ),
            inputs=[
                IO.String.Input("api_key", multiline=False, default="", tooltip="Google AI Studio API key (same as the script writer)."),
                IO.String.Input("model", multiline=False, default="gemini-2.5-flash", tooltip="Gemini model for the repair pass."),
                IO.String.Input("script", multiline=True, default="", tooltip="Full 6-scene script from the architect/script-writer node."),
                IO.Boolean.Input("enabled", default=True, tooltip="Disable to pass the script through untouched (no checks, no repair)."),
                IO.Int.Input("max_video_prompt_chars", default=950, min=500, max=2000, step=10, advanced=True),
                IO.Int.Input("max_voiceover_words", default=12, min=4, max=30, step=1, advanced=True),
                IO.Float.Input("temperature", default=0.3, min=0.0, max=1.0, step=0.05, advanced=True),
                IO.Combo.Input(
                    "scenario_mode",
                    options=["auto", "use cached", "regenerate"],
                    default="auto",
                    optional=True,
                    tooltip=(
                        "Connect RunModeNode.scenario_mode here. When 'use cached', repair is "
                        "skipped entirely (no checks, no API) so cache-reuse runs spend nothing."
                    ),
                ),
            ],
            outputs=[
                IO.String.Output("repaired_script", display_name="repaired_script"),
                IO.String.Output("repair_report", display_name="repair_report"),
            ],
        )

    @classmethod
    async def execute(
        cls,
        api_key: str,
        model: str,
        script: str,
        enabled: bool = True,
        max_video_prompt_chars: int = 950,
        max_voiceover_words: int = 12,
        temperature: float = 0.3,
        scenario_mode: str = "auto",
    ) -> IO.NodeOutput:
        script = script or ""
        if not enabled:
            return IO.NodeOutput(script, "Auto-repair disabled — script passed through untouched.")
        if scenario_mode == "use cached":
            return IO.NodeOutput(script, "Run mode = use cached — repair skipped (no checks, no API).")
        if not script.strip():
            return IO.NodeOutput(script, "Empty script — nothing to repair.")

        issues = _script_issues(script, int(max_video_prompt_chars), int(max_voiceover_words))
        if not issues:
            return IO.NodeOutput(script, "QA passed — no issues, no repair call made.")

        validate_string(api_key, strip_whitespace=True, min_length=1, field_name="api_key")
        issue_list = "\n".join(f"{i}. {issue}" for i, issue in enumerate(issues, start=1))
        user_prompt = (
            "PROBLEMS TO FIX (fix only these, preserve everything else):\n"
            f"{issue_list}\n\n"
            "SCRIPT:\n"
            f"{script}"
        )
        repaired = await _gemini_generate_text(
            api_key=api_key,
            model=model,
            system_prompt=_REPAIR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=temperature,
        )
        repaired = repaired.strip()
        if not repaired:
            # Fail safe: never hand a worse (empty) script downstream.
            return IO.NodeOutput(
                script,
                "Repair call returned empty output — kept original script.\nIssues found:\n" + issue_list,
            )

        residual = _script_issues(repaired, int(max_video_prompt_chars), int(max_voiceover_words))
        report_lines = [
            f"AUTO-REPAIR: fixed {len(issues)} flagged issue(s).",
            "Issues found:",
            issue_list,
            "",
            ("Residual issues after repair: none." if not residual
             else "Residual issues still present after repair:\n" + "\n".join(f"- {r}" for r in residual)),
        ]
        return IO.NodeOutput(repaired, "\n".join(report_lines))


# ── Concept feasibility scoring (advisory) ──────────────────────────────────────

_CONCEPT_EASY_TERMS = {
    "transform": "transformation arc is strong for AI video",
    "before": "before/after gives clear visual contrast",
    "after": "before/after gives clear visual contrast",
    "satisfying": "satisfying loop holds rewatch",
    "story": "story format maps cleanly to 6 scenes",
    "journey": "journey/arc maps cleanly to 6 scenes",
    "reveal": "reveal/twist drives retention",
    "twist": "reveal/twist drives retention",
    "stylized": "stylized visuals are easy to generate safely",
    "voxel": "blocky/voxel style is easy to generate safely",
    "animation": "animation/fiction fits AI generation",
    "creature": "stylized creature is easy to generate",
    "glow": "glowing signature object reads well in motion",
}
_CONCEPT_HARD_TERMS = {
    "celebrity": "real/celebrity likeness risk — translate to a descriptive look",
    "real person": "real-person likeness risk",
    "interview": "talking-head realism is harder for AI video",
    "talking head": "talking-head lip-sync realism is harder",
    "dance": "precise choreography is harder to generate",
    "sport": "fast precise body physics is harder",
    "text on screen": "lots of legible on-screen text is unreliable in AI video",
    "logo": "brand logos are a content-policy risk",
    "brand": "brand names are a content-policy risk",
    "news": "current real-world references age quickly",
    "politic": "politics/religion content risk",
}


def _concept_feasibility_score(text: str) -> tuple[int, list[str], list[str]]:
    """Heuristic 0-100 score of how AI-producible a generated concept is."""
    low = (text or "").lower()
    easy = sorted({reason for term, reason in _CONCEPT_EASY_TERMS.items() if term in low})
    hard = sorted({reason for term, reason in _CONCEPT_HARD_TERMS.items() if term in low})
    score = 60 + min(28, len(easy) * 7) - min(40, len(hard) * 13)
    return max(0, min(100, score)), easy, hard


class ConceptFeasibilityScoreNode(IO.ComfyNode):
    """Advisory: score a generated concept for AI-producibility before scripting.

    Does NOT block or reroll (a true reroll needs a loop, which a single DAG pass
    cannot do). It surfaces a 0-100 score plus what is easy / risky to generate so
    you can catch a doomed concept (celebrity likeness, talking heads, heavy
    on-screen text) before spending on a script + 6 images + 6 videos.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ConceptFeasibilityScoreNode",
            display_name="Concept Feasibility Score (advisory)",
            category="viral_pipeline/diagnostics",
            description=(
                "Score a generated viral concept 0-100 for how reliably it can be made with "
                "AI image/video, listing what is easy vs risky. Advisory only — wire to a preview."
            ),
            inputs=[
                IO.String.Input("concept", multiline=True, default="", tooltip="The concept text from the concept-generator LLM."),
                IO.Int.Input("warn_below", default=55, min=0, max=100, step=5, tooltip="Scores below this are flagged as risky."),
            ],
            outputs=[IO.String.Output("feasibility_report", display_name="feasibility_report")],
        )

    @classmethod
    async def execute(cls, concept: str, warn_below: int = 55) -> IO.NodeOutput:
        if not (concept or "").strip():
            return IO.NodeOutput("CONCEPT FEASIBILITY\n(no concept text provided)")
        score, easy, hard = _concept_feasibility_score(concept)
        verdict = "GOOD — proceed" if score >= warn_below else "RISKY — consider regenerating the concept"
        lines = [
            "CONCEPT FEASIBILITY (advisory)",
            "==============================",
            f"AI-producibility score: {score}/100 — {verdict}",
            "",
            "Easy to generate:" if easy else "Easy to generate: (no strong easy signals detected)",
        ]
        lines.extend(f"  + {e}" for e in easy)
        if hard:
            lines.append("")
            lines.append("Watch-outs / risk:")
            lines.extend(f"  - {h}" for h in hard)
        if score < warn_below:
            lines.append("")
            lines.append("Tip: re-run the concept generator (RunMode → regenerate scenario) and lean into a "
                         "stylized single protagonist, one signature object, a clear transformation, and no real people/brands.")
        return IO.NodeOutput("\n".join(lines))


_VIDEO_SEGMENT_ORDER = ["START", "ACTION", "END", "CAMERA", "ATMOSPHERE"]
_VIDEO_SEGMENT_RE = re.compile(r"\b(START|ACTION|END|CAMERA|ATMOSPHERE)\s*:", re.IGNORECASE)


def _split_video_segments(video_prompt: str) -> dict:
    """Parse a single-line 'START: .. ACTION: .. END: .. CAMERA: .. ATMOSPHERE: ..' prompt."""
    if not video_prompt:
        return {}
    matches = list(_VIDEO_SEGMENT_RE.finditer(video_prompt))
    segs: dict[str, str] = {}
    for idx, m in enumerate(matches):
        key = m.group(1).upper()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(video_prompt)
        segs[key] = video_prompt[start:end].strip().strip(".").strip()
    return segs


def _join_video_segments(segs: dict) -> str:
    parts = [f"{k}: {segs[k]}" for k in _VIDEO_SEGMENT_ORDER if segs.get(k)]
    return (". ".join(parts) + ".") if parts else ""


def _set_field(script: str, scene_n: int, field: str, value: str) -> str:
    """Replace the single-line value of `Field:` in scene N's block (plain labels)."""
    line_re = re.compile(rf"(^[^\S\r\n]*{re.escape(field)}[^\S\r\n]*:[^\S\r\n]*).*$", re.MULTILINE)

    def repl_block(m):
        return line_re.sub(lambda lm: lm.group(1) + value, m.group(0), count=1)

    block_re = re.compile(
        rf"SCENE\s+{scene_n}\s*:.*?(?=SCENE\s+{scene_n + 1}\s*:|$)",
        re.DOTALL | re.IGNORECASE,
    )
    return block_re.sub(repl_block, script, count=1)


class SceneContinuityAnchorNode(IO.ComfyNode):
    """Mechanically enforce scene-to-scene continuity in video prompts.

    For each scene N (2..6) it copies the END: segment of scene N-1 into the
    START: segment of scene N, so each clip visually begins where the previous
    one ended. This makes the 'START of scene N matches END of scene N-1' rule a
    guarantee instead of relying on the LLM's discipline. No API calls.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SceneContinuityAnchorNode",
            display_name="Scene Continuity Anchor (END→START)",
            category="viral_pipeline/text",
            description=(
                "Rewrites each scene's video-prompt START: to match the previous scene's END:, "
                "deterministically chaining the 6 clips. Insert after the script repair node, "
                "before the SceneParser. No API calls."
            ),
            inputs=[
                IO.String.Input("script", multiline=True, default="", tooltip="Full 6-scene script."),
                IO.Boolean.Input("enabled", default=True, tooltip="Disable to pass the script through untouched."),
            ],
            outputs=[
                IO.String.Output("script", display_name="script"),
                IO.String.Output("continuity_report", display_name="continuity_report"),
            ],
        )

    @classmethod
    async def execute(cls, script: str, enabled: bool = True) -> IO.NodeOutput:
        script = script or ""
        if not enabled or not script.strip():
            return IO.NodeOutput(script, "Continuity anchor disabled or empty script — passed through.")

        segs_by_scene = {
            n: _split_video_segments(_parse_field(script, n, "Video Prompt"))
            for n in range(1, _MAX_SCENES + 1)
        }
        notes: list[str] = []
        changed = 0
        new_script = script
        for n in range(2, _MAX_SCENES + 1):
            prev, cur = segs_by_scene[n - 1], segs_by_scene[n]
            if not cur:
                continue
            prev_end = prev.get("END")
            if not prev_end:
                notes.append(f"Scene {n}: scene {n - 1} has no END segment — left unchanged")
                continue
            if not cur.get("START"):
                notes.append(f"Scene {n}: no START segment — left unchanged")
                continue
            if cur["START"].strip().lower() == prev_end.strip().lower():
                continue
            cur["START"] = prev_end
            new_script = _set_field(new_script, n, "Video Prompt", _join_video_segments(cur))
            changed += 1
            notes.append(f"Scene {n}: START set to match scene {n - 1} END")

        report = [f"CONTINUITY ANCHOR: chained {changed} scene transition(s)."]
        report.extend(f"- {note}" for note in notes) if notes else report.append("- All transitions already aligned.")
        return IO.NodeOutput(new_script, "\n".join(report))


class PipelineQualityCheckNode(IO.ComfyNode):
    """Deterministic QA before final render: prompt variety, lengths, cache state."""

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="PipelineQualityCheckNode",
            display_name="Ready To Render? QA",
            category="viral_pipeline/diagnostics",
            inputs=[
                IO.String.Input("script", multiline=True, default="", optional=True),
                IO.String.Input("image_prompt_1", multiline=True, default="", optional=True),
                IO.String.Input("image_prompt_2", multiline=True, default="", optional=True),
                IO.String.Input("image_prompt_3", multiline=True, default="", optional=True),
                IO.String.Input("image_prompt_4", multiline=True, default="", optional=True),
                IO.String.Input("image_prompt_5", multiline=True, default="", optional=True),
                IO.String.Input("image_prompt_6", multiline=True, default="", optional=True),
                IO.String.Input("video_prompt_1", multiline=True, default="", optional=True),
                IO.String.Input("video_prompt_2", multiline=True, default="", optional=True),
                IO.String.Input("video_prompt_3", multiline=True, default="", optional=True),
                IO.String.Input("video_prompt_4", multiline=True, default="", optional=True),
                IO.String.Input("video_prompt_5", multiline=True, default="", optional=True),
                IO.String.Input("video_prompt_6", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_1", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_2", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_3", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_4", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_5", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_6", multiline=True, default="", optional=True),
                IO.Int.Input("max_video_prompt_chars", default=950, min=500, max=2000, step=10),
                IO.Int.Input("max_voiceover_words", default=12, min=4, max=30, step=1),
                IO.String.Input(
                    "feasibility_report", multiline=True, default="", optional=True,
                    tooltip="Connect ConceptFeasibilityScoreNode.feasibility_report to fold the concept score into this verdict.",
                ),
                IO.Int.Input(
                    "min_feasibility", default=0, min=0, max=100, step=5, optional=True,
                    tooltip="If > 0 and the concept score is below this, the verdict flips to NOT READY. 0 = advisory only.",
                ),
            ],
            outputs=[
                IO.String.Output("qa_report", display_name="qa_report"),
                IO.String.Output("regeneration_plan", display_name="regeneration_plan"),
            ],
        )

    @staticmethod
    def _words(text: str) -> list[str]:
        return re.findall(r"[A-Za-zА-Яа-я0-9']+", text or "")

    @staticmethod
    def _opening(text: str, n: int = 10) -> str:
        return " ".join(PipelineQualityCheckNode._words(text.lower())[:n])

    @classmethod
    async def execute(cls, **kwargs) -> IO.NodeOutput:
        image_prompts = [kwargs.get(f"image_prompt_{i}", "") or "" for i in range(1, 7)]
        video_prompts = [kwargs.get(f"video_prompt_{i}", "") or "" for i in range(1, 7)]
        voiceovers = [kwargs.get(f"voiceover_{i}", "") or "" for i in range(1, 7)]
        max_video = int(kwargs.get("max_video_prompt_chars", 950))
        max_voice = int(kwargs.get("max_voiceover_words", 12))
        feasibility_report = kwargs.get("feasibility_report", "") or ""
        min_feasibility = int(kwargs.get("min_feasibility", 0) or 0)

        issues = []
        plan = []

        feasibility_score = None
        match = re.search(r"score:\s*(\d+)\s*/\s*100", feasibility_report, re.IGNORECASE)
        if match:
            feasibility_score = int(match.group(1))
            if min_feasibility > 0 and feasibility_score < min_feasibility:
                issues.append(f"Concept feasibility {feasibility_score}/100 < {min_feasibility} — concept is hard to produce with AI")
                plan.append("Regenerate the concept (RunMode → regenerate scenario); favor a stylized single protagonist, one signature object, clear transformation, no real people/brands.")

        starts = {}
        for i, prompt in enumerate(image_prompts, start=1):
            if not prompt.strip():
                issues.append(f"Scene {i}: missing image prompt")
                plan.append(f"Regenerate/fix image prompt for scene {i}.")
                continue
            if len(prompt.split()) < 45:
                issues.append(f"Scene {i}: image prompt looks short ({len(prompt.split())} words)")
            start = cls._opening(prompt)
            if start in starts and start:
                issues.append(f"Scenes {starts[start]} and {i}: image prompts start too similarly")
                plan.append(f"Rewrite image prompt opening for scene {i}.")
            starts[start] = i
            if "character bible" in prompt.lower():
                issues.append(f"Scene {i}: image prompt contains 'CHARACTER BIBLE'")
                plan.append(f"Repair image prompt for scene {i}; start with action, not bible text.")

        for i, prompt in enumerate(video_prompts, start=1):
            if not prompt.strip():
                issues.append(f"Scene {i}: missing video prompt")
                plan.append(f"Regenerate video prompt for scene {i}.")
            elif len(prompt) > max_video:
                issues.append(f"Scene {i}: video prompt {len(prompt)} chars > {max_video}")
                plan.append(f"Compress video prompt for scene {i}.")

        for i, text in enumerate(voiceovers, start=1):
            wc = len(cls._words(text))
            if wc == 0:
                issues.append(f"Scene {i}: missing voiceover")
                plan.append(f"Regenerate voiceover text for scene {i}.")
            elif wc > max_voice:
                issues.append(f"Scene {i}: voiceover {wc} words > {max_voice}")
                plan.append(f"Shorten voiceover scene {i}; then regenerate its audio cache.")

        status = "READY" if not issues else "NOT READY"
        report = [f"READY TO RENDER: {status}", "======================"]
        if issues:
            report.extend(f"- {issue}" for issue in issues)
        else:
            report.append("- No blocking deterministic issues found.")
        if feasibility_score is not None:
            report.append("")
            report.append(f"Concept feasibility: {feasibility_score}/100"
                          + (f" (threshold {min_feasibility})" if min_feasibility > 0 else " (advisory)"))
        report.append("")
        report.append("Residual risk: this does not visually inspect generated images/videos.")
        regen = "\n".join(dict.fromkeys(plan)) if plan else "No targeted regeneration needed."
        return IO.NodeOutput("\n".join(report), regen)


class ManifestWriterNode(IO.ComfyNode):
    """Write a per-run JSON manifest next to final outputs."""

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ManifestWriterNode",
            display_name="Run Manifest Writer",
            category="viral_pipeline/diagnostics",
            inputs=[
                IO.Video.Input("video", optional=True, lazy=True),
                IO.String.Input("run_id", multiline=False, default="", optional=True),
                IO.String.Input("script", multiline=True, default="", optional=True),
                IO.String.Input("qa_report", multiline=True, default="", optional=True),
                IO.String.Input("cost_report", multiline=True, default="", optional=True),
                IO.String.Input("publish_copy", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_1", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_2", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_3", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_4", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_5", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_6", multiline=True, default="", optional=True),
                IO.Boolean.Input("write_file", default=True),
            ],
            outputs=[IO.String.Output("manifest_summary", display_name="manifest_summary")],
        )

    @classmethod
    async def execute(cls, video=None, run_id: str = "", script: str = "", qa_report: str = "",
                      cost_report: str = "", publish_copy: str = "", voiceover_1: str = "",
                      voiceover_2: str = "", voiceover_3: str = "", voiceover_4: str = "",
                      voiceover_5: str = "", voiceover_6: str = "", write_file: bool = True) -> IO.NodeOutput:
        import json
        import os
        from datetime import datetime
        import folder_paths

        now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safe_run = re.sub(r"[^A-Za-z0-9_.-]+", "_", (run_id or now).strip()).strip("_") or now
        video_source = ""
        if video is not None:
            try:
                src = video.get_stream_source()
                video_source = src if isinstance(src, str) else ""
            except Exception:
                video_source = ""

        data = {
            "created_at": now,
            "run_id": safe_run,
            "video_source": video_source,
            "script": script,
            "voiceovers": [voiceover_1, voiceover_2, voiceover_3, voiceover_4, voiceover_5, voiceover_6],
            "qa_report": qa_report,
            "cost_report": cost_report,
            "publish_copy": publish_copy,
        }

        manifest_path = ""
        if write_file:
            manifest_dir = os.path.join(folder_paths.get_output_directory(), "manifests")
            os.makedirs(manifest_dir, exist_ok=True)
            manifest_path = os.path.join(manifest_dir, f"{safe_run}.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        summary = (
            "RUN MANIFEST\n"
            "============\n"
            f"run_id: {safe_run}\n"
            f"path: {manifest_path or '(write_file=false)'}\n"
            f"video_source: {video_source or '(unknown)'}"
        )
        return IO.NodeOutput(summary)


# ══════════════════════════════════════════════════════════════════════════════
# COST TRACKER
# ══════════════════════════════════════════════════════════════════════════════

# Hardcoded prices per Poyo's public pricing page (USD).
# Image: per-call. Video: per-second (or per-video for older veo3.1-* base models).
# Update via the `refresh_prices` button on the CostTrackerNode (TODO).
_POYO_PRICES_USD = {
    "image": {
        "gpt-image-2":         {"1K": 0.010, "2K": 0.020, "4K": 0.040, "_unit": "per_call"},
        "gpt-image-2-edit":    {"1K": 0.030, "2K": 0.040, "4K": 0.060, "_unit": "per_call"},
        "nano-banana":         {"1K": 0.004, "2K": 0.008, "4K": 0.016, "_unit": "per_call"},
    },
    "video": {
        # per_second pricing
        "seedance-2":               {"480p": 0.050, "720p": 0.100, "1080p": 0.225, "_unit": "per_second"},
        "seedance-2-fast":          {"480p": 0.040, "720p": 0.080,                  "_unit": "per_second"},
        "veo3.1-lite-official":     {"720p": 0.050, "1080p": 0.050, "4K": 0.150,    "_unit": "per_second"},
        "veo3.1-fast-official":     {"720p": 0.018, "1080p": 0.030,                 "_unit": "per_second"},
        "veo3.1-quality-official":  {"720p": 0.120, "1080p": 0.120, "4K": 0.240,    "_unit": "per_second"},
        # per_video pricing (legacy VEO 3.1 base)
        "veo3.1-lite":              {"720p": 0.100, "1080p": 0.100, "4K": 0.150, "_unit": "per_video"},
        "veo3.1-fast":              {"720p": 0.180, "1080p": 0.180, "4K": 0.300, "_unit": "per_video"},
        "veo3.1-quality":           {"720p": 1.00,  "1080p": 1.00,  "4K": 2.00,  "_unit": "per_video"},
    },
}

# Gemini approximate pricing (per 1M tokens, USD). Free tier has 20 req/day quota
# that resets daily — we surface that as "free tier" if user stayed within limits,
# but still track tokens so paid usage is visible.
_GEMINI_PRICES_USD = {
    "gemini-2.5-flash":      {"input": 0.075,  "output": 0.30},
    "gemini-2.5-flash-lite": {"input": 0.0375, "output": 0.15},
    "gemini-2.5-pro":        {"input": 1.25,   "output": 10.0},
    "gemini-2.0-flash":      {"input": 0.10,   "output": 0.40},
    "gemini-3-pro-preview":  {"input": 1.25,   "output": 10.0},
    "gemma-4-31b-it":        {"input": 0.0,    "output": 0.0},  # gemma on AI Studio is free
}

# ElevenLabs approximate cost: $0.18 per 1000 characters on Creator tier.
_ELEVENLABS_PRICE_PER_1K_CHARS_USD = 0.18


def _lookup_price(category: str, model: str, resolution: str | None = None) -> tuple[float, str] | None:
    """Returns (cost_usd, unit) or None if model unknown."""
    table = _POYO_PRICES_USD.get(category, {})
    if model not in table:
        return None
    m_prices = table[model]
    unit = m_prices.get("_unit", "per_call")
    if resolution and resolution in m_prices:
        return float(m_prices[resolution]), unit
    # Pick any non-meta key as fallback (smallest by USD)
    numeric = {k: v for k, v in m_prices.items() if not k.startswith("_")}
    if not numeric:
        return None
    best = min(numeric.values())
    return float(best), unit


class CostTrackerNode(IO.ComfyNode):
    """Print a per-run cost breakdown to a string output.

    Place this AFTER VideoConcat (connect its VIDEO output to the `trigger`
    input here so this node executes last). Reads the in-memory usage log
    that all the other API/cache nodes push entries into, then computes a
    breakdown using a built-in price table sourced from poyo.ai/pricing.

    Cache hits show as $0 with "saved" deltas so you can see how much the
    cache layer is saving you.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="CostTrackerNode",
            display_name="Cost Tracker (run breakdown)",
            category="viral_pipeline/control",
            description=(
                "Reads usage entries logged by Image/Video/Audio/Text caches and the API "
                "nodes (Poyo, Gemini, ElevenLabs) and emits a USD breakdown for this run."
            ),
            inputs=[
                IO.Video.Input(
                    "trigger",
                    tooltip="REQUIRED: connect VideoConcat's output so this node runs LAST, after all video generation logs.",
                ),
                IO.Boolean.Input(
                    "clear_after_read",
                    default=True,
                    tooltip="Clear the usage log after emitting the breakdown (recommended — gives one clean report per run).",
                ),
                IO.String.Input(
                    "currency_label",
                    multiline=False,
                    default="USD",
                    tooltip="Label printed next to amounts. Pricing internally is USD.",
                ),
            ],
            outputs=[IO.String.Output(display_name="cost_breakdown")],
        )

    @classmethod
    async def execute(cls, trigger=None, clear_after_read: bool = True,
                      currency_label: str = "USD") -> IO.NodeOutput:
        raw_entries = get_and_clear_usage_log() if clear_after_read else list(_RUN_USAGE_LOG)

        if not raw_entries:
            return IO.NodeOutput("=== Cost breakdown ===\n(no usage logged this run)")

        # Dedupe: ComfyUI v3 lazy nodes can run execute() multiple times when
        # the same output feeds several downstreams (e.g. ImageCache → PoyoVideo
        # AND ImageCache → BuildVeoPrompt). We dedupe by a stable signature
        # composed of the entry's identifying fields.
        seen = set()
        entries = []
        for e in raw_entries:
            sig = (
                e.get("category"),
                e.get("scene"),
                e.get("model"),
                e.get("resolution"),
                bool(e.get("cache_hit")),
            )
            if sig in seen:
                continue
            seen.add(sig)
            entries.append(e)

        # Bucket entries
        buckets = {"image": [], "video": [], "audio": [], "text": []}
        for e in entries:
            cat = e.get("category", "text")
            buckets.setdefault(cat, []).append(e)

        lines: list[str] = ["═════════════════════════════════════════════",
                            "  💰  COST BREAKDOWN — this run",
                            "═════════════════════════════════════════════"]

        total_paid = 0.0
        total_saved = 0.0

        # ── Images ────────────────────────────────────────────────────
        if buckets["image"]:
            lines.append("\n🖼️  Images (Poyo):")
            for e in buckets["image"]:
                if e.get("cache_hit"):
                    # We can't know what the original cost would have been just
                    # from the cache hit — fall back to a conservative default.
                    saved = 0.03  # rough avg of gpt-image-2-edit @ 1K
                    total_saved += saved
                    lines.append(f"  • {e.get('scene','?'):14s} CACHE HIT (saved ~${saved:.3f})")
                else:
                    price = _lookup_price("image", e.get("model","?"), e.get("resolution"))
                    cost = price[0] if price else 0.0
                    total_paid += cost
                    lines.append(f"  • {e.get('model','?'):20s} {e.get('resolution','-'):>5s} → ${cost:.3f}")

        # ── Videos ────────────────────────────────────────────────────
        lines.append("\n🎬 Videos (Poyo):")
        if not buckets["video"]:
            lines.append("  ⚠ no video entries this run — either VideoConcat didn't fire, PoyoVideo errored before logging, or this CostTracker ran before video nodes finished.")
        else:
            for e in buckets["video"]:
                if e.get("cache_hit"):
                    saved = 0.30  # rough avg veo3.1-lite-official 6s @ 720p
                    total_saved += saved
                    lines.append(f"  • {e.get('scene','?'):14s} CACHE HIT (saved ~${saved:.3f})")
                else:
                    price = _lookup_price("video", e.get("model","?"), e.get("resolution"))
                    if price is None:
                        lines.append(f"  • {e.get('model','?'):26s} {e.get('resolution','-'):>5s} → unknown price")
                    else:
                        per, unit = price
                        if unit == "per_second":
                            cost = per * float(e.get("duration", 0) or 0)
                            lines.append(f"  • {e.get('model','?'):26s} {e.get('resolution','-'):>5s} × {e.get('duration',0)}s → ${cost:.3f}")
                        else:
                            cost = per
                            lines.append(f"  • {e.get('model','?'):26s} {e.get('resolution','-'):>5s} (flat) → ${cost:.3f}")
                        total_paid += cost

        # ── Audio (ElevenLabs) ────────────────────────────────────────
        if buckets["audio"]:
            lines.append("\n🔊 Audio (ElevenLabs TTS):")
            tts_chars = 0
            for e in buckets["audio"]:
                if e.get("cache_hit"):
                    saved = 0.05
                    total_saved += saved
                    lines.append(f"  • {e.get('scene','?'):14s} CACHE HIT (saved ~${saved:.3f})")
                else:
                    chars = int(e.get("chars", 0) or 0)
                    tts_chars += chars
                    cost = chars / 1000.0 * _ELEVENLABS_PRICE_PER_1K_CHARS_USD
                    total_paid += cost
                    lines.append(f"  • {e.get('model','?'):24s} {chars:>5d} chars → ${cost:.4f}")
            if tts_chars:
                lines.append(f"    (total {tts_chars} chars billed)")

        # ── Text (Gemini) ─────────────────────────────────────────────
        if buckets["text"]:
            lines.append("\n🤖 LLM (Gemini / Gemma):")
            llm_calls = 0
            for e in buckets["text"]:
                if e.get("cache_hit"):
                    saved = 0.001
                    total_saved += saved
                    lines.append(f"  • {e.get('scene','?'):14s} CACHE HIT (saved ~${saved:.4f})")
                else:
                    llm_calls += 1
                    model = e.get("model", "?")
                    rates = _GEMINI_PRICES_USD.get(model, {"input": 0.0, "output": 0.0})
                    tin = int(e.get("tokens_in", 0) or 0)
                    tout = int(e.get("tokens_out", 0) or 0) + int(e.get("tokens_thought", 0) or 0)
                    cost = tin/1_000_000*rates["input"] + tout/1_000_000*rates["output"]
                    total_paid += cost
                    free_note = " (free tier ≤20/day)" if model.startswith("gemini-2.5-flash") or model.startswith("gemma") else ""
                    lines.append(f"  • {model:24s} in={tin:>5d} out={tout:>5d} → ${cost:.4f}{free_note}")
            if llm_calls > 20:
                lines.append(f"    ⚠️  {llm_calls} calls in one run — Gemini free tier is 20/day per model")

        # ── Totals ────────────────────────────────────────────────────
        lines.append("\n═════════════════════════════════════════════")
        lines.append(f"  TOTAL THIS RUN: ${total_paid:.3f} {currency_label}")
        lines.append(f"  SAVED BY CACHE: ${total_saved:.3f} {currency_label}")
        if total_paid + total_saved > 0:
            efficiency = total_saved / (total_paid + total_saved) * 100
            lines.append(f"  Cache efficiency: {efficiency:.0f}% of work was reused")
        lines.append("═════════════════════════════════════════════")

        return IO.NodeOutput("\n".join(lines))


class ExtractScriptHeaderNode(IO.ComfyNode):
    """Extract a named header field (e.g. SIGNATURE OBJECT, STORY SPINE) from the full script text.

    Looks for "FIELD_NAME:" and captures everything until the next ALL-CAPS field
    header or the first "SCENE 1:" marker. Useful for feeding the SIGNATURE
    OBJECT bible into BuildVeoPromptNode without manual copy/paste.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ExtractScriptHeaderNode",
            display_name="Extract Script Header Field",
            category="viral_pipeline/text",
            description=(
                "Extract a named field from a script header (e.g. SIGNATURE OBJECT). "
                "Connect the full script string and pick which field to pull."
            ),
            inputs=[
                IO.String.Input(
                    "script",
                    multiline=True,
                    default="",
                    tooltip="Full script text (connect TextCache.STRING or SceneParser source).",
                ),
                IO.String.Input(
                    "field_name",
                    multiline=False,
                    default="SIGNATURE OBJECT",
                    tooltip="Header field to extract. Common values: SIGNATURE OBJECT, STORY SPINE, GOAL, OBSTACLE, STAKES, CHARACTER BIBLE, SETTING BIBLE, VISUAL STYLE.",
                ),
            ],
            outputs=[IO.String.Output(display_name="field_value")],
        )

    @classmethod
    async def execute(cls, script: str, field_name: str) -> IO.NodeOutput:
        import re
        if not script or not script.strip():
            return IO.NodeOutput("")
        name = field_name.strip()
        # Match "FIELD_NAME:" then capture until the next ALL-CAPS header or "SCENE 1:" or end
        pat = (
            rf"{re.escape(name)}\s*:\s*"
            r"(.+?)"
            r"(?=^\s*[A-Z][A-Z _]{2,}\s*:|^\s*SCENE\s*\d\s*:|---|\Z)"
        )
        m = re.search(pat, script, re.DOTALL | re.MULTILINE | re.IGNORECASE)
        return IO.NodeOutput(m.group(1).strip() if m else "")


class BuildVeoPromptNode(IO.ComfyNode):
    """Assemble a clean, conflict-free Veo/Seedance prompt for ONE scene.

    Wrong way (what causes glitches): concatenate Visual + Video Prompt + full
    SIGNATURE OBJECT bible + full SETTING bible. The video model then sees:
      • two competing descriptions of the same action,
      • the signature object in ALL its evolved states at once (so it tries
        to animate the whole evolution inside ONE 6-second clip — glitch),
      • two different camera moves,
    and produces the "constant jumps/teleports" the user reported.

    Right way (what this node does):
      • Use the per-scene Video Prompt as the SOLE action description.
      • Extract ONLY the scene-current state of the signature object from
        the full bible (matched by "Scene N").
      • Append a short, single sentence of setting only if Video Prompt is
        short and lacks context — never the full Setting Bible verbatim.
      • Hard cap output at ~480 chars (Veo cap is 1000, but shorter = sharper).
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="BuildVeoPromptNode",
            display_name="Build Veo Prompt (clean, per-scene)",
            category="viral_pipeline/text",
            description=(
                "Build a clean Veo/Seedance prompt for one scene from the SceneParser's "
                "video_prompt output + the SIGNATURE OBJECT bible (one line, multi-scene). "
                "Extracts only THIS scene's signature-object state to avoid in-clip mutation."
            ),
            inputs=[
                IO.Int.Input(
                    "scene_number",
                    default=1,
                    min=1,
                    max=_MAX_SCENES,
                    tooltip="Which scene this prompt is for (1-6).",
                ),
                IO.String.Input(
                    "video_prompt",
                    multiline=True,
                    default="",
                    tooltip="The Video Prompt for THIS scene (from SceneParser.video_prompt_N).",
                ),
                IO.String.Input(
                    "signature_object_bible",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip=(
                        "Full SIGNATURE OBJECT description from the script header. "
                        "Should contain markers like 'Scene 1', 'Scene 2', ... so this node "
                        "can extract just the current scene's state."
                    ),
                ),
                IO.String.Input(
                    "setting_short",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip="Optional 1-sentence setting hint (NOT the full Setting Bible). Only used if video_prompt < 200 chars.",
                ),
                IO.String.Input(
                    "max_chars",
                    multiline=False,
                    default="480",
                    tooltip="Hard cap on output length (digits only). Veo limit is 1000; under 500 produces sharper video. Empty = default 480.",
                ),
            ],
            outputs=[IO.String.Output(display_name="veo_prompt")],
        )

    @classmethod
    async def execute(cls, scene_number: int, video_prompt: str,
                      signature_object_bible: str = "", setting_short: str = "",
                      max_chars=480) -> IO.NodeOutput:
        # Be robust to empty / non-numeric max_chars coming from a workflow
        # JSON quirk (widget shifts when other widgets are converted to inputs).
        try:
            max_chars = int(str(max_chars).strip()) if str(max_chars).strip() else 480
        except (ValueError, TypeError):
            max_chars = 480
        max_chars = max(200, min(900, max_chars))
        import re

        vp = (video_prompt or "").strip()
        if not vp:
            raise ValueError(
                f"BuildVeoPrompt: video_prompt is empty for scene {scene_number}. "
                "Connect SceneParser.video_prompt_N to this node's video_prompt input."
            )

        # Extract just this scene's signature-object state from the bible.
        # Bible lines typically look like: "Scene 1: burns brightly, Scene 2: flickers, ..."
        # or "it burns brightly in Scene 1, flickers in Scene 2, ..."
        so_state = ""
        bible = (signature_object_bible or "").strip()
        if bible:
            # Pattern 1: "Scene N: <state>" or "Scene N — <state>" up to next "Scene M" or end
            pat1 = rf"Scene\s*{scene_number}\s*[:\-—–]\s*([^.;\n]+?)(?=(?:[,;.]\s*)?Scene\s*\d|[.;\n]|$)"
            m = re.search(pat1, bible, re.IGNORECASE)
            if not m:
                # Pattern 2: "<state> in Scene N" — capture preceding clause
                pat2 = rf"([^.;,\n]+?)\s+in\s+Scene\s*{scene_number}\b"
                m = re.search(pat2, bible, re.IGNORECASE)
            if m:
                so_state = m.group(1).strip().rstrip(",.;")

        # Compose
        parts = [vp.rstrip(".") + "."]
        if so_state:
            # Frame it so Veo knows it's the only state to depict (not a chronology)
            parts.append(f"Signature object — {so_state}.")
        if setting_short and len(vp) < 200:
            parts.append(setting_short.rstrip(".") + ".")

        out = " ".join(parts)
        if len(out) > max_chars:
            out = out[:max_chars].rsplit(" ", 1)[0] + "…"
        return IO.NodeOutput(out)


class TranslateVoiceoversNode(IO.ComfyNode):
    """Batch-translate the 6 scene voiceovers to a target language via Gemini.

    For preview only — output is meant to be wired into a PreviewAny node so the
    user can read what the English voice will say. Pair with TextCacheNode if you
    want to skip the LLM call on subsequent runs.

    Costs: 1 Gemini call per run (cacheable). No video/image API hit.
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="TranslateVoiceoversNode",
            display_name="Translate Voiceovers (preview only)",
            category="viral_pipeline/text",
            description=(
                "Translate all 6 scene voiceovers to a target language in a single LLM call. "
                "Output is a numbered list for human preview — not used anywhere in the pipeline."
            ),
            inputs=[
                IO.String.Input("api_key", multiline=False, default="",
                                tooltip="Google AI Studio API key."),
                IO.String.Input("model", multiline=False, default="gemini-2.5-flash-lite",
                                tooltip="Gemini model id (use a cheap one — translation is simple)."),
                IO.String.Input("target_language", multiline=False, default="Russian",
                                tooltip="Target language name in English (e.g. 'Russian', 'Spanish', 'Japanese')."),
                IO.String.Input("voiceover_1", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_2", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_3", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_4", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_5", multiline=True, default="", optional=True),
                IO.String.Input("voiceover_6", multiline=True, default="", optional=True),
            ],
            outputs=[IO.String.Output(display_name="translated_preview")],
        )

    @classmethod
    async def execute(cls, api_key: str, model: str, target_language: str,
                      voiceover_1: str = "", voiceover_2: str = "", voiceover_3: str = "",
                      voiceover_4: str = "", voiceover_5: str = "", voiceover_6: str = "") -> IO.NodeOutput:
        import aiohttp
        validate_string(api_key, strip_whitespace=True, min_length=1, field_name="api_key")

        lines = [voiceover_1, voiceover_2, voiceover_3, voiceover_4, voiceover_5, voiceover_6]
        non_empty = [(i+1, t.strip()) for i, t in enumerate(lines) if t and t.strip()]
        if not non_empty:
            return IO.NodeOutput("(no voiceovers to translate)")

        numbered = "\n".join(f"{i}. {t}" for i, t in non_empty)
        prompt = (
            f"Translate the following {len(non_empty)} short narration lines into {target_language}. "
            f"Keep the numbering, one line per item. Preserve emotional tone and inner-monologue style. "
            f"Output ONLY the numbered translated lines, no preamble.\n\n{numbered}"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model.strip()}:generateContent"
        headers = {"x-goog-api-key": api_key.strip(), "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise Exception(f"Translate error {resp.status}: {await resp.text()}")
                data = await resp.json()
        cands = data.get("candidates", [])
        if not cands:
            raise Exception("Gemini returned no candidates for translation")
        out = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", [])
                      if not p.get("thought"))
        # Pretty header so it's obvious in PreviewAny
        return IO.NodeOutput(f"=== Voiceovers in {target_language} ===\n{out.strip()}")


class TextCacheNode(IO.ComfyNode):
    """Cache-aware text passthrough — same idea as ImageCacheNode but for strings.

    Put this RIGHT AFTER the script-generating Gemma node. On the first successful
    run the script is written to disk; on subsequent runs with mode='use cached'
    or 'auto' (cache hit), the Gemma node is NOT called — saves API quota and
    lets you safely iterate on downstream nodes without re-rolling the script.

    Cache location: <ComfyUI output_dir>/text_cache/<cache_name>.txt
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="TextCacheNode",
            display_name="Text Cache (skip regen)",
            category="viral_pipeline/control",
            description=(
                "Cache a string (e.g. the generated script) to disk. mode=use cached "
                "skips the upstream LLM call entirely."
            ),
            inputs=[
                IO.String.Input(
                    "cache_name",
                    multiline=False,
                    default="script",
                    tooltip="Filename (no extension) inside <output>/text_cache/.",
                ),
                IO.Combo.Input(
                    "mode",
                    options=["global", "auto", "use cached", "regenerate"],
                    default="auto",
                    tooltip=(
                        "global = follow RunModeNode/global_mode.\n"
                        "auto = use cached file if exists, else generate & save.\n"
                        "use cached = NEVER call upstream (errors if cache missing).\n"
                        "regenerate = always call upstream and overwrite cache."
                    ),
                ),
                IO.Combo.Input(
                    "global_mode",
                    options=["auto", "use cached", "regenerate"],
                    default="auto",
                    optional=True,
                    tooltip="Connect RunModeNode text mode here. Used only when local mode = global.",
                ),
                IO.String.Input(
                    "text",
                    multiline=True,
                    default="",
                    optional=True,
                    lazy=True,
                    force_input=True,
                    tooltip="Upstream text source. Skipped entirely on cache hit.",
                ),
                IO.String.Input(
                    "cache_fingerprint",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip=(
                        "Optional source key for this cached text. Connect the prompt, "
                        "script, or source concept here. In auto mode, TextCache only "
                        "reuses disk cache when this fingerprint matches."
                    ),
                ),
            ],
            outputs=[IO.String.Output()],
        )

    @classmethod
    def _cache_path(cls, cache_name: str) -> str:
        import os
        import folder_paths
        cache_dir = os.path.join(folder_paths.get_output_directory(), "text_cache")
        os.makedirs(cache_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in cache_name.strip()) or "untitled"
        return os.path.join(cache_dir, f"{safe}.txt")

    @classmethod
    def _meta_path(cls, cache_name: str) -> str:
        return cls._cache_path(cache_name) + ".fingerprint"

    @staticmethod
    def _fingerprint(value: str | None) -> str:
        import hashlib
        text = (value or "").strip()
        if not text:
            return ""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def _fingerprint_matches(cls, cache_name: str, cache_fingerprint: str | None) -> bool:
        import os
        expected = cls._fingerprint(cache_fingerprint)
        if not expected:
            return True
        meta_path = cls._meta_path(cache_name)
        if not os.path.exists(meta_path):
            return False
        with open(meta_path, "r", encoding="utf-8") as f:
            return f.read().strip() == expected

    @classmethod
    def _write_fingerprint(cls, cache_name: str, cache_fingerprint: str | None) -> None:
        expected = cls._fingerprint(cache_fingerprint)
        if not expected:
            return
        with open(cls._meta_path(cache_name), "w", encoding="utf-8") as f:
            f.write(expected)

    @classmethod
    def check_lazy_status(
        cls,
        cache_name: str,
        mode: str,
        global_mode: str = "auto",
        text=None,
        cache_fingerprint: str = "",
    ) -> list[str]:
        import os
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        fingerprint_ok = cls._fingerprint_matches(cache_name, cache_fingerprint)
        if effective_mode == "regenerate":
            decision = ["text"]
        elif effective_mode == "use cached":
            decision = []
        elif exists and fingerprint_ok:
            decision = []
        else:
            decision = ["text"]
        print(f"[TextCache] check_lazy_status name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} fingerprint_ok={fingerprint_ok} → upstream_needed={decision}", flush=True)
        return decision

    @classmethod
    async def execute(
        cls,
        cache_name: str,
        mode: str,
        global_mode: str = "auto",
        text: str = "",
        cache_fingerprint: str = "",
    ) -> IO.NodeOutput:
        import os
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        fingerprint_ok = cls._fingerprint_matches(cache_name, cache_fingerprint)
        print(f"[TextCache] execute name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} fingerprint_ok={fingerprint_ok} got_text={bool(text)}", flush=True)

        if effective_mode == "use cached" or (effective_mode == "auto" and os.path.exists(path) and not text):
            if not os.path.exists(path):
                raise RuntimeError(
                    f"TextCache: mode='{effective_mode}' but no cached file at {path}. "
                    f"Run once with mode='regenerate' or 'auto' (with upstream connected) first."
                )
            if not fingerprint_ok:
                raise RuntimeError(
                    f"TextCache: cached text at {path} belongs to a different source prompt. "
                    "Switch mode to 'auto' or 'regenerate' so it can update."
                )
            with open(path, "r", encoding="utf-8") as f:
                log_usage("text", cache_hit=True, scene=cache_name)
                return IO.NodeOutput(f.read())

        if not text:
            raise RuntimeError(
                "TextCache: upstream text is empty. Connect a text-generator output to the 'text' input "
                "or switch mode to 'use cached' (if a cached file exists)."
            )
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        cls._write_fingerprint(cache_name, cache_fingerprint)
        return IO.NodeOutput(text)


class ImageCacheNode(IO.ComfyNode):
    """Cache-aware image passthrough.

    Place this between an image-generator node (e.g. PoyoAIImageNode) and the
    downstream consumer. With `mode = "use cached"` the upstream is NOT
    evaluated (no API call, no credits), and the cached PNG is loaded from
    disk. With `mode = "auto"` the cache is used when present, otherwise the
    image is generated and saved for next time. With `mode = "regenerate"` the
    upstream is always called and overwrites the cache.

    Cache directory: <ComfyUI output_dir>/image_cache/<cache_name>.png
    """

    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ImageCacheNode",
            display_name="Image Cache (skip regen)",
            category="viral_pipeline/control",
            description=(
                "Cache an image to disk; on subsequent runs, optionally bypass the upstream "
                "generator to save API credits. mode=auto reuses the cached file if present."
            ),
            inputs=[
                IO.String.Input(
                    "cache_name",
                    multiline=False,
                    default="scene_1",
                    tooltip=(
                        "Filename (without extension) inside <output>/image_cache/. "
                        "Use a unique name per scene, e.g. scene_1, scene_2, ..."
                    ),
                ),
                IO.Combo.Input(
                    "mode",
                    options=["global", "auto", "use cached", "regenerate"],
                    default="auto",
                    tooltip=(
                        "global = follow RunModeNode/global_mode.\n"
                        "auto = use cached file if exists, else generate & save.\n"
                        "use cached = NEVER call upstream (saves credits even if cache missing — errors out).\n"
                        "regenerate = always call upstream and overwrite cache."
                    ),
                ),
                IO.Combo.Input(
                    "global_mode",
                    options=["auto", "use cached", "regenerate"],
                    default="auto",
                    optional=True,
                    tooltip="Connect RunModeNode.image_mode here. Used only when local mode = global.",
                ),
                IO.Image.Input(
                    "image",
                    optional=True,
                    lazy=True,
                    tooltip="Upstream image source. Skipped entirely when mode='use cached' or 'auto' finds a hit.",
                ),
            ],
            outputs=[IO.Image.Output()],
        )

    @classmethod
    def _cache_path(cls, cache_name: str) -> str:
        import os
        import folder_paths
        cache_dir = os.path.join(folder_paths.get_output_directory(), "image_cache")
        os.makedirs(cache_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in cache_name.strip()) or "untitled"
        return os.path.join(cache_dir, f"{safe}.png")

    @classmethod
    def check_lazy_status(cls, cache_name: str, mode: str, global_mode: str = "auto", image=None) -> list[str]:
        """Decide whether the upstream `image` input is needed before executing.

        Returning an empty list means execute() will be called WITHOUT evaluating
        upstream — which is how we avoid the API call.
        """
        import os
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        if effective_mode == "regenerate":
            decision = ["image"]
        elif effective_mode == "use cached":
            decision = []
        elif exists:  # mode == "auto"  + cache hit
            decision = []
        else:  # mode == "auto" + cache miss
            decision = ["image"]
        print(f"[ImageCache] check_lazy_status name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} → upstream_needed={decision}", flush=True)
        return decision

    @classmethod
    async def execute(cls, cache_name: str, mode: str, global_mode: str = "auto", image=None) -> IO.NodeOutput:
        import os
        import numpy as np
        from PIL import Image as PILImage
        effective_mode = _effective_cache_mode(mode, global_mode)
        path = cls._cache_path(cache_name)
        exists = os.path.exists(path)
        print(f"[ImageCache] execute name={cache_name!r} mode={mode!r} effective={effective_mode!r} "
              f"cache_exists={exists} got_image={image is not None}", flush=True)

        if effective_mode == "use cached" or (effective_mode == "auto" and exists and image is None):
            if not exists:
                raise RuntimeError(
                    f"ImageCache: mode='{effective_mode}' but no cached file at {path}. "
                    f"Run once with mode='regenerate' or 'auto' (with image connected) to create the cache."
                )
            pil = PILImage.open(path).convert("RGB")
            arr = np.array(pil).astype(np.float32) / 255.0
            tensor = torch.from_numpy(arr).unsqueeze(0)  # [1, H, W, 3]
            print(f"[ImageCache] ← read from disk: {path}", flush=True)
            log_usage("image", cache_hit=True, scene=cache_name)
            return IO.NodeOutput(tensor)

        # We have a fresh image from upstream — save it to cache and return it.
        if image is None:
            raise RuntimeError(
                "ImageCache: mode requires upstream image but none was provided. "
                "Connect the generator's output to the 'image' input."
            )
        img = image if image.dim() == 4 else image.unsqueeze(0)
        arr = (img[0].cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
        PILImage.fromarray(arr).save(path, "PNG")
        print(f"[ImageCache] → wrote to disk: {path}", flush=True)
        return IO.NodeOutput(image)


class ImageGateNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ImageGateNode",
            display_name="Pipeline Gate — Image",
            category="viral_pipeline/control",
            description=(
                "Passes an image through only when 'approved' is enabled. "
                "Disable to pause before video generation for this scene."
            ),
            inputs=[
                IO.Image.Input("image"),
                IO.Boolean.Input(
                    "approved",
                    default=True,
                    tooltip="Enable to start video generation. Disable to pause here.",
                ),
            ],
            outputs=[IO.Image.Output()],
        )

    @classmethod
    async def execute(cls, image, approved: bool) -> IO.NodeOutput:
        if not approved:
            raise RuntimeError(
                "Pipeline paused at image stage — review the scene image above, "
                "then check 'approved' and re-run."
            )
        return IO.NodeOutput(image)


# ── Registration ───────────────────────────────────────────────────────────────

class SceneParserExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [
            SceneParserNode,
            VoiceoverRewriteApplyNode,
            AudioConcatNode,
            StringGateNode,
            ImageGateNode,
            ImageCacheNode,
            TextCacheNode,
            VideoCacheNode,
            AudioCacheNode,
            VideoConcatFFmpegNode,
            RunModeNode,
            CacheNamespaceNode,
            CaptionChunkerNode,
            SceneStatusPanelNode,
            PipelineQualityCheckNode,
            ManifestWriterNode,
            TranslateVoiceoversNode,
            CostTrackerNode,
            BuildVeoPromptNode,
            ExtractScriptHeaderNode,
            ApifyDatasetFetchNode,
            ApifyTrendAnalyzerNode,
            ElevenLabsTTSDirectNode,
            SceneScriptRepairNode,
            ConceptFeasibilityScoreNode,
            SceneContinuityAnchorNode,
        ]


async def comfy_entrypoint() -> SceneParserExtension:
    return SceneParserExtension()
