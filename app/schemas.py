from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .conversation_store import MessageRole


class MessageSchema(BaseModel):
    message_id: str
    role: MessageRole
    content: str
    timestamp: str


class CharacterSchema(BaseModel):
    id: str
    name: str
    display_name: str
    short_description: str
    theme_color: str
    ui_accent_color: str
    avatar_label: str
    visual_type: Literal["image", "video", "none"]
    visual_path: str
    talking_visual_path: str
    waiting_visual_path: str
    voice_name: str
    greeting: str
    role_text: str
    tags: list[str]
    is_default: bool


class ConversationCreateRequest(BaseModel):
    character_id: str | None = None


class ChatStreamRequest(BaseModel):
    conversation_id: str
    message: str | None = Field(default=None, min_length=1)
    role: str | None = Field(default=None, min_length=1)
    max_history: int | None = Field(default=None, ge=0, le=20)
    summary_threshold_chars: int | None = Field(default=None, ge=0, le=4000)
    summary_max_chars: int | None = Field(default=None, ge=1, le=2000)
    audio_enabled: bool = False
    tts_split_on_soft_boundaries: bool = False
    selected_style_id: int | None = None
    input_audio_b64: str | None = None
    input_audio_format: str | None = Field(default=None, min_length=1)

    @field_validator("message", "role", "input_audio_b64", "input_audio_format", mode="before")
    @classmethod
    def normalize_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("selected_style_id", mode="before")
    @classmethod
    def normalize_selected_style_id(cls, value: object) -> int | None | object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return int(stripped)
        return value


class ImageAnalysisStreamRequest(BaseModel):
    conversation_id: str
    role: str | None = Field(default=None, min_length=1)
    summary_threshold_chars: int | None = Field(default=None, ge=0, le=4000)
    summary_max_chars: int | None = Field(default=None, ge=1, le=2000)
    audio_enabled: bool = False
    fast_image_analysis: bool = False
    tts_split_on_soft_boundaries: bool = False
    selected_style_id: int | None = None
    image_b64: str = Field(min_length=1)
    image_format: str = Field(default="jpeg", min_length=1)

    @field_validator("conversation_id", "role", "image_b64", "image_format", mode="before")
    @classmethod
    def normalize_stream_image_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("selected_style_id", mode="before")
    @classmethod
    def normalize_stream_image_selected_style_id(cls, value: object) -> int | None | object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return int(stripped)
        return value


class ImageAnalysisRequest(BaseModel):
    conversation_id: str
    image_b64: str = Field(min_length=1)
    image_format: str = Field(default="jpeg", min_length=1)
    role_text: str | None = None
    audio_enabled: bool = False
    fast_image_analysis: bool = False
    selected_style_id: int | None = None

    @field_validator("conversation_id", "image_b64", "image_format", "role_text", mode="before")
    @classmethod
    def normalize_image_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("selected_style_id", mode="before")
    @classmethod
    def normalize_image_selected_style_id(cls, value: object) -> int | None | object:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return int(stripped)
        return value


class YouTubeCommentStartRequest(BaseModel):
    conversation_id: str
    video_id: str = Field(min_length=1)


class YouTubeCommentStopRequest(BaseModel):
    conversation_id: str
