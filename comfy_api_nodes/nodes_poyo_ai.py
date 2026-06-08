"""ComfyUI nodes for Poyo AI — image generation (GPT Image 2) and video generation (Seedance 2).

API docs: https://docs.poyo.ai
Submit:  POST https://api.poyo.ai/api/generate/submit
Poll:    GET  https://api.poyo.ai/api/generate/status/{task_id}
Upload:  POST https://api.poyo.ai/api/common/upload/base64
"""

import asyncio
import hashlib
import random

import aiohttp

try:
    from comfy_api_nodes.nodes_scene_parser import log_seed, log_usage
except ImportError:
    def log_usage(*args, **kwargs):  # graceful no-op if scene_parser isn't loaded
        pass

    def log_seed(*args, **kwargs):  # graceful no-op if scene_parser isn't loaded
        pass

from comfy_api.latest import IO, ComfyExtension
from comfy_api_nodes.apis.poyo_ai import (
    PoyoStatusResponse,
    PoyoSubmitInput,
    PoyoSubmitRequest,
    PoyoSubmitResponse,
)
from comfy_api_nodes.util import (
    ApiEndpoint,
    download_url_to_image_tensor,
    download_url_to_video_output,
    poll_op,
    sync_op,
    tensor_to_base64_string,
    validate_string,
)

_POYO_CONCURRENCY: asyncio.Semaphore | None = None
_POYO_CONCURRENCY_LOOP: asyncio.AbstractEventLoop | None = None


def _poyo_semaphore() -> asyncio.Semaphore:
    global _POYO_CONCURRENCY, _POYO_CONCURRENCY_LOOP
    loop = asyncio.get_running_loop()
    if _POYO_CONCURRENCY is None or _POYO_CONCURRENCY_LOOP is not loop:
        _POYO_CONCURRENCY = asyncio.Semaphore(4)
        _POYO_CONCURRENCY_LOOP = loop
    return _POYO_CONCURRENCY


_SUBMIT_URL = "https://api.poyo.ai/api/generate/submit"
_UPLOAD_URL = "https://api.poyo.ai/api/common/upload/base64"

# Cache uploaded image URLs by tensor hash so the same Scene-1 anchor isn't re-uploaded
# 5 times for 5 downstream scenes. URLs are valid 72h on Poyo storage.
_UPLOAD_CACHE: dict[str, str] = {}


def _tensor_fingerprint(tensor) -> str:
    """Hash the raw bytes of a tensor for upload de-duplication."""
    return hashlib.sha256(tensor.detach().cpu().numpy().tobytes()).hexdigest()


async def _upload_image_to_poyo(api_key: str, tensor) -> str:
    """Upload a single image tensor to Poyo storage; return the http URL.

    Poyo's generation endpoints require image_urls to start with http:// or https://
    (data: URIs and base64 are rejected with HTTP 400). This uploads via their
    base64 endpoint and returns the cdn URL good for 72 hours.

    Uses an in-memory cache keyed on the tensor's content hash so the same image
    (e.g. a Scene-1 style anchor reused as reference by 5 other scenes) is
    uploaded only once per process.
    """
    fp = _tensor_fingerprint(tensor)
    cached = _UPLOAD_CACHE.get(fp)
    if cached:
        return cached

    b64 = tensor_to_base64_string(tensor)
    body = {"base64_data": f"data:image/png;base64,{b64}"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            _UPLOAD_URL,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise Exception(
                    f"Poyo AI image upload failed (HTTP {resp.status}): {err[:300]}"
                )
            data = await resp.json()

    if not data.get("success") or "data" not in data:
        raise Exception(f"Poyo AI upload returned unexpected response: {str(data)[:300]}")

    url = data["data"].get("file_url")
    if not url or not url.startswith(("http://", "https://")):
        raise Exception(f"Poyo AI upload returned no usable file_url: {str(data)[:300]}")

    _UPLOAD_CACHE[fp] = url
    return url


_ATLAS_UPLOAD_CACHE: dict[str, str] = {}


async def _upload_image_to_atlas(api_key: str, tensor) -> str:
    fp = _tensor_fingerprint(tensor)
    cached = _ATLAS_UPLOAD_CACHE.get(fp)
    if cached:
        return cached

    import io
    from PIL import Image as PILImage
    import numpy as np

    img = tensor[0] if tensor.dim() == 4 else tensor
    if img.shape[-1] == 4:
        img = img[..., :3]
    arr = (img.cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    pil = PILImage.fromarray(arr)
    
    bio = io.BytesIO()
    pil.save(bio, format="PNG")
    bio.seek(0)

    data = aiohttp.FormData()
    data.add_field("file", bio, filename="image.png", content_type="image/png")
    
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    upload_url = "https://api.atlascloud.ai/api/v1/model/uploadMedia"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            upload_url,
            data=data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise Exception(
                    f"Atlas Cloud AI image upload failed (HTTP {resp.status}): {err[:300]}"
                )
            res_data = await resp.json()

    url = res_data.get("url")
    if not url:
        raise Exception(f"Atlas Cloud AI upload returned no url in response: {str(res_data)[:300]}")

    _ATLAS_UPLOAD_CACHE[fp] = url
    return url


async def _submit_and_poll_atlas(
    api_key: str,
    submit_url: str,
    payload: dict,
    estimated_duration: int = 60
) -> list[str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(submit_url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise Exception(f"Atlas Cloud AI submit failed (HTTP {resp.status}): {err[:300]}")
            res_data = await resp.json()
            
    data_section = res_data.get("data", {})
    prediction_id = None
    if isinstance(data_section, dict):
        prediction_id = data_section.get("id") or data_section.get("prediction_id")
    if not prediction_id:
        prediction_id = res_data.get("id") or res_data.get("prediction_id")
        
    if not prediction_id:
        raise Exception(f"Atlas Cloud AI submit response missing ID: {str(res_data)[:300]}")
        
    poll_url = f"https://api.atlascloud.ai/api/v1/model/prediction/{prediction_id}"
    poll_interval = 5.0
    elapsed = 0.0
    max_timeout = max(300.0, float(estimated_duration * 3))
    
    while elapsed < max_timeout:
        try:
            from comfy_api_nodes.util._helpers import sleep_with_interrupt
            await sleep_with_interrupt(poll_interval, None, None, None, None)
        except (ImportError, Exception):
            await asyncio.sleep(poll_interval)
            
        elapsed += poll_interval
        
        async with aiohttp.ClientSession() as session:
            async with session.get(poll_url, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[Atlas Cloud] Status check warning (HTTP {resp.status})", flush=True)
                    continue
                poll_data = await resp.json()
                
        data_sec = poll_data.get("data", {})
        if not isinstance(data_sec, dict):
            data_sec = poll_data
            
        status = (data_sec.get("status") or "").strip().lower()
        print(f"[Atlas Cloud] Task {prediction_id} status: {status} (elapsed: {int(elapsed)}s)", flush=True)
        
        if status == "completed":
            outputs = data_sec.get("outputs")
            if not outputs:
                outputs = data_sec.get("output") or poll_data.get("outputs") or poll_data.get("output")
            if isinstance(outputs, str):
                outputs = [outputs]
            if not outputs:
                raise Exception(f"Atlas Cloud AI task completed but returned no outputs: {str(poll_data)[:300]}")
            return outputs
            
        if status in ("failed", "error", "cancelled"):
            error_msg = data_sec.get("error_message") or data_sec.get("error") or "Unknown error"
            raise Exception(f"Atlas Cloud AI task {status}: {error_msg}")
            
    raise Exception(f"Atlas Cloud AI task timed out after {int(elapsed)} seconds.")


def _status_url(task_id: str) -> str:
    return f"https://api.poyo.ai/api/generate/status/{task_id}"


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def _submit_and_poll(
    cls: type[IO.ComfyNode],
    api_key: str,
    request: PoyoSubmitRequest,
    estimated_duration: int,
) -> PoyoStatusResponse:
    submit_ep = ApiEndpoint(path=_SUBMIT_URL, method="POST", headers=_headers(api_key))
    async with _poyo_semaphore():
        submit_resp = await sync_op(
            cls,
            submit_ep,
            response_model=PoyoSubmitResponse,
            data=request,
            wait_label="Submitting task",
            monitor_progress=False,
        )
    if submit_resp.code == 402:
        raise Exception(
            "Poyo AI: Insufficient credits (402). Top up your balance at poyo.ai/dashboard."
        )
    if submit_resp.code != 200 or submit_resp.data is None:
        err = submit_resp.error or {}
        raise Exception(f"Poyo AI submit error (code {submit_resp.code}): {err}")

    task_id = submit_resp.data.task_id
    poll_ep = ApiEndpoint(path=_status_url(task_id), headers=_headers(api_key))
    return await poll_op(
        cls,
        poll_ep,
        response_model=PoyoStatusResponse,
        status_extractor=lambda r: r.data.status,
        progress_extractor=lambda r: r.data.progress,
        completed_statuses=["finished"],
        failed_statuses=["failed"],
        queued_statuses=["not_started"],
        poll_interval=5.0,
        estimated_duration=estimated_duration,
    )


def _is_moderation_error(exc: Exception) -> bool:
    """True when a task failed because Poyo's content filter rejected it.

    Poyo's moderation is probabilistic — the SAME prompt/reference is sometimes
    accepted and sometimes rejected — so these failures are worth retrying.
    """
    msg = str(exc).lower()
    return (
        "does not comply" in msg
        or "platform regulation" in msg
        or ("content" in msg and "regulation" in msg)
    )


async def _submit_and_poll_retry(
    cls: type[IO.ComfyNode],
    api_key: str,
    request: PoyoSubmitRequest,
    estimated_duration: int,
    max_attempts: int = 3,
) -> PoyoStatusResponse:
    """Like _submit_and_poll, but retries flaky content-moderation rejections.

    Each retry uses a fresh random seed so it is a genuinely new generation (a new
    roll of the probabilistic filter), which clears the rejection the vast majority
    of the time. Non-moderation errors are raised immediately. If every attempt is
    rejected, raises a clear, actionable message instead of the raw API dump.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await _submit_and_poll(cls, api_key, request, estimated_duration)
        except Exception as exc:
            if not _is_moderation_error(exc):
                raise
            if attempt < max_attempts:
                new_seed = random.randint(1, 2_147_483_647)
                try:
                    request.input.seed = new_seed
                except Exception:
                    pass
                print(
                    f"[Poyo] content moderation rejection (attempt {attempt}/{max_attempts}); "
                    f"retrying with seed={new_seed} — Poyo's filter is flaky.",
                    flush=True,
                )
                await asyncio.sleep(1.5)
                continue
            raise Exception(
                f"Poyo content moderation rejected this generation on all {max_attempts} attempts. "
                "Poyo's filter is flaky, but persistent rejection usually means the prompt itself "
                "trips it — tweak the wording (avoid graphic/violent terms) and re-run. "
                f"Last API error: {exc}"
            ) from exc


class PoyoAIImageNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="PoyoAIImageNode",
            display_name="Poyo AI — Image Generation",
            category="poyo_ai/image",
            description=(
                "Generate images via Poyo AI (GPT Image 2 and other models). "
                "Requires a Poyo AI API key from poyo.ai/dashboard/api-key."
            ),
            inputs=[
                IO.String.Input(
                    "api_key",
                    multiline=False,
                    default="",
                    optional=True,
                    tooltip="Your API key. Optional if using environment variable.",
                ),
                IO.Combo.Input(
                    "api_provider",
                    options=["poyo", "atlascloud"],
                    default="poyo",
                    tooltip="Select the API provider: Poyo AI or Atlas Cloud AI.",
                ),
                IO.String.Input(
                    "model",
                    multiline=False,
                    default="gpt-image-2",
                    tooltip=(
                        "Model to use. Available: gpt-image-2 (text→image), "
                        "gpt-image-2-edit (image→image with reference). "
                        "Edit mode activates automatically when reference_image is connected. "
                        "For Atlas Cloud, type the desired model path here, e.g. 'black-forest-labs/flux-1-schnell'."
                    ),
                ),
                IO.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Describe the image you want to generate (max 20,000 chars).",
                ),
                IO.Combo.Input(
                    "aspect_ratio",
                    options=["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "auto"],
                    default="16:9",
                    tooltip="Aspect ratio of the output image.",
                ),
                IO.Combo.Input(
                    "quality",
                    options=["low", "medium", "high"],
                    default="low",
                    tooltip="Output quality. Higher = better but more expensive.",
                ),
                IO.Combo.Input(
                    "resolution",
                    options=["1K", "2K", "4K"],
                    default="1K",
                    tooltip="Output resolution.",
                ),
                IO.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=2147483647,
                    control_after_generate=True,
                    tooltip="Seed for reproducibility (0 = random).",
                ),
                IO.Image.Input(
                    "reference_image",
                    optional=True,
                    tooltip=(
                        "Optional reference image (e.g. a character sheet for identity consistency). "
                        "When connected, switches to edit mode. Can be combined with "
                        "base_scene_image to lock both environment and characters."
                    ),
                ),
                # Declared AFTER reference_image so adding it never shifts reference_image's slot
                # index in pre-existing graphs (viral/asmr pipelines) that already wire reference_image.
                IO.Image.Input(
                    "base_scene_image",
                    optional=True,
                    tooltip=(
                        "Optional base-scene / establishing-plate image (environment + camera angle). "
                        "Locked as the primary canvas so a chunk's scenes keep an identical background "
                        "while only foreground characters/objects change. When connected, switches to "
                        "edit mode."
                    ),
                ),
                IO.Float.Input(
                    "image_prompt_strength",
                    default=0.1,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip=(
                        "How strongly the reference image(s) influence the output (edit mode only). "
                        "Applies to the whole edit request."
                    ),
                    advanced=True,
                ),
            ],
            outputs=[IO.Image.Output()],
        )

    @classmethod
    async def execute(
        cls,
        api_key: str,
        model: str,
        prompt: str,
        aspect_ratio: str,
        quality: str,
        resolution: str,
        seed: int,
        image_prompt_strength: float = 0.1,
        base_scene_image=None,
        reference_image=None,
        api_provider: str = "poyo",
    ) -> IO.NodeOutput:
        if not prompt.strip():
            print("[PoyoAIImageNode] Image generation skipped because prompt is empty.", flush=True)
            return IO.NodeOutput(block_execution=None)

        # Resolve/share API key
        import os
        key = api_key.strip()
        if not key:
            key = os.getenv("POYO_API_KEY", "").strip() or os.getenv("ATLAS_API_KEY", "").strip()
        if not key:
            raise ValueError(
                "Poyo/Atlas API Key is missing. Enter it in the node input, "
                "or set the POYO_API_KEY or ATLAS_API_KEY environment variable."
            )
        os.environ["POYO_API_KEY"] = key
        api_key = key
        validate_string(prompt, strip_whitespace=True, min_length=1, field_name="prompt")

        if api_provider == "atlascloud":
            submit_url = "https://api.atlascloud.ai/api/v1/model/generateImage"
            image_urls: list[str] | None = None
            refs = [t for t in (base_scene_image, reference_image) if t is not None]
            if refs:
                image_urls = [await _upload_image_to_atlas(api_key, t) for t in refs]
            
            effective_seed = seed if seed > 0 else random.randint(1, 2147483647)
            payload = {
                "model": model.strip(),
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "size": aspect_ratio,
                "seed": effective_seed,
            }
            if image_urls:
                payload["image"] = image_urls[0]
                payload["image_url"] = image_urls[0]
                payload["image_urls"] = image_urls
                payload["image_strength"] = image_prompt_strength
                
            outputs = await _submit_and_poll_atlas(api_key, submit_url, payload, estimated_duration=60)
            return IO.NodeOutput(await download_url_to_image_tensor(outputs[0]))

        image_urls: list[str] | None = None
        effective_model = model.strip() or "gpt-image-2"

        # Reference images, in request order: the base scene (establishing plate) is the primary
        # canvas to edit, the character sheet follows as an identity reference. Either may be absent.
        refs = [t for t in (base_scene_image, reference_image) if t is not None]
        if refs:
            if "edit" not in effective_model:
                effective_model = "gpt-image-2-edit"
            # Poyo's submit endpoint requires http(s) URLs — upload each first.
            # _upload_image_to_poyo caches by content hash, so a per-chunk plate reused across
            # scenes is uploaded only once.
            image_urls = [await _upload_image_to_poyo(api_key, t) for t in refs]

        # Resolve the seed client-side so the EXACT seed sent to Poyo is always known and
        # recordable — even a seed=0 ("random") run becomes reproducible, because we capture
        # what we actually sent instead of letting the server pick an unknowable seed.
        effective_seed = seed if seed > 0 else random.randint(1, 2147483647)
        request = PoyoSubmitRequest(
            model=effective_model,
            input=PoyoSubmitInput(
                prompt=prompt,
                quality=quality,
                size=aspect_ratio,
                resolution=resolution,
                seed=effective_seed,
                image_urls=image_urls,
                image_strength=image_prompt_strength if image_urls else None,
            ),
        )
        result = await _submit_and_poll_retry(cls, api_key, request, estimated_duration=60)

        if not result.data.files:
            raise Exception(f"Poyo AI returned no files. status={result.data.status}, error={result.data.error_message}")

        # request.input.seed reflects the seed of the SUCCESSFUL generation — a moderation
        # retry replaces it with a fresh seed, so read it back rather than trusting effective_seed.
        final_seed = request.input.seed
        log_usage("image", cache_hit=False, model=effective_model, resolution=resolution, quality=quality, seed=final_seed)
        log_seed("image", model=effective_model, seed=final_seed, resolution=resolution, quality=quality)
        return IO.NodeOutput(await download_url_to_image_tensor(result.data.files[0].file_url))


_VIDEO_MODELS = [
    # VEO 3.1 official — supports 4/6/8s, image-to-video, sound control. Cheapest: lite.
    "veo3.1-lite-official",
    "veo3.1-fast-official",
    "veo3.1-quality-official",
    # VEO 3.1 base — fixed 8s only. lite is text-to-video only (no image refs).
    "veo3.1-lite",
    "veo3.1-fast",
    "veo3.1-quality",
    # ByteDance Seedance 2
    "seedance-2-fast",
    "seedance-2",
    # Atlas Cloud models
    "kling-v2.0",
    "kling-v1.5",
    "luma-ray-v2",
    "luma-ray-v1",
    "runway-gen3",
    "hailuo-v1.5",
]


def _is_veo(model: str) -> bool:
    return model.startswith("veo")


def _is_veo_lite_text_only(model: str) -> bool:
    """veo3.1-lite (non-official) is the only VEO model that rejects image_urls."""
    return model == "veo3.1-lite"


class PoyoAISeedanceVideoNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="PoyoAISeedanceVideoNode",
            display_name="Poyo AI — Video (Seedance / VEO 3.1)",
            category="poyo_ai/video",
            description=(
                "Generate videos via Poyo AI. Supports Seedance 2 and Google VEO 3.1 families. "
                "Cheapest: veo3.1-lite-official (4/6/8s, 720p/1080p). "
                "Requires a Poyo AI API key."
            ),
            inputs=[
                IO.String.Input(
                    "api_key",
                    multiline=False,
                    default="",
                    optional=True,
                    tooltip="Your API key. Optional if using environment variable.",
                ),
                IO.Combo.Input(
                    "api_provider",
                    options=["poyo", "atlascloud"],
                    default="poyo",
                    tooltip="Select the API provider: Poyo AI or Atlas Cloud AI.",
                ),
                IO.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Describe the video you want to generate.",
                ),
                IO.Combo.Input(
                    "model",
                    options=_VIDEO_MODELS,
                    default="veo3.1-lite-official",
                    tooltip=(
                        "Cheapest → priciest:\n"
                        "• veo3.1-lite-official — 4/6/8s, 720p/1080p, image-to-video OK\n"
                        "• veo3.1-fast-official / quality-official — more features, costlier\n"
                        "• veo3.1-lite — TEXT ONLY (no image refs), 8s fixed, 720p+\n"
                        "• seedance-2-fast — 4–15s, 480p/720p, image-to-video OK\n"
                        "• seedance-2 — 4–15s up to 1080p\n"
                        "For Atlas Cloud, type the desired model path here, e.g. 'kling-v2.0'."
                    ),
                ),
                IO.Combo.Input(
                    "resolution",
                    options=["480p", "720p", "1080p", "4k"],
                    default="720p",
                    tooltip=(
                        "Output resolution. VEO 3.1 has NO 480p (min 720p). "
                        "veo3.1-lite-official: no 4k. seedance-2-fast: 480p/720p only."
                    ),
                ),
                IO.Combo.Input(
                    "aspect_ratio",
                    options=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "auto"],
                    default="16:9",
                    tooltip="Video aspect ratio. VEO supports 16:9, 9:16, auto (auto only with image refs).",
                ),
                IO.Int.Input(
                    "duration",
                    default=6,
                    min=4,
                    max=15,
                    step=1,
                    tooltip=(
                        "Video length in seconds. "
                        "VEO 3.1 official: 4/6/8 only. VEO 3.1 base: 8 only. Seedance: 4–15."
                    ),
                ),
                IO.Boolean.Input(
                    "generate_audio",
                    default=True,
                    tooltip=(
                        "Generate audio with the video. "
                        "Seedance: background music/SFX. VEO official: native sound (sound=true)."
                    ),
                ),
                IO.Boolean.Input(
                    "auto_upscale",
                    default=False,
                    tooltip="Auto-upscale (Seedance only — ignored by VEO).",
                ),
                IO.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=2147483647,
                    control_after_generate=True,
                    tooltip="Seed for reproducibility (0 = random).",
                ),
                IO.Autogrow.Input(
                    "images",
                    template=IO.Autogrow.TemplateNames(
                        IO.Image.Input("image"),
                        names=[f"image_{i}" for i in range(1, 11)],
                        min=0,
                    ),
                    tooltip="Optional reference image(s) for image-to-video generation (up to 10). "
                            "Connect image_1 for first-frame control; image_1 + image_2 for start/end control.",
                ),
                IO.Video.Input(
                    "video_1",
                    optional=True,
                    tooltip="Optional reference video (not used by the Poyo API — reserved for future support).",
                ),
                IO.Audio.Input(
                    "audio_1",
                    optional=True,
                    tooltip="Optional reference audio (not used by the Poyo API — reserved for future support).",
                ),
            ],
            outputs=[IO.Video.Output()],
        )

    @classmethod
    async def execute(
        cls,
        api_key: str,
        prompt: str,
        model: str,
        resolution: str,
        duration: int,
        aspect_ratio: str,
        generate_audio: bool,
        auto_upscale: bool,
        seed: int,
        images: IO.Autogrow.Type = None,
        video_1=None,
        audio_1=None,
        api_provider: str = "poyo",
    ) -> IO.NodeOutput:
        # Resolve/share API key
        import os
        key = api_key.strip()
        if not key:
            key = os.getenv("POYO_API_KEY", "").strip() or os.getenv("ATLAS_API_KEY", "").strip()
        if not key:
            raise ValueError(
                "Poyo/Atlas API Key is missing. Enter it in the node input, "
                "or set the POYO_API_KEY or ATLAS_API_KEY environment variable."
            )
        os.environ["POYO_API_KEY"] = key
        api_key = key
        if not prompt.strip():
            raise ValueError(
                "Video prompt is empty — the SceneParser may have returned an empty string. "
                "Check that the script-writing node produced a correctly formatted script "
                "(each scene must have a 'Video Prompt:' line)."
            )
        validate_string(prompt, strip_whitespace=True, min_length=1, field_name="prompt")

        if api_provider == "atlascloud":
            submit_url = "https://api.atlascloud.ai/api/v1/model/generateVideo"
            image_urls: list[str] | None = None
            if images:
                tensors = [t for t in images.values() if t is not None]
                if tensors:
                    image_urls = [await _upload_image_to_atlas(api_key, t) for t in tensors]
            
            effective_seed = seed if seed > 0 else random.randint(1, 2147483647)
            payload = {
                "model": model.strip(),
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "size": aspect_ratio,
                "seed": effective_seed,
                "duration": duration,
            }
            if image_urls:
                payload["image"] = image_urls[0]
                payload["image_url"] = image_urls[0]
                payload["image_urls"] = image_urls
                if len(image_urls) >= 2:
                    payload["first_frame"] = image_urls[0]
                    payload["last_frame"] = image_urls[1]
                    payload["start_image"] = image_urls[0]
                    payload["end_image"] = image_urls[1]
            
            outputs = await _submit_and_poll_atlas(api_key, submit_url, payload, estimated_duration=duration * 20)
            return IO.NodeOutput(await download_url_to_video_output(outputs[0]))

        # Per-model validation — fail fast with a useful message instead of burning credits on a 400.
        if _is_veo(model):
            if len(prompt) > 1000:
                raise ValueError(
                    f"VEO prompt is {len(prompt)} chars but VEO 3.1 caps prompts at 1000. "
                    "Shorten your Video Prompt or have Gemma compress it."
                )
            if resolution == "480p":
                raise ValueError(
                    f"{model}: 480p is not supported by VEO 3.1 (min 720p). "
                    "Pick 720p, or switch to seedance-2-fast for 480p."
                )
            if model == "veo3.1-lite-official" and resolution == "4k":
                raise ValueError("veo3.1-lite-official does not support 4k. Use 720p or 1080p.")
            if "official" in model and duration not in (4, 6, 8):
                raise ValueError(
                    f"{model}: duration must be 4, 6, or 8 (got {duration})."
                )
            if not model.endswith("-official") and duration != 8:
                raise ValueError(
                    f"{model}: only 8-second duration is supported. Use {model}-official for 4/6/8s."
                )

        image_urls: list[str] | None = None
        if images:
            tensors = [t for t in images.values() if t is not None]
            if tensors:
                if _is_veo_lite_text_only(model):
                    raise ValueError(
                        "veo3.1-lite is TEXT-TO-VIDEO ONLY — it rejects image references. "
                        "Use veo3.1-lite-official (cheap, accepts 1–2 images) or veo3.1-fast."
                    )
                veo_image_limit = 2 if model == "veo3.1-lite-official" else 3
                if _is_veo(model) and len(tensors) > veo_image_limit:
                    raise ValueError(
                        f"{model} accepts at most {veo_image_limit} reference image(s); "
                        f"got {len(tensors)}."
                    )
                # Poyo's submit endpoint requires http(s) URLs — upload each tensor first.
                image_urls = []
                for tensor in tensors:
                    url = await _upload_image_to_poyo(api_key, tensor)
                    image_urls.append(url)

        # Resolve the seed client-side so the EXACT seed sent to Poyo is always known and
        # recordable — even a seed=0 ("random") run becomes reproducible, because we capture
        # what we actually sent instead of letting the server pick an unknowable seed.
        effective_seed = seed if seed > 0 else random.randint(1, 2147483647)

        # VEO official uses `sound`; Seedance uses `generate_audio`. Send only the relevant key.
        input_kwargs = dict(
            prompt=prompt,
            resolution=resolution,
            duration=duration,
            aspect_ratio=aspect_ratio,
            seed=effective_seed,
            image_urls=image_urls,
        )
        if _is_veo(model):
            if model.endswith("-official"):
                input_kwargs["sound"] = generate_audio
            # auto_upscale and generate_audio (Seedance flag) are ignored by VEO.
        else:
            input_kwargs["generate_audio"] = generate_audio
            if auto_upscale:
                input_kwargs["auto_upscale"] = True

        request = PoyoSubmitRequest(
            model=model,
            input=PoyoSubmitInput(**input_kwargs),
        )
        result = await _submit_and_poll_retry(
            cls, api_key, request, estimated_duration=duration * 20
        )

        if not result.data.files:
            raise Exception(f"Poyo AI returned no files. status={result.data.status}, error={result.data.error_message}")

        # request.input.seed reflects the seed of the SUCCESSFUL generation — a moderation
        # retry replaces it with a fresh seed, so read it back rather than trusting effective_seed.
        final_seed = request.input.seed
        log_usage("video", cache_hit=False, model=model, resolution=resolution, duration=duration, seed=final_seed)
        log_seed("video", model=model, seed=final_seed, resolution=resolution, duration=duration)
        return IO.NodeOutput(await download_url_to_video_output(result.data.files[0].file_url))


# ── Registration ───────────────────────────────────────────────────────────────

class PoyoAIExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [PoyoAIImageNode, PoyoAISeedanceVideoNode]


async def comfy_entrypoint() -> PoyoAIExtension:
    return PoyoAIExtension()
