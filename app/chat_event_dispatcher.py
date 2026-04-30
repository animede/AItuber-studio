from __future__ import annotations

import asyncio
import base64
import json

from fastapi import WebSocket


class ChatEventDispatcher:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.send_lock = asyncio.Lock()

    async def send_payload(self, payload: dict) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def send_error(
        self,
        error: str,
        *,
        message_id: str | None = None,
        stage: str | None = None,
        fatal: bool | None = None,
    ) -> None:
        payload = {"type": "error", "error": error}
        if message_id is not None:
            payload["message_id"] = message_id
        if stage is not None:
            payload["stage"] = stage
        if fatal is not None:
            payload["fatal"] = fatal
        await self.send_payload(payload)

    async def send_start(
        self,
        *,
        conversation_id: str,
        character_id: str,
        message_id: str,
        audio_enabled: bool,
        selected_style_id: int | None,
    ) -> None:
        await self.send_payload(
            {
                "type": "start",
                "conversation_id": conversation_id,
                "character_id": character_id,
                "message_id": message_id,
                "audio_enabled": audio_enabled,
                "selected_style_id": selected_style_id,
            }
        )

    async def send_delta(self, *, message_id: str, delta: str) -> None:
        await self.send_payload(
            {
                "type": "delta",
                "message_id": message_id,
                "delta": delta,
            }
        )

    async def send_text_end(self, *, message_id: str) -> None:
        await self.send_payload(
            {
                "type": "text_end",
                "message_id": message_id,
            }
        )

    async def send_end(self, *, message_id: str, finish_reason: str) -> None:
        await self.send_payload(
            {
                "type": "end",
                "message_id": message_id,
                "finish_reason": finish_reason,
            }
        )

    async def send_audio(
        self,
        *,
        message_id: str,
        segment_index: int,
        text: str,
        audio_format: str,
        audio_bytes: bytes,
    ) -> None:
        await self.send_payload(
            {
                "type": "audio",
                "message_id": message_id,
                "segment_index": segment_index,
                "text": text,
                "audio_format": audio_format,
                "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
            }
        )


def log_history_summary(
    *,
    conversation_id: str,
    message_id: str,
    threshold_chars: int,
    max_chars: int,
    summary: str,
) -> None:
    print(
        json.dumps(
            {
                "type": "assistant_history_summary",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "summary_threshold_chars": threshold_chars,
                "summary_max_chars": max_chars,
                "summary": summary,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def log_llm_first_chunk_timing(*, conversation_id: str, message_id: str, elapsed_ms: float) -> None:
    print(
        json.dumps(
            {
                "type": "llm_first_chunk_timing",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "elapsed_ms": round(elapsed_ms, 1),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )