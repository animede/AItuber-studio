from __future__ import annotations

import unittest
from unittest.mock import patch

from app.chat_turn_state_machine import (
    ChatTurnEvent,
    ChatTurnState,
    ChatTurnStateMachine,
    InvalidChatTurnTransition,
)


class ChatTurnStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = ChatTurnStateMachine(conversation_id="conv_test")
        self.print_patcher = patch("builtins.print")
        self.print_patcher.start()

    def tearDown(self) -> None:
        self.print_patcher.stop()

    def test_happy_path_without_audio_completes(self) -> None:
        self.machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
        self.machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
        self.machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        self.machine.apply(ChatTurnEvent.LLM_STREAM_STARTED)
        self.machine.apply(ChatTurnEvent.LLM_FIRST_CHUNK_RECEIVED)
        self.machine.apply(ChatTurnEvent.LLM_STREAM_FINISHED)
        self.machine.apply(ChatTurnEvent.SUMMARY_STARTED)
        self.machine.apply(ChatTurnEvent.AUDIO_PIPELINE_FINISHED)
        self.machine.apply(ChatTurnEvent.SUMMARY_FINISHED)

        self.assertEqual(self.machine.state, ChatTurnState.COMPLETED)

    def test_happy_path_with_audio_completes(self) -> None:
        self.machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
        self.machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
        self.machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        self.machine.apply(ChatTurnEvent.LLM_STREAM_STARTED)
        self.machine.apply(ChatTurnEvent.LLM_FIRST_CHUNK_RECEIVED)
        self.machine.apply(ChatTurnEvent.AUDIO_SEGMENT_ENQUEUED)
        self.machine.apply(ChatTurnEvent.AUDIO_SEGMENT_ENQUEUED)
        self.machine.apply(ChatTurnEvent.LLM_STREAM_FINISHED)
        self.machine.apply(ChatTurnEvent.SUMMARY_STARTED)
        self.machine.apply(ChatTurnEvent.AUDIO_PIPELINE_FINISHED)
        self.machine.apply(ChatTurnEvent.SUMMARY_FINISHED)

        self.assertEqual(self.machine.state, ChatTurnState.COMPLETED)

    def test_audio_pipeline_finished_before_llm_finish_without_audio_segments(self) -> None:
        self.machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
        self.machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
        self.machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        self.machine.apply(ChatTurnEvent.LLM_STREAM_STARTED)
        self.machine.apply(ChatTurnEvent.AUDIO_PIPELINE_FINISHED)

        self.assertEqual(self.machine.state, ChatTurnState.STREAMING_TEXT)

        self.machine.apply(ChatTurnEvent.LLM_STREAM_FINISHED)
        self.machine.apply(ChatTurnEvent.SUMMARY_STARTED)
        self.machine.apply(ChatTurnEvent.SUMMARY_FINISHED)

        self.assertEqual(self.machine.state, ChatTurnState.COMPLETED)

    def test_audio_pipeline_finished_before_llm_finish_with_audio_segments(self) -> None:
        self.machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
        self.machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
        self.machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        self.machine.apply(ChatTurnEvent.AUDIO_SEGMENT_ENQUEUED)
        self.machine.apply(ChatTurnEvent.AUDIO_PIPELINE_FINISHED)

        self.assertEqual(self.machine.state, ChatTurnState.SUMMARIZING)

        self.machine.apply(ChatTurnEvent.SUMMARY_STARTED)
        self.machine.apply(ChatTurnEvent.SUMMARY_FINISHED)

        self.assertEqual(self.machine.state, ChatTurnState.COMPLETED)

    def test_invalid_transition_raises(self) -> None:
        with self.assertRaises(InvalidChatTurnTransition):
            self.machine.apply(ChatTurnEvent.SUMMARY_FINISHED)

    def test_turn_failed_from_streaming_audio_moves_to_failed(self) -> None:
        self.machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
        self.machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
        self.machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        self.machine.apply(ChatTurnEvent.AUDIO_SEGMENT_ENQUEUED)
        self.machine.apply(ChatTurnEvent.TURN_FAILED)

        self.assertEqual(self.machine.state, ChatTurnState.FAILED)

    def test_client_disconnected_from_summarizing_moves_to_cancelled(self) -> None:
        self.machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
        self.machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
        self.machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        self.machine.apply(ChatTurnEvent.LLM_STREAM_FINISHED)
        self.machine.apply(ChatTurnEvent.CLIENT_DISCONNECTED)

        self.assertEqual(self.machine.state, ChatTurnState.CANCELLED)

    def test_bind_message_id_updates_runtime_identity(self) -> None:
        self.machine.bind_message_id("msg_test")

        self.assertEqual(self.machine.message_id, "msg_test")

    def test_state_change_listener_receives_only_real_state_changes(self) -> None:
        changes: list[str] = []
        machine = ChatTurnStateMachine(
            conversation_id="conv_test",
            on_state_changed=changes.append,
        )

        machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
        machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
        machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        machine.apply(ChatTurnEvent.LLM_STREAM_STARTED)

        self.assertEqual(
            changes,
            [
                ChatTurnState.VALIDATING_REQUEST.value,
                ChatTurnState.PREPARING_PROMPT.value,
                ChatTurnState.STREAMING_TEXT.value,
            ],
        )


if __name__ == "__main__":
    unittest.main()