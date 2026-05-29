"""Google Gemma/Gemini text generation and TTS — direct Google AI Studio API.

Uses the Generative Language API directly with the user's own Google API key.
Get a free key: https://aistudio.google.com/apikey

Text models:  gemini-2.0-flash, gemini-2.5-flash, gemma-3-27b-it, …
TTS  models:  gemini-2.5-flash-preview-tts, gemini-2.5-pro-preview-tts
"""

import asyncio
import base64

import aiohttp

try:
    from comfy_api_nodes.nodes_scene_parser import log_usage
except ImportError:
    def log_usage(*args, **kwargs):
        pass
import numpy as np
import torch
from pydantic import BaseModel, Field

from comfy_api.latest import IO, ComfyExtension, Input, Types
from comfy_api_nodes.util import (
    ApiEndpoint,
    audio_to_base64_string,
    sync_op,
    tensor_to_base64_string,
    validate_string,
    video_to_base64_string,
)

_GOOGLE_AI_BASE = "https://generativelanguage.googleapis.com"


def _model_supports_system_instruction(model: str) -> bool:
    """Gemma models on AI Studio reject `systemInstruction` with HTTP 500 INTERNAL
    (and often drop the TCP connection mid-flight, surfacing as `Connection reset by peer`).
    Only Gemini-family models accept it."""
    return model.lower().startswith("gemini")


async def _post_with_retry(
    url: str,
    body: dict,
    headers: dict,
    *,
    retries: int = 3,
    timeout: float = 180.0,
) -> dict:
    """POST with exponential backoff on transient 5xx and connection-reset errors.

    Google AI Studio occasionally returns 500/503 or drops the TCP connection under load —
    a single retry usually clears it. Re-raises the underlying error after `retries` attempts.
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status >= 500 and attempt < retries - 1:
                        last_err = Exception(f"Gemini error {resp.status}: {await resp.text()}")
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if resp.status != 200:
                        err = await resp.text()
                        raise Exception(f"Gemini error {resp.status}: {err}")
                    return await resp.json()
        except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("Unreachable")

_TTS_VOICES = [
    "Kore", "Aoede", "Charon", "Fenrir",
    "Leda", "Orus", "Puck", "Zephyr",
]


# ── Text generation node ───────────────────────────────────────────────────────

class GoogleGemmaNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="GoogleGemmaNode",
            display_name="Google Gemma (AI Studio)",
            category="api node/text/Google",
            description=(
                "Generate text using Gemini / Gemma models via your own Google AI Studio key. "
                "Supports multimodal inputs: images, audio, video. "
                "Free key: https://aistudio.google.com/apikey"
            ),
            inputs=[
                IO.String.Input(
                    "api_key",
                    multiline=False,
                    default="",
                    tooltip="Google AI Studio API key. Keep this private!",
                ),
                IO.String.Input(
                    "model",
                    multiline=False,
                    default="gemini-2.0-flash",
                    tooltip=(
                        "Model ID — e.g. gemini-2.0-flash, gemini-2.5-flash, gemma-3-27b-it. "
                        "Full list: https://ai.google.dev/gemini-api/docs/models"
                    ),
                ),
                IO.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="User prompt / instruction.",
                ),
                IO.Image.Input(
                    "images",
                    optional=True,
                    tooltip="Optional image(s) to include as context. Use Batch Images node for multiple.",
                ),
                IO.Audio.Input(
                    "audio",
                    optional=True,
                    tooltip="Optional audio clip to include as context.",
                ),
                IO.Video.Input(
                    "video",
                    optional=True,
                    tooltip="Optional video to include as context.",
                ),
                IO.String.Input(
                    "system_prompt",
                    multiline=True,
                    default="",
                    optional=True,
                    tooltip="System instruction to set the model's behaviour.",
                    advanced=True,
                ),
                IO.Float.Input(
                    "temperature",
                    default=0.7,
                    min=0.0,
                    max=2.0,
                    step=0.05,
                    tooltip="Sampling temperature.",
                    advanced=True,
                ),
                IO.Int.Input(
                    "max_tokens",
                    default=4096,
                    min=64,
                    max=65536,
                    tooltip="Maximum output tokens.",
                    advanced=True,
                ),
            ],
            outputs=[IO.String.Output()],
        )

    @classmethod
    async def execute(
        cls,
        api_key: str,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
        system_prompt: str = "",
        images: Input.Image | None = None,
        audio: Input.Audio | None = None,
        video: Input.Video | None = None,
    ) -> IO.NodeOutput:
        validate_string(api_key, strip_whitespace=True, min_length=1)
        validate_string(model, strip_whitespace=True, min_length=1)
        validate_string(prompt, strip_whitespace=True, min_length=1)

        parts = []

        if images is not None:
            img_batch = images if len(images.shape) == 4 else images.unsqueeze(0)
            for i in range(img_batch.shape[0]):
                b64 = tensor_to_base64_string(img_batch[i])
                parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})

        if audio is not None:
            b64_audio = audio_to_base64_string(audio, container_format="mp3", codec_name="libmp3lame")
            parts.append({"inline_data": {"mime_type": "audio/mp3", "data": b64_audio}})

        if video is not None:
            b64_video = video_to_base64_string(
                video,
                container_format=Types.VideoContainer.MP4,
                codec=Types.VideoCodec.H264,
            )
            parts.append({"inline_data": {"mime_type": "video/mp4", "data": b64_video}})

        model_id = model.strip()
        sys_text = system_prompt.strip() if system_prompt else ""

        # Gemma models reject `systemInstruction` — fold it into the user turn instead.
        if sys_text and not _model_supports_system_instruction(model_id):
            parts.append({"text": f"[SYSTEM INSTRUCTIONS]\n{sys_text}\n\n[USER REQUEST]\n{prompt}"})
        else:
            parts.append({"text": prompt})

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if sys_text and _model_supports_system_instruction(model_id):
            body["systemInstruction"] = {"parts": [{"text": sys_text}]}

        url = f"{_GOOGLE_AI_BASE}/v1beta/models/{model_id}:generateContent"
        headers = {
            "x-goog-api-key": api_key.strip(),
            "Content-Type": "application/json",
        }

        data = await _post_with_retry(url, body, headers)

        candidates = data.get("candidates", [])
        if not candidates:
            raise Exception(
                "Google AI returned no candidates. Check your API key and model name."
            )

        text = "".join(
            part.get("text", "")
            for part in candidates[0].get("content", {}).get("parts", [])
        )
        usage = data.get("usageMetadata", {})
        log_usage(
            "text",
            cache_hit=False,
            model=model_id,
            tokens_in=usage.get("promptTokenCount", 0),
            tokens_out=usage.get("candidatesTokenCount", 0),
            tokens_thought=usage.get("thoughtsTokenCount", 0),
        )
        return IO.NodeOutput(text)


# ── TTS node ───────────────────────────────────────────────────────────────────

class GoogleTTSNode(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="GoogleTTSNode",
            display_name="Google TTS (AI Studio)",
            category="api node/audio/Google",
            description=(
                "Generate speech from text using Gemini TTS models. "
                "Uses the same Google AI Studio API key as the text node."
            ),
            inputs=[
                IO.String.Input(
                    "api_key",
                    multiline=False,
                    default="",
                    tooltip="Google AI Studio API key (same as GoogleGemmaNode).",
                ),
                IO.String.Input(
                    "text",
                    multiline=True,
                    default="",
                    tooltip="Text to convert to speech.",
                ),
                IO.Combo.Input(
                    "voice",
                    options=_TTS_VOICES,
                    default="Kore",
                    tooltip="Voice name.",
                ),
                IO.Combo.Input(
                    "model",
                    options=[
                        "gemini-2.5-flash-preview-tts",
                        "gemini-2.5-pro-preview-tts",
                    ],
                    default="gemini-2.5-flash-preview-tts",
                    tooltip="TTS model to use.",
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
    ) -> IO.NodeOutput:
        validate_string(api_key, strip_whitespace=True, min_length=1)
        validate_string(text, strip_whitespace=True, min_length=1)

        url = f"{_GOOGLE_AI_BASE}/v1beta/models/{model}:generateContent"
        headers = {
            "x-goog-api-key": api_key.strip(),
            "Content-Type": "application/json",
        }
        body = {
            "contents": [{"parts": [{"text": text}], "role": "user"}],
            "generationConfig": {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {
                        "prebuilt_voice_config": {"voice_name": voice}
                    }
                },
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    raise Exception(f"Gemini TTS error {resp.status}: {err}")
                data = await resp.json()

        candidates = data.get("candidates", [])
        if not candidates:
            raise Exception("Gemini TTS returned no candidates")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts or "inlineData" not in parts[0]:
            raise Exception("Gemini TTS returned no audio data in response")

        inline = parts[0]["inlineData"]
        mime = inline.get("mimeType", "audio/pcm;rate=24000")
        sample_rate = 24000
        if "rate=" in mime:
            try:
                sample_rate = int(mime.split("rate=")[1].split(";")[0])
            except Exception:
                pass

        pcm_bytes = base64.b64decode(inline["data"])
        audio_np = (
            np.frombuffer(pcm_bytes, dtype=np.int16)
            .astype(np.float32) / 32768.0
        )
        waveform = torch.from_numpy(audio_np.copy()).unsqueeze(0).unsqueeze(0)

        return IO.NodeOutput({"waveform": waveform, "sample_rate": sample_rate})


# ── Registration ───────────────────────────────────────────────────────────────

class GoogleGemmaExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [GoogleGemmaNode, GoogleTTSNode]


async def comfy_entrypoint() -> GoogleGemmaExtension:
    return GoogleGemmaExtension()
