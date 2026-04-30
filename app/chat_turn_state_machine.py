from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Callable


class ChatTurnState(StrEnum):
    IDLE = "idle"
    VALIDATING_REQUEST = "validating_request"
    PREPARING_PROMPT = "preparing_prompt"
    STREAMING_TEXT = "streaming_text"
    STREAMING_AUDIO = "streaming_audio"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChatTurnEvent(StrEnum):
    USER_MESSAGE_RECEIVED = "user_message_received"
    REQUEST_VALIDATED = "request_validated"
    PROMPT_PREPARED = "prompt_prepared"
    LLM_STREAM_STARTED = "llm_stream_started"
    LLM_FIRST_CHUNK_RECEIVED = "llm_first_chunk_received"
    LLM_STREAM_FINISHED = "llm_stream_finished"
    AUDIO_SEGMENT_ENQUEUED = "audio_segment_enqueued"
    AUDIO_PIPELINE_FINISHED = "audio_pipeline_finished"
    SUMMARY_STARTED = "summary_started"
    SUMMARY_FINISHED = "summary_finished"
    TURN_FAILED = "turn_failed"
    CLIENT_DISCONNECTED = "client_disconnected"


@dataclass(frozen=True)
class ChatTurnTransition:
    from_state: ChatTurnState
    event: ChatTurnEvent
    to_state: ChatTurnState


class InvalidChatTurnTransition(ValueError):
    pass


StateChangeListener = Callable[[str], None]


class ChatTurnStateMachine:
    _TRANSITIONS: dict[tuple[ChatTurnState, ChatTurnEvent], ChatTurnState] = {
        (ChatTurnState.IDLE, ChatTurnEvent.USER_MESSAGE_RECEIVED): ChatTurnState.VALIDATING_REQUEST,
        (ChatTurnState.VALIDATING_REQUEST, ChatTurnEvent.REQUEST_VALIDATED): ChatTurnState.PREPARING_PROMPT,
        (ChatTurnState.PREPARING_PROMPT, ChatTurnEvent.PROMPT_PREPARED): ChatTurnState.STREAMING_TEXT,
        (ChatTurnState.STREAMING_TEXT, ChatTurnEvent.LLM_STREAM_STARTED): ChatTurnState.STREAMING_TEXT,
        (ChatTurnState.STREAMING_TEXT, ChatTurnEvent.LLM_FIRST_CHUNK_RECEIVED): ChatTurnState.STREAMING_TEXT,
        (ChatTurnState.STREAMING_TEXT, ChatTurnEvent.AUDIO_SEGMENT_ENQUEUED): ChatTurnState.STREAMING_AUDIO,
        (ChatTurnState.STREAMING_TEXT, ChatTurnEvent.LLM_STREAM_FINISHED): ChatTurnState.SUMMARIZING,
        (ChatTurnState.STREAMING_TEXT, ChatTurnEvent.AUDIO_PIPELINE_FINISHED): ChatTurnState.STREAMING_TEXT,
        (ChatTurnState.STREAMING_AUDIO, ChatTurnEvent.AUDIO_SEGMENT_ENQUEUED): ChatTurnState.STREAMING_AUDIO,
        (ChatTurnState.STREAMING_AUDIO, ChatTurnEvent.LLM_STREAM_FINISHED): ChatTurnState.SUMMARIZING,
        (ChatTurnState.STREAMING_AUDIO, ChatTurnEvent.AUDIO_PIPELINE_FINISHED): ChatTurnState.SUMMARIZING,
        (ChatTurnState.SUMMARIZING, ChatTurnEvent.SUMMARY_STARTED): ChatTurnState.SUMMARIZING,
        (ChatTurnState.SUMMARIZING, ChatTurnEvent.AUDIO_PIPELINE_FINISHED): ChatTurnState.SUMMARIZING,
        (ChatTurnState.SUMMARIZING, ChatTurnEvent.SUMMARY_FINISHED): ChatTurnState.COMPLETED,
        (ChatTurnState.COMPLETED, ChatTurnEvent.AUDIO_PIPELINE_FINISHED): ChatTurnState.COMPLETED,
        (ChatTurnState.IDLE, ChatTurnEvent.TURN_FAILED): ChatTurnState.FAILED,
        (ChatTurnState.VALIDATING_REQUEST, ChatTurnEvent.TURN_FAILED): ChatTurnState.FAILED,
        (ChatTurnState.PREPARING_PROMPT, ChatTurnEvent.TURN_FAILED): ChatTurnState.FAILED,
        (ChatTurnState.STREAMING_TEXT, ChatTurnEvent.TURN_FAILED): ChatTurnState.FAILED,
        (ChatTurnState.STREAMING_AUDIO, ChatTurnEvent.TURN_FAILED): ChatTurnState.FAILED,
        (ChatTurnState.SUMMARIZING, ChatTurnEvent.TURN_FAILED): ChatTurnState.FAILED,
        (ChatTurnState.IDLE, ChatTurnEvent.CLIENT_DISCONNECTED): ChatTurnState.CANCELLED,
        (ChatTurnState.VALIDATING_REQUEST, ChatTurnEvent.CLIENT_DISCONNECTED): ChatTurnState.CANCELLED,
        (ChatTurnState.PREPARING_PROMPT, ChatTurnEvent.CLIENT_DISCONNECTED): ChatTurnState.CANCELLED,
        (ChatTurnState.STREAMING_TEXT, ChatTurnEvent.CLIENT_DISCONNECTED): ChatTurnState.CANCELLED,
        (ChatTurnState.STREAMING_AUDIO, ChatTurnEvent.CLIENT_DISCONNECTED): ChatTurnState.CANCELLED,
        (ChatTurnState.SUMMARIZING, ChatTurnEvent.CLIENT_DISCONNECTED): ChatTurnState.CANCELLED,
    }

    def __init__(
        self,
        *,
        conversation_id: str,
        message_id: str | None = None,
        on_state_changed: StateChangeListener | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.state = ChatTurnState.IDLE
        self.history: list[ChatTurnTransition] = []
        self._state_change_listeners: list[StateChangeListener] = []
        if on_state_changed is not None:
            self._state_change_listeners.append(on_state_changed)

    def apply(self, event: ChatTurnEvent) -> ChatTurnState:
        next_state = self._TRANSITIONS.get((self.state, event))
        if next_state is None:
            raise InvalidChatTurnTransition(
                f"Invalid transition: state={self.state} event={event} conversation_id={self.conversation_id}"
            )

        transition = ChatTurnTransition(
            from_state=self.state,
            event=event,
            to_state=next_state,
        )
        self.history.append(transition)
        self.state = next_state
        self._log_transition(transition)
        self._notify_state_change(transition)
        return self.state

    def bind_message_id(self, message_id: str) -> None:
        self.message_id = message_id

    def subscribe_state_change(self, listener: StateChangeListener) -> None:
        self._state_change_listeners.append(listener)

    def _log_transition(self, transition: ChatTurnTransition) -> None:
        print(
            json.dumps(
                {
                    "type": "chat_turn_transition",
                    "conversation_id": self.conversation_id,
                    "message_id": self.message_id,
                    "from_state": transition.from_state,
                    "event": transition.event,
                    "to_state": transition.to_state,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def _notify_state_change(self, transition: ChatTurnTransition) -> None:
        if transition.from_state == transition.to_state:
            return

        for listener in self._state_change_listeners:
            listener(transition.to_state.value)