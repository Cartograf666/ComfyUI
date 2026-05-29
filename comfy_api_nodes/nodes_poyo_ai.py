"""ComfyUI nodes for Poyo AI — image generation (GPT Image 2) and video generation (Seedance 2).

API docs: https://docs.poyo.ai
Submit:  POST https://api.poyo.ai/api/generate/submit
Poll:    GET  https://api.poyo.ai/api/generate/status/{task_id}
Upload:  POST https://api.poyo.ai/api/common/upload/base64
"""

import asyncio
import hashlib

import aiohttp

try:
    from comfy_api_nodes.nodes_scene_parser import log_usage
except ImportError:
    def log_usage(*args, **kwargs):  # graceful no-op if scene_parser isn't loaded
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
                    tooltip="Your Poyo AI API key. Keep this private!",
                ),
                IO.String.Input(
                    "model",
                    multiline=False,
                    default="gpt-image-2",
                    tooltip=(
                        "Model to use. Available: gpt-image-2 (text→image), "
                        "gpt-image-2-edit (image→image with reference). "
                        "Edit mode activates automatically when reference_image is connected."
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
                    tooltip="Optional reference image. When connected, switches to gpt-image-2-edit mode.",
                ),
                IO.Float.Input(
                    "image_prompt_strength",
                    default=0.1,
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    tooltip="How strongly the reference image influences the output (edit mode only).",
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
        reference_image=None,
    ) -> IO.NodeOutput:
        validate_string(api_key, strip_whitespace=True, min_length=1, field_name="api_key")
        if not prompt.strip():
            raise ValueError(
                "Image prompt is empty — the SceneParser may have returned an empty string. "
                "Check that the script-writing Gemini node produced a correctly formatted 6-scene script "
                "(each scene must have an 'Image Prompt:' line)."
            )
        validate_string(prompt, strip_whitespace=True, min_length=1, field_name="prompt")

        image_urls: list[str] | None = None
        effective_model = model.strip() or "gpt-image-2"

        if reference_image is not None:
            if "edit" not in effective_model:
                effective_model = "gpt-image-2-edit"
            # Poyo's submit endpoint requires http(s) URLs — upload first.
            ref_url = await _upload_image_to_poyo(api_key, reference_image)
            image_urls = [ref_url]

        request = PoyoSubmitRequest(
            model=effective_model,
            input=PoyoSubmitInput(
                prompt=prompt,
                quality=quality,
                size=aspect_ratio,
                resolution=resolution,
                seed=seed if seed > 0 else None,
                image_urls=image_urls,
                image_strength=image_prompt_strength if image_urls else None,
            ),
        )
        result = await _submit_and_poll(cls, api_key, request, estimated_duration=60)

        if not result.data.files:
            raise Exception(f"Poyo AI returned no files. status={result.data.status}, error={result.data.error_message}")

        log_usage("image", cache_hit=False, model=effective_model, resolution=resolution, quality=quality)
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
                    tooltip="Your Poyo AI API key (poyo.ai/dashboard/api-key). Keep this private!",
                ),
                IO.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Describe the video you want to generate (max 1000 chars for VEO).",
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
                        "• seedance-2 — 4–15s up to 1080p"
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
    ) -> IO.NodeOutput:
        validate_string(api_key, strip_whitespace=True, min_length=1, field_name="api_key")
        if not prompt.strip():
            raise ValueError(
                "Video prompt is empty — the SceneParser may have returned an empty string. "
                "Check that the script-writing node produced a correctly formatted script "
                "(each scene must have a 'Video Prompt:' line)."
            )
        validate_string(prompt, strip_whitespace=True, min_length=1, field_name="prompt")

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

        # VEO official uses `sound`; Seedance uses `generate_audio`. Send only the relevant key.
        input_kwargs = dict(
            prompt=prompt,
            resolution=resolution,
            duration=duration,
            aspect_ratio=aspect_ratio,
            seed=seed if seed > 0 else None,
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
        result = await _submit_and_poll(
            cls, api_key, request, estimated_duration=duration * 20
        )

        if not result.data.files:
            raise Exception(f"Poyo AI returned no files. status={result.data.status}, error={result.data.error_message}")

        log_usage("video", cache_hit=False, model=model, resolution=resolution, duration=duration)
        return IO.NodeOutput(await download_url_to_video_output(result.data.files[0].file_url))


# ── Registration ───────────────────────────────────────────────────────────────

class PoyoAIExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [PoyoAIImageNode, PoyoAISeedanceVideoNode]


async def comfy_entrypoint() -> PoyoAIExtension:
    return PoyoAIExtension()
