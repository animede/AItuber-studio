from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.background_agent import BackgroundProposal, ProposalKind
from app.character_registry import get_default_character
import app.api_chat as api_chat


class FakeDispatcher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_start(
        self,
        *,
        conversation_id: str,
        character_id: str,
        message_id: str,
        audio_enabled: bool,
        selected_style_id: int | None,
    ) -> None:
        self.events.append(
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
        self.events.append({"type": "delta", "message_id": message_id, "delta": delta})

    async def send_text_end(self, *, message_id: str) -> None:
        self.events.append({"type": "text_end", "message_id": message_id})

    async def send_end(self, *, message_id: str, finish_reason: str) -> None:
        self.events.append({"type": "end", "message_id": message_id, "finish_reason": finish_reason})

    async def send_audio(
        self,
        *,
        message_id: str,
        segment_index: int,
        text: str,
        audio_format: str,
        audio_bytes: bytes,
    ) -> None:
        self.events.append(
            {
                "type": "audio",
                "message_id": message_id,
                "segment_index": segment_index,
                "text": text,
                "audio_format": audio_format,
                "audio_bytes": audio_bytes,
            }
        )

    async def send_error(
        self,
        error: str,
        *,
        message_id: str | None = None,
        stage: str | None = None,
        fatal: bool | None = None,
    ) -> None:
        self.events.append(
            {
                "type": "error",
                "error": error,
                "message_id": message_id,
                "stage": stage,
                "fatal": fatal,
            }
        )


class FakeTTSClient:
    audio_format = "wav"

    def has_live_engine(self) -> bool:
        return True

    def synthesize(self, text: str, selected_style_id: int | None) -> bytes:
        del selected_style_id
        return f"audio:{text}".encode("utf-8")


class ApiChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_background_followup_emits_audio_when_tts_available(self) -> None:
        character = get_default_character()
        conversation = api_chat.conversation_store.create_conversation(character.id, greeting=None)
        dispatcher = FakeDispatcher()
        proposal = BackgroundProposal(
            conversation_id=conversation["conversation_id"],
            kind=ProposalKind.FOLLOW_UP_MESSAGE,
            summary="idle follow-up",
            payload={"reason": "idle_timeout", "last_image_analysis_text": None},
        )

        try:
            with (
                patch.object(api_chat, "tts_client", FakeTTSClient()),
                patch.object(api_chat, "generate_idle_followup", AsyncMock(return_value="こんにちは。")),
            ):
                await api_chat.execute_background_followup(dispatcher, proposal)
        finally:
            api_chat.conversation_store.delete_conversation(conversation["conversation_id"])

        self.assertIn(
            {
                "type": "start",
                "conversation_id": conversation["conversation_id"],
                "character_id": character.id,
                "message_id": dispatcher.events[0]["message_id"],
                "audio_enabled": True,
                "selected_style_id": None,
            },
            dispatcher.events,
        )
        self.assertTrue(any(event["type"] == "audio" for event in dispatcher.events))
        self.assertEqual(dispatcher.events[-1]["type"], "end")


if __name__ == "__main__":
    unittest.main()