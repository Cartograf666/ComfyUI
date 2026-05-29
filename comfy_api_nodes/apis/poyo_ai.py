from pydantic import BaseModel, Field


class PoyoSubmitInput(BaseModel):
    prompt: str
    quality: str | None = None
    size: str | None = None
    resolution: str | None = None
    image_urls: list[str] | None = None
    image_strength: float | None = None  # edit mode: how much the reference image influences output
    seed: int | None = None
    # video-specific
    duration: int | None = None
    aspect_ratio: str | None = None
    generate_audio: bool | None = None
    auto_upscale: bool | None = None
    # VEO 3.1 official specific
    sound: bool | None = None
    generation_type: str | None = None


class PoyoSubmitRequest(BaseModel):
    model: str
    input: PoyoSubmitInput
    callback_url: str | None = None


class PoyoTaskData(BaseModel):
    task_id: str
    status: str
    created_time: str | None = None


class PoyoSubmitResponse(BaseModel):
    code: int
    data: PoyoTaskData | None = None
    error: dict | None = None


class PoyoFile(BaseModel):
    file_url: str
    file_type: str | None = None
    label: str | None = None
    format: str | None = None
    content_type: str | None = None
    file_name: str | None = None
    file_size: int | None = None


class PoyoStatusData(BaseModel):
    task_id: str
    status: str
    files: list[PoyoFile] = Field(default_factory=list)
    created_time: str | None = None
    progress: int | None = None
    error_message: str | None = None


class PoyoStatusResponse(BaseModel):
    code: int
    data: PoyoStatusData
