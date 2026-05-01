from __future__ import annotations

import unittest

from app.llm_client import (
    build_audio_input_content,
    build_character_image_analysis_messages,
    build_image_analysis_content,
    build_messages,
)


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

    def test_build_image_analysis_content_wraps_data_url(self) -> None:
        content = build_image_analysis_content(
            image_b64="AAA",
            image_format="jpg",
            prompt_text="見えているものを教えて",
        )

        self.assertEqual(content[0], {"type": "text", "text": "見えているものを教えて"})
        self.assertEqual(
            content[1],
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,AAA"},
            },
        )

    def test_build_character_image_analysis_messages_embeds_role_text(self) -> None:
        messages = build_character_image_analysis_messages(
            image_b64="AAA",
            image_format="jpeg",
            role_text="名前: もも\n口調: やさしい",
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("名前: もも", messages[0]["content"])
        self.assertIn("このキャラクタが今見た場面", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")


if __name__ == "__main__":
    unittest.main()