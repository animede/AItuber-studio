from __future__ import annotations

import unittest

from app.llm_client import build_audio_input_content, build_messages


class LLMClientMessageBuildTests(unittest.TestCase):
    def test_build_messages_replaces_latest_user_with_audio_content(self) -> None:
        messages = [
            {"message_id": "msg_1", "role": "assistant", "content": "こんにちは", "timestamp": "2026-01-01T00:00:00Z"},
            {"message_id": "msg_2", "role": "user", "content": "音声入力", "timestamp": "2026-01-01T00:00:01Z"},
        ]
        audio_content = build_audio_input_content(audio_b64="AAA", audio_format="wav")

        payload = build_messages(
            "system prompt",
            messages,
            latest_user_content_override=audio_content,
        )

        self.assertEqual(payload[1]["content"], "こんにちは")
        self.assertEqual(payload[2]["content"], audio_content)


if __name__ == "__main__":
    unittest.main()