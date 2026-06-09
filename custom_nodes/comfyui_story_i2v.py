import sys
import os
import subprocess
import json
import requests
import hashlib
import shutil
import torch
import numpy as np
from PIL import Image

# Comfy may put <repo>/comfy before <repo> on sys.path while loading custom
# nodes. Keep the project root ahead of it so imports like utils.install_util
# resolve to the root package, not comfy/utils.py.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root in sys.path:
    sys.path.remove(project_root)
sys.path.insert(0, project_root)
import utils.install_util  # noqa: F401

from comfy_execution.graph import ExecutionBlocker
from typing_extensions import override
from comfy_api.latest import IO, ComfyExtension, Input, InputImpl

# Add hyperpipeline directory to sys.path to load local libraries
hyperpipeline_dir = os.path.join(project_root, "hyperpipeline")
if hyperpipeline_dir not in sys.path:
    sys.path.append(hyperpipeline_dir)

from download_audio import download_audio
from analyze_audio import analyze_audio
from generate_html import generate_html
from generate_story_html import generate_story_html

# Shared state and cache folder setup
output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
cache_dir = os.path.join(output_dir, "text_cache")
os.makedirs(cache_dir, exist_ok=True)

TEXT_API_PROVIDERS = ("poyo", "google_official", "atlascloud")
SHARED_API_KEYS = {"poyo": "", "google_official": "", "atlascloud": ""}
GOOGLE_GEMINI_MODEL_ALIASES = {
    "gemini-3.1-flash-light": "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite": "gemini-2.5-flash-lite",
    "gemini-2.5-flash-light": "gemini-2.5-flash-lite",
}
STORY_VIDEO_PROMPT_PRESETS = {
    "kling": (
        "Kling I2V preset: preserve the input frame identity, costume, background, lens, "
        "and composition; use one clean physical action with natural subject motion; "
        "keep the camera stable or use a slow cinematic push/pan; no sudden cuts, no scene reset"
    ),
    "seedance": (
        "Seedance I2V preset: write START, ACTION, END, CAMERA, and ATMOSPHERE beats clearly; "
        "keep movement simple, continuous, first-frame faithful, and readable within the clip duration; "
        "avoid over-complex choreography"
    ),
    "generic": (
        "Generic I2V preset: preserve identity, costume, environment, lighting, and camera continuity; "
        "use continuous motion only; avoid abrupt edits and impossible limb motion"
    ),
}

def _safe_cache_name(value, default="story_script_8"):
    text = (value or default).strip() or default
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in text)

def _clean_json_text(raw_text):
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned

def _load_json_text(raw_text, label):
    cleaned = _clean_json_text(raw_text)
    try:
        return json.loads(cleaned)
    except Exception as e:
        raise Exception(f"{label}: failed to parse JSON: {e}\nRaw text:\n{raw_text}")

def _project_cache_name(project_slug, suffix):
    return _safe_cache_name(f"{project_slug or 'story_project'}.{suffix}", f"story_project.{suffix}")

def _text_cache_paths(cache_name):
    safe = _safe_cache_name(cache_name, "story_cache")
    return (
        os.path.join(cache_dir, f"{safe}.json"),
        os.path.join(cache_dir, f"{safe}.metadata.json"),
    )

def _fingerprint_text(*parts):
    joined = "\n\n".join(str(p or "") for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()

def _join_prompt_parts(*parts):
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip()).strip()

def _append_if_missing(prompt, label, value):
    text = (prompt or "").strip()
    value = (value or "").strip()
    if not value:
        return text
    if value.lower() in text.lower():
        return text
    return _join_prompt_parts(text, f"{label}: {value}.")

def _production_image_prompt(prompt, global_style, character_bible, negative_prompt):
    text = _append_if_missing(prompt, "Global style", global_style)
    text = _append_if_missing(text, "Character identity bible", character_bible)
    text = _join_prompt_parts(
        text,
        "Use the storyboard panel as composition reference, but render a full-resolution final keyframe with high detail.",
    )
    return _append_if_missing(text, "Avoid", negative_prompt)

def _model_video_prompt(prompt, video_model, global_style, character_bible, negative_prompt):
    model = (video_model or "").strip().lower()
    text = _append_if_missing(prompt, "Global style", global_style)
    text = _append_if_missing(text, "Character continuity", character_bible)
    if "kling" in model:
        preset = STORY_VIDEO_PROMPT_PRESETS["kling"]
    elif "seedance" in model:
        preset = STORY_VIDEO_PROMPT_PRESETS["seedance"]
    else:
        preset = STORY_VIDEO_PROMPT_PRESETS["generic"]
    text = _join_prompt_parts(text, preset + ".")
    return _append_if_missing(text, "Avoid", negative_prompt)

def _estimate_narration_duration(text, min_seconds=4, max_seconds=15, padding_seconds=1.0, words_per_minute=145):
    words = len([w for w in (text or "").replace("\n", " ").split(" ") if w.strip()])
    if words <= 0:
        return int(min_seconds)
    seconds = (words / max(1, words_per_minute)) * 60.0 + float(padding_seconds)
    return int(max(min_seconds, min(max_seconds, round(seconds))))

def _read_cached_json(cache_name, fingerprint, mode, label):
    cache_path, metadata_path = _text_cache_paths(cache_name)
    cache_exists = os.path.exists(cache_path)
    metadata_exists = os.path.exists(metadata_path)

    if mode == "use cached":
        if not cache_exists:
            raise Exception(f"{label}: cache_mode='use cached' but cache does not exist: {cache_path}")
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    if mode == "auto" and cache_exists and metadata_exists:
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("fingerprint") == fingerprint:
                print(f"[{label}] Loading from cache: {cache_path}")
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            print(f"[{label}] Failed to read cache metadata: {e}")
    return None

def _write_cached_json(cache_name, fingerprint, payload):
    cache_path, metadata_path = _text_cache_paths(cache_name)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fingerprint}, f, ensure_ascii=False, indent=2)

def _resolve_story_api_key(api_provider, api_key):
    provider = (api_provider or "poyo").strip().lower()
    if provider not in TEXT_API_PROVIDERS:
        provider = "poyo"
    if provider == "google_official":
        key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or SHARED_API_KEYS["google_official"]
            or ""
        ).strip()
        missing = "Google/Gemini API key not found in env variables or inputs."
    elif provider == "atlascloud":
        key = (
            api_key
            or os.getenv("ATLAS_API_KEY")
            or os.getenv("POYO_API_KEY")
            or SHARED_API_KEYS["atlascloud"]
            or SHARED_API_KEYS["poyo"]
            or ""
        ).strip()
        missing = (
            "Atlas/Poyo-compatible text API key not found. Story text generation currently "
            "routes atlascloud text requests through the Poyo Gemini-compatible endpoint."
        )
    else:
        key = (api_key or os.getenv("POYO_API_KEY") or SHARED_API_KEYS["poyo"] or "").strip()
        missing = "Poyo API key not found in env variables or inputs."
    if not key:
        raise Exception(missing)
    SHARED_API_KEYS[provider] = key
    return provider, key

def _normalize_story_text_model(api_provider, model):
    provider = (api_provider or "poyo").strip().lower()
    model_id = (model or "").strip()
    if not model_id:
        return "gemini-2.5-flash-lite" if provider == "google_official" else "gemini-2.5-flash"
    if provider == "google_official":
        normalized = GOOGLE_GEMINI_MODEL_ALIASES.get(model_id.lower(), model_id)
        if normalized != model_id:
            print(f"[StoryScript8] Replaced unsupported Google Gemini model '{model_id}' with '{normalized}'.", flush=True)
        return normalized
    return model_id

def _story_model_call(api_provider, api_key, model, system_instruction, user_prompt, max_tokens=16384):
    provider, key = _resolve_story_api_key(api_provider, api_key)
    model = _normalize_story_text_model(provider, model)
    if provider == "google_official":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model.strip().lower()}:generateContent?key={key}"
        headers = {"Content-Type": "application/json"}
        return _call_gemini_api(
            url,
            headers,
            system_instruction,
            user_prompt,
            max_tokens=max_tokens,
            provider_name="Google Gemini",
        )
    return _call_poyo_gemini_api(
        model,
        key,
        system_instruction,
        user_prompt,
        max_tokens=max_tokens,
    )

def _story_text_summary(script_data):
    scenes = script_data.get("scenes", [])
    lines = []
    title = script_data.get("title_ru") or script_data.get("story_title_ru") or "Без названия"
    lines.append(f"Название: {title}")
    if script_data.get("logline_ru"):
        lines.append(f"Логлайн: {script_data['logline_ru']}")
    if script_data.get("style_ru"):
        lines.append(f"Стиль: {script_data['style_ru']}")
    if script_data.get("character_bible_ru"):
        lines.append("\nГерои:")
        lines.append(str(script_data["character_bible_ru"]))
    lines.append("\nСцены:")
    for scene in scenes:
        idx = scene.get("scene_index", len(lines))
        title_ru = scene.get("title_ru", "")
        visual = scene.get("visual_description_ru", "")
        action = scene.get("action_ru", "")
        narration = scene.get("narration_ru", "")
        lines.append(f"{idx}. {title_ru}")
        if visual:
            lines.append(f"   Визуал: {visual}")
        if action:
            lines.append(f"   Действие: {action}")
        if narration:
            lines.append(f"   Озвучка: {narration}")
    return "\n".join(lines).strip()

def _normalize_scenes(script_data, scene_count=8):
    scenes = script_data.get("scenes", [])
    normalized = []
    for idx in range(scene_count):
        scene = dict(scenes[idx]) if idx < len(scenes) and isinstance(scenes[idx], dict) else {}
        scene["scene_index"] = idx + 1
        normalized.append(scene)
    script_data["scenes"] = normalized
    return script_data

def _call_gemini_api(url, headers, system_instruction, user_prompt, max_tokens=16384, provider_name="Gemini"):
    body = {
        "contents": [{
            "role": "user",
            "parts": [{"text": user_prompt}]
        }],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json"
        },
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        }
    }

    import time
    max_retries = 5
    backoff = 2.0
    resp = None

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=90)
            if resp.status_code == 200:
                break
            if resp.status_code in (503, 429, 500, 502, 504) and attempt < max_retries - 1:
                print(f"[StoryScript8] {provider_name} API temporary error (HTTP {resp.status_code}). Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff *= 2.0
                continue
            raise Exception(f"{provider_name} API error (HTTP {resp.status_code}): {resp.text}")
        except (requests.exceptions.RequestException, ConnectionError) as e:
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue
            raise e

    data = resp.json()
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        if data.get("code") not in (None, 200):
            raise Exception(f"{provider_name} API returned code {data.get('code')}: {data}")
        data = data["data"]
    candidates = data.get("candidates", [])
    if not candidates:
        raise Exception(f"{provider_name} API returned no candidates.")

    text = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []))
    return text.strip()

def _call_poyo_gemini_api(model, api_key, system_instruction, user_prompt, max_tokens=16384):
    url = f"https://api.poyo.ai/v1beta/models/{model.strip()}:generateContent"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    raw_text = _call_gemini_api(
        url,
        headers,
        system_instruction,
        user_prompt,
        max_tokens=max_tokens,
        provider_name="Poyo Gemini",
    )
    return raw_text

# ==============================================================================
# 1. STORY SCRIPT GENERATOR (8 SCENES)
# ==============================================================================
class StoryProjectConfig8Node:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "project_slug": ("STRING", {"default": "robot_station_001"}),
                "cache_mode": (["auto", "use cached", "regenerate"], {"default": "auto"}),
            }
        }

    RETURN_TYPES = ("STRING", "COMBO", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "project_slug",
        "cache_mode",
        "ru_script_cache",
        "en_pack_cache",
        "grid_cache",
        "scene_1_image_cache", "scene_2_image_cache", "scene_3_image_cache", "scene_4_image_cache",
        "scene_5_image_cache", "scene_6_image_cache", "scene_7_image_cache", "scene_8_image_cache",
        "scene_1_video_cache", "scene_2_video_cache", "scene_3_video_cache", "scene_4_video_cache",
        "scene_5_video_cache", "scene_6_video_cache", "scene_7_video_cache", "scene_8_video_cache",
    )
    FUNCTION = "configure"
    CATEGORY = "hyperpipeline/story_i2v"

    def configure(self, project_slug, cache_mode):
        slug = _safe_cache_name(project_slug, "story_project")
        image_caches = [_project_cache_name(slug, f"scene_{i:02d}_image") for i in range(1, 9)]
        video_caches = [_project_cache_name(slug, f"scene_{i:02d}_video") for i in range(1, 9)]
        return (
            slug,
            cache_mode,
            _project_cache_name(slug, "ru_script"),
            _project_cache_name(slug, "en_prompt_pack"),
            _project_cache_name(slug, "storyboard_grid"),
            *image_caches,
            *video_caches,
        )


class StoryRuScriptGeneratorNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "idea": ("STRING", {"default": "Космическое приключение двух роботов-исследователей на заброшенной станции", "multiline": True}),
                "style_description": ("STRING", {"default": "cinematic 3D render, retro-futurism, glowing neon lights, highly detailed", "multiline": True}),
                "story_direction": ("STRING", {"default": "Начать с загадки, усилить напряжение в середине, закончить эмоциональным открытием.", "multiline": True}),
                "scene_count": ("INT", {"default": 8, "min": 6, "max": 8, "step": 1}),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_provider": (list(TEXT_API_PROVIDERS), {"default": "poyo"}),
                "project_slug": ("STRING", {"default": "robot_station_001"}),
                "cache_mode": (["auto", "use cached", "regenerate"], {"default": "auto"}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("script_json", "script_text_ru", "fingerprint")
    FUNCTION = "generate"
    CATEGORY = "hyperpipeline/story_i2v"

    def generate(self, idea, style_description, story_direction, scene_count, api_key, model, api_provider, project_slug, cache_mode):
        mode = (cache_mode or "auto").strip().lower()
        if mode not in ("auto", "use cached", "regenerate"):
            mode = "auto"
        scene_count = max(6, min(8, int(scene_count)))
        model = _normalize_story_text_model(api_provider, model)
        fingerprint = _fingerprint_text(idea, style_description, story_direction, scene_count, model, api_provider)
        cache_name = _project_cache_name(project_slug, "ru_script")

        script_data = None if mode == "regenerate" else _read_cached_json(cache_name, fingerprint, mode, "StoryRuScript")
        if script_data is None:
            system_instruction = """Ты профессиональный сценарист коротких вертикальных AI-видео.
Напиши сценарий строго на русском языке и верни только JSON.
Структура:
{
  "title_ru": "...",
  "logline_ru": "...",
  "style_ru": "...",
  "character_bible_ru": "единое описание персонажей, внешности, одежды, отличительных признаков",
  "scenes": [
    {
      "scene_index": 1,
      "title_ru": "...",
      "visual_description_ru": "что должно быть в ключевом кадре",
      "action_ru": "понятное законченное действие внутри будущего видео",
      "camera_ru": "простое движение камеры",
      "narration_ru": "короткая озвучка, если нужна"
    }
  ]
}
Правила:
- Сцен ровно столько, сколько просит пользователь.
- Все персонажи должны сохранять одинаковую внешность во всех сценах.
- Каждая сцена должна иметь конкретное действие, не статичную позу.
- Сцена 1 — хук: первые 2 секунды должны создавать загадку или сильную эмоцию, останавливающую скролл; действие начинается мгновенно, без вступления.
- Последняя сцена — луп: её финальное состояние визуально перекликается с открывающим кадром сцены 1 (то же место/ракурс), чтобы видео бесшовно зацикливалось при повторе, но при этом давало эмоциональную развязку.
- Не используй английский язык в этом JSON."""
            user_prompt = (
                f"Идея:\n{idea}\n\n"
                f"Стилистика:\n{style_description}\n\n"
                f"Примерное развитие сценария:\n{story_direction}\n\n"
                f"Количество сцен: {scene_count}"
            )
            raw_text = _story_model_call(api_provider, api_key, model, system_instruction, user_prompt)
            script_data = _load_json_text(raw_text, "StoryRuScript")
            script_data = _normalize_scenes(script_data, scene_count)
            _write_cached_json(cache_name, fingerprint, script_data)

        script_text = _story_text_summary(script_data)
        return json.dumps(script_data, ensure_ascii=False, indent=2), script_text, fingerprint


class StoryScriptApprovalNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "generated_script_json": ("STRING", {"default": "", "multiline": True}),
                "manual_script_json": ("STRING", {"default": "", "multiline": True}),
                "approved": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("approved_script_json", "approved_script_text_ru", "fingerprint")
    FUNCTION = "approve"
    CATEGORY = "hyperpipeline/story_i2v"

    def approve(self, generated_script_json, manual_script_json, approved):
        if not approved:
            blocker = ExecutionBlocker(None)
            try:
                script_data = _load_json_text(generated_script_json, "StoryScriptApproval preview")
                preview_text = _story_text_summary(script_data)
            except Exception:
                preview_text = generated_script_json or "Russian script is not approved yet."
            return blocker, preview_text, blocker
        chosen = manual_script_json.strip() if manual_script_json and manual_script_json.strip() else generated_script_json
        script_data = _load_json_text(chosen, "StoryScriptApproval")
        scene_count = len(script_data.get("scenes", [])) or 8
        script_data = _normalize_scenes(script_data, max(6, min(8, scene_count)))
        script_json = json.dumps(script_data, ensure_ascii=False, indent=2)
        return script_json, _story_text_summary(script_data), _fingerprint_text(script_json)


class StoryEnPromptPackNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "approved_script_json": ("STRING", {"default": "", "multiline": True}),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "api_provider": (list(TEXT_API_PROVIDERS), {"default": "poyo"}),
                "project_slug": ("STRING", {"default": "robot_station_001"}),
                "cache_mode": (["auto", "use cached", "regenerate"], {"default": "auto"}),
            },
            "optional": {
                "video_model": ("STRING", {"default": "auto"}),
            }
        }

    RETURN_TYPES = (
        "STRING", "STRING",
        "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING",
        "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING",
        "STRING", "STRING", "STRING",
    )
    RETURN_NAMES = (
        "prompt_pack_json", "storyboard_grid_prompt",
        "scene_1_image_prompt", "scene_2_image_prompt", "scene_3_image_prompt", "scene_4_image_prompt",
        "scene_5_image_prompt", "scene_6_image_prompt", "scene_7_image_prompt", "scene_8_image_prompt",
        "scene_1_video_prompt", "scene_2_video_prompt", "scene_3_video_prompt", "scene_4_video_prompt",
        "scene_5_video_prompt", "scene_6_video_prompt", "scene_7_video_prompt", "scene_8_video_prompt",
        "character_bible_en", "negative_prompt", "fingerprint",
    )
    FUNCTION = "translate"
    CATEGORY = "hyperpipeline/story_i2v"

    def translate(self, approved_script_json, api_key, model, api_provider, project_slug, cache_mode, video_model="auto"):
        mode = (cache_mode or "auto").strip().lower()
        if mode not in ("auto", "use cached", "regenerate"):
            mode = "auto"
        model = _normalize_story_text_model(api_provider, model)
        video_model = (video_model or "auto").strip()
        fingerprint = _fingerprint_text(approved_script_json, model, api_provider, video_model)
        cache_name = _project_cache_name(project_slug, "en_prompt_pack")

        pack = None if mode == "regenerate" else _read_cached_json(cache_name, fingerprint, mode, "StoryEnPromptPack")
        if pack is None:
            script_data = _load_json_text(approved_script_json, "StoryEnPromptPack input")
            system_instruction = """You are a production prompt engineer for AI image/video generation.
Convert the Russian story JSON into a compact English production prompt pack.
Return only JSON with this exact structure:
{
  "global_style_prompt": "...",
  "character_bible_en": "stable character descriptions to preserve identity",
  "negative_prompt": "low quality, deformed limbs, extra fingers, warped face, identity drift, frozen action, blurry, text artifacts",
  "storyboard_grid_prompt": "prompt for one storyboard sheet with exactly N separated numbered panels",
  "scenes": [
    {
      "scene_index": 1,
      "image_prompt": "English keyframe prompt with style, characters, setting, composition",
      "video_prompt": "English video prompt with concrete action, camera movement, continuity constraints, anti-deformation constraints"
    }
  ]
}
Rules:
- Keep prompts concise but specific.
- Preserve the same characters across every scene.
- Each video_prompt must describe a clear action and camera movement.
- Add stability instructions: stable identity, consistent costume, no limb distortion.
- Do not include Russian text in generated prompts."""
            raw_text = _story_model_call(
                api_provider,
                api_key,
                model,
                system_instruction,
                json.dumps(script_data, ensure_ascii=False, indent=2),
            )
            pack = _load_json_text(raw_text, "StoryEnPromptPack")
            _write_cached_json(cache_name, fingerprint, pack)

        scenes = pack.get("scenes", [])
        image_prompts = [""] * 8
        video_prompts = [""] * 8
        global_style = pack.get("global_style_prompt", "")
        character_bible = pack.get("character_bible_en", "")
        negative_prompt = pack.get("negative_prompt", "")
        for idx in range(8):
            if idx < len(scenes) and isinstance(scenes[idx], dict):
                image_prompts[idx] = _production_image_prompt(
                    scenes[idx].get("image_prompt", ""),
                    global_style,
                    character_bible,
                    negative_prompt,
                )
                video_prompts[idx] = _model_video_prompt(
                    scenes[idx].get("video_prompt", ""),
                    video_model,
                    global_style,
                    character_bible,
                    negative_prompt,
                )
                scenes[idx]["image_prompt"] = image_prompts[idx]
                scenes[idx]["video_prompt"] = video_prompts[idx]
        storyboard_grid_prompt = pack.get("storyboard_grid_prompt", "")
        if not storyboard_grid_prompt:
            storyboard_grid_prompt = (
                "Create one storyboard sheet with exactly 8 clearly separated numbered panels. "
                f"Global style: {global_style}. "
                f"Characters: {character_bible}. "
                + " ".join(f"Panel {i+1}: {p}" for i, p in enumerate(image_prompts) if p)
            ).strip()
        storyboard_grid_prompt = _append_if_missing(storyboard_grid_prompt, "Avoid", negative_prompt)
        prompt_pack_json = json.dumps(pack, ensure_ascii=False, indent=2)
        return (
            prompt_pack_json,
            storyboard_grid_prompt,
            *image_prompts,
            *video_prompts,
            character_bible,
            negative_prompt,
            _fingerprint_text(prompt_pack_json),
        )


class StoryNarrationPackNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "approved_script_json": ("STRING", {"default": "", "multiline": True}),
                "min_scene_seconds": ("INT", {"default": 4, "min": 2, "max": 15, "step": 1}),
                "max_scene_seconds": ("INT", {"default": 8, "min": 4, "max": 20, "step": 1}),
                "padding_seconds": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 5.0, "step": 0.1}),
                "words_per_minute": ("INT", {"default": 145, "min": 80, "max": 240, "step": 5}),
            }
        }

    RETURN_TYPES = (
        "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING",
        "INT", "INT", "INT", "INT", "INT", "INT", "INT", "INT",
        "STRING", "STRING",
    )
    RETURN_NAMES = (
        "narration_1", "narration_2", "narration_3", "narration_4",
        "narration_5", "narration_6", "narration_7", "narration_8",
        "duration_1", "duration_2", "duration_3", "duration_4",
        "duration_5", "duration_6", "duration_7", "duration_8",
        "fingerprint", "status",
    )
    FUNCTION = "extract"
    CATEGORY = "hyperpipeline/story_i2v"

    def extract(self, approved_script_json, min_scene_seconds, max_scene_seconds, padding_seconds, words_per_minute):
        script_data = _load_json_text(approved_script_json, "StoryNarrationPack")
        scenes = script_data.get("scenes", [])
        narrations = []
        durations = []
        for idx in range(8):
            scene = scenes[idx] if idx < len(scenes) and isinstance(scenes[idx], dict) else {}
            text = (scene.get("narration_ru") or "").strip()
            narrations.append(text)
            durations.append(
                _estimate_narration_duration(
                    text,
                    min_seconds=int(min_scene_seconds),
                    max_seconds=int(max_scene_seconds),
                    padding_seconds=float(padding_seconds),
                    words_per_minute=int(words_per_minute),
                )
            )
        fingerprint = _fingerprint_text(
            approved_script_json,
            min_scene_seconds,
            max_scene_seconds,
            padding_seconds,
            words_per_minute,
            *narrations,
            *durations,
        )
        status = " | ".join(f"{i+1}:{durations[i]}s" for i in range(8))
        return (*narrations, *durations, fingerprint, status)


class StoryCharacterSheetPromptNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "prompt_pack_json": ("STRING", {"default": "", "multiline": True}),
                "character_bible_en": ("STRING", {"default": "", "multiline": True}),
                "negative_prompt": ("STRING", {"default": "", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("character_sheet_prompt", "fingerprint")
    FUNCTION = "build"
    CATEGORY = "hyperpipeline/story_i2v"

    def build(self, prompt_pack_json, character_bible_en, negative_prompt):
        global_style = ""
        if (prompt_pack_json or "").strip():
            try:
                pack = _load_json_text(prompt_pack_json, "StoryCharacterSheetPrompt")
                global_style = pack.get("global_style_prompt", "")
            except Exception:
                global_style = ""
        prompt = _join_prompt_parts(
            "Create one clean character reference sheet for the story cast.",
            character_bible_en,
            "Show every recurring character full body, front view, neutral pose, consistent costume, plain light background.",
            f"Global style: {global_style}." if global_style else "",
            f"Avoid: {negative_prompt}." if negative_prompt else "",
        )
        return prompt, _fingerprint_text(prompt)


class StoryVideoSettingsNode:
    SIZE_MAP = {
        "1080x1920 vertical": ("1080x1920", "1080p", "9:16"),
        "1920x1080 horizontal": ("1920x1080", "1080p", "16:9"),
        "720x1280 vertical draft": ("720x1280", "720p", "9:16"),
        "1280x720 horizontal draft": ("1280x720", "720p", "16:9"),
        "2160x3840 vertical 4k": ("1080x1920", "4k", "9:16"),
        "3840x2160 horizontal 4k": ("1920x1080", "4k", "16:9"),
    }

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "api_provider": (["poyo", "atlascloud"], {"default": "atlascloud"}),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "video_model": ("STRING", {"default": "bytedance/seedance-2.0-fast/image-to-video"}),
                "output_size": (list(s.SIZE_MAP.keys()), {"default": "1080x1920 vertical"}),
                "duration": ("INT", {"default": 6, "min": 4, "max": 15, "step": 1}),
            }
        }

    RETURN_TYPES = ("COMBO", "STRING", "STRING", "COMBO", "COMBO", "INT", "STRING")
    RETURN_NAMES = ("api_provider", "api_key", "video_model", "video_resolution", "aspect_ratio", "duration", "status")
    FUNCTION = "settings"
    CATEGORY = "hyperpipeline/story_i2v"

    def settings(self, api_provider, api_key, video_model, output_size, duration):
        provider = (api_provider or "atlascloud").strip().lower()
        model = (video_model or "").strip()
        if provider == "atlascloud" and model in ("seedance-2", "seedance-2-fast", "veo3.1-lite-official", "veo3.1-fast-official", "veo3.1-quality-official", "veo3.1-lite", "veo3.1-fast", "veo3.1-quality"):
            # Cheapest Atlas i2v until Phase 0 proves a pricier model is worth it
            # (kling-v2.0 stays available when selected explicitly).
            model = "bytedance/seedance-2.0-fast/image-to-video"
        elif provider == "poyo" and model in ("kling-v2.0", "kling-v1.5", "luma-ray-v2", "luma-ray-v1", "runway-gen3", "hailuo-v1.5"):
            model = "seedance-2-fast"
        elif not model:
            model = "bytedance/seedance-2.0-fast/image-to-video" if provider == "atlascloud" else "seedance-2-fast"
        _image_resolution, video_resolution, aspect_ratio = self.SIZE_MAP.get(
            output_size,
            self.SIZE_MAP["1080x1920 vertical"],
        )
        status = (
            f"{provider}: model={model}, {output_size}, "
            f"video_resolution={video_resolution}, aspect_ratio={aspect_ratio}, duration={duration}s"
        )
        return provider, api_key, model, video_resolution, aspect_ratio, int(duration), status


class StoryImageSettingsNode:
    SIZE_MAP = {
        "1K 4:3 1024x768": ("1K", "4:3"),
        "1K 3:4 768x1024": ("1K", "3:4"),
        "1K 1:1 1024x1024": ("1K", "1:1"),
        "1K 2:3 1024x1536": ("1K", "2:3"),
        "1K 3:2 1536x1024": ("1K", "3:2"),
        "2K 16:9 2560x1440": ("2K", "16:9"),
        "2K 9:16 1440x2560": ("2K", "9:16"),
        "4K 16:9 3840x2160": ("4K", "16:9"),
        "4K 9:16 2160x3840": ("4K", "9:16"),
    }

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "api_provider": (["poyo", "atlascloud"], {"default": "atlascloud"}),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "text_to_image_model": ("STRING", {"default": "black-forest-labs/flux-1-dev"}),
                "image_edit_model": ("STRING", {"default": "black-forest-labs/flux-1-dev"}),
                "output_size": (list(s.SIZE_MAP.keys()), {"default": "2K 9:16 1440x2560"}),
                "grid_quality": (["low", "medium", "high"], {"default": "medium"}),
                "scene_quality": (["low", "medium", "high"], {"default": "high"}),
                "image_prompt_strength": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 1.0, "step": 0.01}),
            }
        }

    RETURN_TYPES = ("COMBO", "STRING", "STRING", "STRING", "COMBO", "COMBO", "COMBO", "COMBO", "FLOAT", "STRING")
    RETURN_NAMES = (
        "api_provider",
        "api_key",
        "text_to_image_model",
        "image_edit_model",
        "resolution",
        "aspect_ratio",
        "grid_quality",
        "scene_quality",
        "image_prompt_strength",
        "status",
    )
    FUNCTION = "settings"
    CATEGORY = "hyperpipeline/story_i2v"

    def settings(
        self,
        api_provider,
        api_key,
        text_to_image_model,
        image_edit_model,
        output_size,
        grid_quality,
        scene_quality,
        image_prompt_strength,
    ):
        provider = (api_provider or "atlascloud").strip().lower()
        text_model = (text_to_image_model or "").strip()
        edit_model = (image_edit_model or "").strip()
        if provider == "atlascloud":
            if text_model in ("gpt-image-2", "gpt-image-2-edit", ""):
                text_model = "black-forest-labs/flux-1-dev"
            if edit_model in ("gpt-image-2", "gpt-image-2-edit", ""):
                edit_model = text_model
        else:
            if text_model.startswith("black-forest-labs/") or not text_model:
                text_model = "gpt-image-2"
            if edit_model.startswith("black-forest-labs/") or not edit_model:
                edit_model = "gpt-image-2-edit"
        resolution, aspect_ratio = self.SIZE_MAP.get(output_size, self.SIZE_MAP["2K 9:16 1440x2560"])
        status = (
            f"{provider}: text2image={text_model}, edit={edit_model}, "
            f"{output_size}, grid_quality={grid_quality}, scene_quality={scene_quality}, "
            f"strength={image_prompt_strength}"
        )
        return (
            provider,
            api_key,
            text_model,
            edit_model,
            resolution,
            aspect_ratio,
            grid_quality,
            scene_quality,
            float(image_prompt_strength),
            status,
        )


class StoryImageModelSwitchNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "text_to_image_model": ("STRING", {"default": "gpt-image-2"}),
                "image_edit_model": ("STRING", {"default": "gpt-image-2-edit"}),
                "use_edit_model": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("COMBO",)
    RETURN_NAMES = ("model",)
    FUNCTION = "pick"
    CATEGORY = "hyperpipeline/story_i2v"

    def pick(self, text_to_image_model, image_edit_model, use_edit_model):
        return (image_edit_model if use_edit_model else text_to_image_model,)


class StoryScriptGenerator8Node:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "prompt": ("STRING", {"default": "Космическое приключение двух роботов-исследователей на заброшенной станции", "multiline": True}),
                "api_key": ("STRING", {"default": "", "multiline": False}),
                "model": ("STRING", {"default": "gemini-2.5-flash"}),
                "style_description": ("STRING", {"default": "cinematic 3D render, retro-futurism, glowing neon lights, highly detailed, octane render style", "multiline": True}),
                "api_provider": (list(TEXT_API_PROVIDERS), {"default": "poyo"}),
                "cache_name": ("STRING", {"default": "story_script_8"}),
                "cache_mode": (["auto", "use cached", "regenerate"], {"default": "auto"}),
            }
        }

    RETURN_TYPES = (
        "STRING",
        "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", # visual_prompts
        "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING",           # motion_prompts
        "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", # voiceovers
        "STRING", # cast_summary
        "STRING", # storyboard_grid_prompt
    )
    RETURN_NAMES = (
        "script_json",
        "scene_1_prompt", "scene_2_prompt", "scene_3_prompt", "scene_4_prompt", "scene_5_prompt", "scene_6_prompt", "scene_7_prompt", "scene_8_prompt",
        "motion_prompt_1", "motion_prompt_2", "motion_prompt_3", "motion_prompt_4", "motion_prompt_5", "motion_prompt_6", "motion_prompt_7",
        "voiceover_1", "voiceover_2", "voiceover_3", "voiceover_4", "voiceover_5", "voiceover_6", "voiceover_7", "voiceover_8",
        "cast_summary",
        "storyboard_grid_prompt",
    )
    FUNCTION = "generate_script"
    CATEGORY = "hyperpipeline/story_i2v"

    def generate_script(
        self,
        prompt,
        api_key,
        model,
        style_description,
        api_provider="poyo",
        cache_name="story_script_8",
        cache_mode="auto",
    ):
        global SHARED_API_KEYS
        provider = (api_provider or "poyo").strip().lower()
        if provider not in TEXT_API_PROVIDERS:
            provider = "poyo"

        if provider == "google_official":
            key = (
                api_key
                or os.getenv("GEMINI_API_KEY")
                or os.getenv("GOOGLE_API_KEY")
                or SHARED_API_KEYS["google_official"]
                or ""
            ).strip()
            missing_key_message = "Google/Gemini API key not found in env variables or inputs."
        elif provider == "atlascloud":
            key = (
                api_key
                or os.getenv("ATLAS_API_KEY")
                or os.getenv("POYO_API_KEY")
                or SHARED_API_KEYS["atlascloud"]
                or SHARED_API_KEYS["poyo"]
                or ""
            ).strip()
            missing_key_message = (
                "Atlas/Poyo-compatible text API key not found. StoryScriptGenerator8 "
                "currently routes atlascloud text requests through the Poyo Gemini-compatible endpoint."
            )
        else:
            key = (api_key or os.getenv("POYO_API_KEY") or SHARED_API_KEYS["poyo"] or "").strip()
            missing_key_message = "Poyo API key not found in env variables or inputs."
        if not key:
            raise Exception(missing_key_message)
        SHARED_API_KEYS[provider] = key

        # Check cache
        safe_cache_name = _safe_cache_name(cache_name)
        cache_path = os.path.join(cache_dir, f"{safe_cache_name}.json")
        metadata_path = os.path.join(cache_dir, f"{safe_cache_name}.metadata.json")
        mode = (cache_mode or "auto").strip().lower()
        if mode not in ("auto", "use cached", "regenerate"):
            mode = "auto"

        script_data = None
        cache_exists = os.path.exists(cache_path)
        metadata_exists = os.path.exists(metadata_path)
        if mode == "use cached":
            if not cache_exists:
                raise Exception(
                    f"StoryScript8 cache_mode='use cached' but cache does not exist: {cache_path}. "
                    "Run once with cache_mode='auto' or 'regenerate'."
                )
            print(f"[StoryScript8] Loading script from cache ({safe_cache_name}) without regenerating.")
            with open(cache_path, "r", encoding="utf-8") as f:
                script_data = json.load(f)
        elif mode == "auto" and cache_exists and metadata_exists:
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if (
                    meta.get("prompt") == prompt
                    and meta.get("model") == model
                    and meta.get("style_description") == style_description
                    and meta.get("api_provider", "google_official") == provider
                ):
                    print(f"[StoryScript8] Loading script from cache ({safe_cache_name}).")
                    with open(cache_path, "r", encoding="utf-8") as f:
                        script_data = json.load(f)
            except Exception as e:
                print(f"[StoryScript8] Failed to load cache: {e}")

        if script_data is None:
            system_instruction = """Ты — профессиональный сценарист и режиссер анимационных фильмов.
Твоя задача — сгенерировать подробный сценарный план для 8-сценного фильма на основе промпта пользователя.
Вывод должен быть строго в формате JSON со следующей структурой:
{
  "aspect_ratio": "9:16",
  "style_description": "Описание визуального стиля на русском (например: 'красивый 2D мультфильм, фэнтези, яркие цвета').",
  "story_bible": {
    "logline": "Краткое описание сюжета одной строкой.",
    "tone": "Тон фильма."
  },
  "cast": [
    {
      "name": "Имя персонажа",
      "description": "Визуальное описание персонажа на русском."
    }
  ],
  "scenes": [
    {
      "scene_index": 1,
      "visual_prompt": "Prompt for image generator to create the keyframe for scene 1 (in English). Detailed visual description containing setting, characters, poses, style.",
      "motion_prompt": "Prompt for video generator (first/last frame transition) to animate scene 1 to scene 2 (in English). Describe only the movement/action that happens between scene 1 and scene 2. Do not re-describe the characters/setting.",
      "voiceover": "Текст закадровой озвучки для сцены 1 на русском (15-25 слов)."
    },
    ...
    {
      "scene_index": 8,
      "visual_prompt": "Prompt for image generator to create the keyframe for scene 8 (in English).",
      "motion_prompt": "Prompt for video generator (first/last frame transition) to animate scene 8 or specify final camera move (in English).",
      "voiceover": "Текст закадровой озвучки для сцены 8 на русском (15-25 слов)."
    }
  ]
}

Правила:
1. Поля visual_prompt и motion_prompt должны быть строго на АНГЛИЙСКОМ языке, так как они идут напрямую в генераторы изображений и видео.
2. Поля voiceover, style_description, logline, name и description персонажей должны быть строго на РУССКОМ языке.
3. Прогрессия: сцены 1-8 должны формировать единую завершенную историю.
"""
            user_prompt = f"Тема фильма: {prompt}\nВизуальный стиль: {style_description}"

            if provider == "google_official":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model.strip().lower()}:generateContent?key={key}"
                headers = {"Content-Type": "application/json"}
                print(f"[StoryScript8] Generating script via Google official Gemini API...")
                raw_text = _call_gemini_api(
                    url,
                    headers,
                    system_instruction,
                    user_prompt,
                    provider_name="Google Gemini",
                )
            else:
                print(f"[StoryScript8] Generating script via Poyo Gemini API...")
                raw_text = _call_poyo_gemini_api(
                    model,
                    key,
                    system_instruction,
                    user_prompt,
                )

            # Clean markdown formatting if present
            cleaned_text = raw_text.strip()
            if cleaned_text.startswith("```"):
                lines = cleaned_text.split("\n")
                if lines[0].startswith("```json") or lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned_text = "\n".join(lines).strip()

            try:
                script_data = json.loads(cleaned_text)
                # Cache it
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(script_data, f, ensure_ascii=False, indent=2)
                with open(metadata_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "prompt": prompt,
                            "model": model,
                            "style_description": style_description,
                            "api_provider": provider,
                        },
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )
            except Exception as e:
                raise Exception(f"Failed to parse script JSON: {e}\nRaw response:\n{raw_text}")

        # Unpack script scenes
        scenes = script_data.get("scenes", [])
        visual_prompts = [""] * 8
        motion_prompts = [""] * 7
        voiceovers = [""] * 8

        for idx in range(8):
            if idx < len(scenes):
                sc = scenes[idx]
                visual_prompts[idx] = sc.get("visual_prompt", "")
                if idx < 7:
                    motion_prompts[idx] = sc.get("motion_prompt", "")
                voiceovers[idx] = sc.get("voiceover", "")

        cast = script_data.get("cast", [])
        cast_summary_lines = []
        for i, c in enumerate(cast):
            cast_summary_lines.append(f"{c.get('name')}: {c.get('description')}")
        cast_summary = "\n".join(cast_summary_lines)
        scene_lines = []
        for idx, visual_prompt in enumerate(visual_prompts, start=1):
            cleaned = " ".join((visual_prompt or "").split())
            if cleaned:
                scene_lines.append(f"Panel {idx}: {cleaned}")
        storyboard_grid_prompt = (
            "Create one vertical storyboard sheet with exactly 4 rows and 2 columns, "
            "8 clearly separated numbered panels in left-to-right row order. "
            "Each panel must illustrate the matching scene below, preserving the same characters, "
            "setting continuity, and visual style across all panels. Do not merge panels. "
            f"Global visual style: {style_description}.\n\n"
            + "\n".join(scene_lines)
        ).strip()

        return (
            json.dumps(script_data, ensure_ascii=False, indent=2),
            *visual_prompts,
            *motion_prompts,
            *voiceovers,
            cast_summary,
            storyboard_grid_prompt,
        )

# ==============================================================================
# 2. IMAGE GRID SPLITTER (N x M Slicing)
# ==============================================================================
class ImageGridSplitterNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image": ("IMAGE",),
                "rows": ("INT", {"default": 4, "min": 1, "max": 10, "step": 1}),
                "columns": ("INT", {"default": 2, "min": 1, "max": 10, "step": 1}),
            }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE")
    RETURN_NAMES = ("image_1", "image_2", "image_3", "image_4", "image_5", "image_6", "image_7", "image_8")
    FUNCTION = "split"
    CATEGORY = "hyperpipeline/story_i2v"

    def split(self, image, rows, columns):
        # image shape is [B, H, W, C]
        B, H, W, C = image.shape
        img = image[0] # Select first image in batch, shape [H, W, C]

        row_height = H // rows
        col_width = W // columns

        sliced_images = []
        for r in range(rows):
            for c in range(columns):
                h_start = r * row_height
                h_end = h_start + row_height
                w_start = c * col_width
                w_end = w_start + col_width

                cropped = img[h_start:h_end, w_start:w_end, :]
                sliced_images.append(cropped.unsqueeze(0)) # shape [1, H_crop, W_crop, C]

        # Pad with black image if we don't have up to 8
        while len(sliced_images) < 8:
            dummy = torch.zeros((1, row_height, col_width, C), dtype=image.dtype, device=image.device)
            sliced_images.append(dummy)

        return tuple(sliced_images[:8])

# ==============================================================================
# 3. I2V TRANSITION GENERATOR
# ==============================================================================
class I2VTransitionGeneratorNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "image_1": ("IMAGE",),
                "image_2": ("IMAGE",),
                "image_3": ("IMAGE",),
                "image_4": ("IMAGE",),
                "image_5": ("IMAGE",),
                "image_6": ("IMAGE",),
                "image_7": ("IMAGE",),
                "image_8": ("IMAGE",),
                "motion_prompt_1": ("STRING", {"default": ""}),
                "motion_prompt_2": ("STRING", {"default": ""}),
                "motion_prompt_3": ("STRING", {"default": ""}),
                "motion_prompt_4": ("STRING", {"default": ""}),
                "motion_prompt_5": ("STRING", {"default": ""}),
                "motion_prompt_6": ("STRING", {"default": ""}),
                "motion_prompt_7": ("STRING", {"default": ""}),
                "model_vendor": (["ByteDance_Seedance", "Kling_AI", "LTXV_Proxy"], {"default": "ByteDance_Seedance"}),
                "resolution": (["480p", "720p", "1080p"], {"default": "720p"}),
                "duration_seconds": ("INT", {"default": 5, "min": 3, "max": 10, "step": 1}),
            },
            "optional": {
                "api_key": ("STRING", {"default": ""}),
                "enabled": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("VIDEO", "VIDEO", "VIDEO", "VIDEO", "VIDEO", "VIDEO", "VIDEO")
    RETURN_NAMES = ("video_1", "video_2", "video_3", "video_4", "video_5", "video_6", "video_7")
    FUNCTION = "generate_transitions"
    CATEGORY = "hyperpipeline/story_i2v"

    async def generate_transitions(self, image_1, image_2, image_3, image_4, image_5, image_6, image_7, image_8,
                                   motion_prompt_1, motion_prompt_2, motion_prompt_3, motion_prompt_4, motion_prompt_5, motion_prompt_6, motion_prompt_7,
                                   model_vendor, resolution, duration_seconds, api_key="", enabled=True):
        if not enabled:
            # Return tuple of blockers/dummies
            return tuple(ExecutionBlocker(None) for _ in range(7))

        images = [image_1, image_2, image_3, image_4, image_5, image_6, image_7, image_8]
        prompts = [motion_prompt_1, motion_prompt_2, motion_prompt_3, motion_prompt_4, motion_prompt_5, motion_prompt_6, motion_prompt_7]

        videos = []
        for i in range(7):
            first_frame = images[i]
            last_frame = images[i+1]
            prompt = prompts[i]

            print(f"[I2VTransition] Generating Transition {i+1}/7 ({model_vendor}, prompt: '{prompt[:40]}')...")

            if model_vendor == "ByteDance_Seedance":
                from comfy_api_nodes.nodes_bytedance import ByteDanceFirstLastFrameNode
                # Execute using ByteDanceFirstLastFrameNode
                node_output = await ByteDanceFirstLastFrameNode.execute(
                    model="seedance-1-0-lite-i2v-250428",
                    prompt=prompt or "smooth movement transition",
                    first_frame=first_frame,
                    last_frame=last_frame,
                    resolution=resolution,
                    aspect_ratio="adaptive",
                    duration=duration_seconds,
                    seed=42,
                    camera_fixed=False,
                    watermark=False
                )
                videos.append(node_output.get_output(0))
            elif model_vendor == "Kling_AI":
                from comfy_api_nodes.nodes_kling import KlingFirstLastFrameNode
                node_output = await KlingFirstLastFrameNode.execute(
                    model="kling-v1-5-std",
                    prompt=prompt or "smooth panning motion",
                    first_frame=first_frame,
                    last_frame=last_frame,
                    duration=f"{duration_seconds}s",
                    cfg=5.0,
                    mode="professional",
                    aspect_ratio="adaptive"
                )
                videos.append(node_output.get_output(0))
            else:
                # Fallback to standard ImageToVideoNode (LTXV) on first_frame only
                from comfy_api_nodes.nodes_ltxv import ImageToVideoNode
                node_output = await ImageToVideoNode.execute(
                    image=first_frame,
                    model="LTX-2 (Fast)",
                    prompt=prompt or "slow pan forward",
                    duration=duration_seconds,
                    resolution="1920x1080" if resolution == "1080p" else "1920x1080", # match LTX resolution combos
                    fps=25,
                    generate_audio=False
                )
                videos.append(node_output.get_output(0))

        return tuple(videos)

# ==============================================================================
# 4. VIDEO CONCATENATOR (8 SCENES / 7 TRANSITIONS)
# ==============================================================================
class VideoConcat8FFmpegNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="VideoConcat8FFmpegNode",
            display_name="Video Concat (8 Scenes / 7 Transitions)",
            category="hyperpipeline/story_i2v",
            description="Losslessly concatenates up to 8 video segments and mixes them with audio tracks using FFmpeg.",
            inputs=[
                IO.Video.Input("video_1", optional=True),
                IO.Video.Input("video_2", optional=True),
                IO.Video.Input("video_3", optional=True),
                IO.Video.Input("video_4", optional=True),
                IO.Video.Input("video_5", optional=True),
                IO.Video.Input("video_6", optional=True),
                IO.Video.Input("video_7", optional=True),
                IO.Video.Input("video_8", optional=True),
                IO.AnyType.Input("audio_1", optional=True),
                IO.AnyType.Input("audio_2", optional=True),
                IO.AnyType.Input("audio_3", optional=True),
                IO.AnyType.Input("audio_4", optional=True),
                IO.AnyType.Input("audio_5", optional=True),
                IO.AnyType.Input("audio_6", optional=True),
                IO.AnyType.Input("audio_7", optional=True),
                IO.AnyType.Input("audio_8", optional=True),
                IO.AnyType.Input("background_music", optional=True),
                IO.Float.Input("background_music_volume", default=0.08, min=0.0, max=1.0, step=0.01),
                IO.String.Input("filename_prefix", default="story_i2v_final"),
                IO.Combo.Input("mode", options=["stream copy (fast)", "re-encode (compatible)"], default="stream copy (fast)"),
                IO.Combo.Input("subtitles", options=["off", "burned in only"], default="off"),
                IO.String.Input("subtitle_text_1", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_2", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_3", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_4", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_5", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_6", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_7", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_text_8", multiline=True, default="", optional=True),
                IO.String.Input("subtitle_style", default="FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=40"),
            ],
            outputs=[IO.Video.Output()]
        )

    @classmethod
    async def execute(cls, video_1=None, video_2=None, video_3=None, video_4=None, video_5=None, video_6=None, video_7=None, video_8=None,
                      audio_1=None, audio_2=None, audio_3=None, audio_4=None, audio_5=None, audio_6=None, audio_7=None, audio_8=None,
                      background_music=None, background_music_volume=0.08,
                      filename_prefix="story_i2v_final", mode="stream copy (fast)", subtitles="off",
                      subtitle_text_1="", subtitle_text_2="", subtitle_text_3="", subtitle_text_4="",
                      subtitle_text_5="", subtitle_text_6="", subtitle_text_7="", subtitle_text_8="",
                      subtitle_style=""):
        import io
        import os
        import shutil
        import subprocess
        import tempfile
        import folder_paths
        from comfy_api.latest import _input_impl as _ii

        # Use system ffmpeg path or standard command name
        ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
        ffprobe_bin = shutil.which("ffprobe") or "ffprobe"

        def write_audio_input(audio, path: str) -> str:
            if isinstance(audio, str):
                return audio
            waveform = audio["waveform"]
            if waveform.dim() == 3:
                waveform = waveform[0]
            waveform = waveform.detach().cpu()
            if waveform.dim() == 1:
                waveform = waveform.unsqueeze(0)

            import wave
            samples = waveform.transpose(0, 1).numpy()
            samples = np.clip(samples, -1.0, 1.0)
            pcm = (samples * 32767.0).astype(np.int16)
            with wave.open(path, "wb") as wav:
                wav.setnchannels(pcm.shape[1])
                wav.setsampwidth(2)
                wav.setframerate(int(audio["sample_rate"]))
                wav.writeframes(pcm.tobytes())
            return path

        scene_pairs = [
            (video_1, audio_1), (video_2, audio_2), (video_3, audio_3), (video_4, audio_4),
            (video_5, audio_5), (video_6, audio_6), (video_7, audio_7), (video_8, audio_8),
        ]
        connected_pairs = [(v, a) for v, a in scene_pairs if v is not None]
        if not connected_pairs:
            print("[VideoConcat8FFmpegNode] No video inputs were connected or approved. Generating a 1-second silent black placeholder video.", flush=True)
            out_dir = folder_paths.get_output_directory()
            os.makedirs(out_dir, exist_ok=True)
            full_folder, filename, counter, subfolder, fname_prefix = folder_paths.get_save_image_path(
                filename_prefix, out_dir
            )
            out_path = os.path.join(full_folder, f"{filename}_{counter:05d}.mp4")
            cmd = [
                ffmpeg_bin, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=1",
                "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
                "-c:v", "libx264", "-t", "1", "-pix_fmt", "yuv420p",
                out_path
            ]
            subprocess.run(cmd, capture_output=True, text=True)
            return IO.NodeOutput(_ii.VideoFromFile(out_path))

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

            # Dump videos & audios to disk, match dimensions/samplerates
            clip_paths = []
            for idx, (vid, scene_audio) in enumerate(connected_pairs):
                src = vid.get_stream_source()
                p = os.path.join(tmpdir, f"clip_{idx}.mp4")
                if isinstance(src, str) and os.path.exists(src):
                    shutil.copy2(src, p)
                elif isinstance(src, io.BytesIO):
                    src.seek(0)
                    with open(p, "wb") as f:
                        shutil.copyfileobj(src, f)
                else:
                    raise ValueError(f"Unable to read video input index {idx}")

                # If audio is present, merge it with video, else keep silent track
                if scene_audio is not None:
                    audio_file_path = write_audio_input(scene_audio, os.path.join(tmpdir, f"audio_{idx}.wav"))

                    # Merge audio & video together
                    merged_p = os.path.join(tmpdir, f"clip_audio_{idx}.mp4")
                    cmd = [
                        ffmpeg_bin, "-y",
                        "-i", p,
                        "-i", audio_file_path,
                        "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac",
                        merged_p
                    ]
                    run_ffmpeg(cmd, f"Audio merge for scene {idx}")
                    clip_paths.append(merged_p)
                else:
                    clip_paths.append(p)

            # Concat list file
            list_file_path = os.path.join(tmpdir, "concat_list.txt")
            with open(list_file_path, "w", encoding="utf-8") as f:
                for c in clip_paths:
                    f.write(f"file '{c}'\n")

            # Concat MP4s
            concat_cmd = [
                ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
                "-i", list_file_path,
                "-c", "copy" if mode == "stream copy (fast)" else "libx264",
                out_path
            ]
            run_ffmpeg(concat_cmd, "FFmpeg video concatenation")

            if background_music is not None and float(background_music_volume) > 0:
                music_path = write_audio_input(background_music, os.path.join(tmpdir, "background_music.wav"))
                mixed_path = os.path.join(full_folder, f"{filename}_{counter:05d}_music.mp4")
                audio_probe = subprocess.run(
                    [
                        ffprobe_bin, "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", out_path,
                    ],
                    capture_output=True,
                    text=True,
                )
                has_audio = bool(audio_probe.stdout.strip())
                volume = max(0.0, min(1.0, float(background_music_volume)))
                if has_audio:
                    filter_complex = (
                        f"[1:a]volume={volume}[music];"
                        "[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]"
                    )
                else:
                    filter_complex = f"[1:a]volume={volume}[a]"
                music_cmd = [
                    ffmpeg_bin, "-y",
                    "-i", out_path,
                    "-stream_loop", "-1", "-i", music_path,
                    "-filter_complex", filter_complex,
                    "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                    mixed_path,
                ]
                run_ffmpeg(music_cmd, "FFmpeg background music mix")
                shutil.move(mixed_path, out_path)

        print(f"[VideoConcat8] Successfully generated final stitched video: {out_path}")
        return IO.NodeOutput(InputImpl.VideoFromFile(out_path))


class VideoGateNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="VideoGateNode",
            display_name="Pipeline Gate — Video",
            category="hyperpipeline/story_i2v",
            description=(
                "Passes a video through only when 'approved' is enabled. "
                "Use it after previewing a generated scene clip and before final concatenation."
            ),
            inputs=[
                IO.Video.Input("video", optional=True, lazy=True),
                IO.Boolean.Input(
                    "approved",
                    default=False,
                    tooltip="Enable after reviewing the generated clip to allow final stitching.",
                ),
            ],
            outputs=[IO.Video.Output()],
        )

    @classmethod
    def check_lazy_status(cls, approved: bool, video=None) -> list[str]:
        if not approved:
            return []
        needed = []
        if video is None:
            needed.append("video")
        return needed

    @classmethod
    async def execute(cls, approved: bool, video=None) -> IO.NodeOutput:
        if not approved:
            return IO.NodeOutput(None)
        return IO.NodeOutput(video)

# ==============================================================================
# COMPY EXTENSION REGISTRATION
# ==============================================================================
NODE_CLASS_MAPPINGS = {
    "StoryProjectConfig8Node": StoryProjectConfig8Node,
    "StoryRuScriptGeneratorNode": StoryRuScriptGeneratorNode,
    "StoryScriptApprovalNode": StoryScriptApprovalNode,
    "StoryEnPromptPackNode": StoryEnPromptPackNode,
    "StoryNarrationPackNode": StoryNarrationPackNode,
    "StoryCharacterSheetPromptNode": StoryCharacterSheetPromptNode,
    "StoryVideoSettingsNode": StoryVideoSettingsNode,
    "StoryImageSettingsNode": StoryImageSettingsNode,
    "StoryImageModelSwitchNode": StoryImageModelSwitchNode,
    "StoryScriptGenerator8Node": StoryScriptGenerator8Node,
    "ImageGridSplitterNode": ImageGridSplitterNode,
    "I2VTransitionGeneratorNode": I2VTransitionGeneratorNode,
    "VideoConcat8FFmpegNode": VideoConcat8FFmpegNode,
    "VideoGateNode": VideoGateNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StoryProjectConfig8Node": "Story Project Config (8 Scenes)",
    "StoryRuScriptGeneratorNode": "RU Story Script Generator",
    "StoryScriptApprovalNode": "Approve / Edit RU Script",
    "StoryEnPromptPackNode": "EN Production Prompt Pack",
    "StoryNarrationPackNode": "RU Narration + Timing Pack",
    "StoryCharacterSheetPromptNode": "Character Sheet Prompt",
    "StoryVideoSettingsNode": "Story Video Settings",
    "StoryImageSettingsNode": "Story Image Settings",
    "StoryImageModelSwitchNode": "Story Image Model Switch",
    "StoryScriptGenerator8Node": "Story Script Generator (8 Scenes)",
    "ImageGridSplitterNode": "Image Grid Splitter (N x M)",
    "I2VTransitionGeneratorNode": "I2V Transition Video Generator",
    "VideoConcat8FFmpegNode": "Video Concat (8 Scenes / 7 Transitions)",
    "VideoGateNode": "Pipeline Gate — Video",
}

class StoryI2vExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [
            StoryProjectConfig8Node,
            StoryRuScriptGeneratorNode,
            StoryScriptApprovalNode,
            StoryEnPromptPackNode,
            StoryNarrationPackNode,
            StoryCharacterSheetPromptNode,
            StoryVideoSettingsNode,
            StoryImageSettingsNode,
            StoryImageModelSwitchNode,
            StoryScriptGenerator8Node,
            ImageGridSplitterNode,
            I2VTransitionGeneratorNode,
            VideoConcat8FFmpegNode,
            VideoGateNode,
        ]

async def comfy_entrypoint() -> StoryI2vExtension:
    return StoryI2vExtension()
