from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
    message: str = Field(min_length=1)
    role: str | None = Field(default=None, min_length=1)
    max_history: int | None = Field(default=None, ge=0, le=20)
    summary_threshold_chars: int | None = Field(default=None, ge=0, le=4000)
    summary_max_chars: int | None = Field(default=None, ge=1, le=2000)
    audio_enabled: bool
    tts_split_on_soft_boundaries: bool = False
    selected_style_id: int | None = None


class YouTubeCommentStartRequest(BaseModel):
    conversation_id: str
    video_id: str = Field(min_length=1)


class YouTubeCommentStopRequest(BaseModel):
    conversation_id: str
