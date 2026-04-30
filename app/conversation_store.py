from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock
from typing import NotRequired, TypedDict
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_conversation_id() -> str:
    return f"conv_{uuid4().hex[:8]}"


def new_message_id() -> str:
    return f"msg_{uuid4().hex[:10]}"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageRecord(TypedDict):
    message_id: str
    role: MessageRole
    content: str
    timestamp: str
    history_summary: NotRequired[str]
    use_summary_for_history: NotRequired[bool]


class ConversationRecord(TypedDict):
    conversation_id: str
    character_id: str
    messages: list[MessageRecord]
    created_at: str
    updated_at: str


class ConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[str, ConversationRecord] = {}
        self._lock = Lock()

    def create_conversation(self, character_id: str, greeting: str | None = None) -> ConversationRecord:
        timestamp = utc_now_iso()
        conversation_id = new_conversation_id()
        messages: list[MessageRecord] = []
        if greeting:
            messages.append(
                {
                    "message_id": new_message_id(),
                    "role": MessageRole.ASSISTANT,
                    "content": greeting,
                    "timestamp": timestamp,
                }
            )

        conversation: ConversationRecord = {
            "conversation_id": conversation_id,
            "character_id": character_id,
            "messages": messages,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._lock:
            self._conversations[conversation_id] = conversation
        return deepcopy(conversation)

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            return deepcopy(conversation) if conversation else None

    def has_conversation(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._conversations

    def delete_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._conversations.pop(conversation_id, None)

    def append_user_message(self, conversation_id: str, content: str) -> MessageRecord:
        return self._append_message(conversation_id, MessageRole.USER, content)

    def append_assistant_message(
        self,
        conversation_id: str,
        content: str,
        *,
        message_id: str | None = None,
    ) -> MessageRecord:
        return self._append_message(conversation_id, MessageRole.ASSISTANT, content, message_id=message_id)

    def _append_message(
        self,
        conversation_id: str,
        role: MessageRole,
        content: str,
        *,
        message_id: str | None = None,
    ) -> MessageRecord:
        timestamp = utc_now_iso()
        message: MessageRecord = {
            "message_id": message_id or new_message_id(),
            "role": role,
            "content": content,
            "timestamp": timestamp,
        }
        with self._lock:
            conversation = self._conversations[conversation_id]
            conversation["messages"].append(message)
            conversation["updated_at"] = timestamp
        return deepcopy(message)

    def recent_messages(self, conversation_id: str, max_messages: int) -> list[MessageRecord]:
        with self._lock:
            messages = self._conversations[conversation_id]["messages"]
            return deepcopy(messages[-max_messages:])

    def recent_messages_for_prompt(self, conversation_id: str, max_messages: int) -> list[MessageRecord]:
        with self._lock:
            messages = deepcopy(self._conversations[conversation_id]["messages"][-max_messages:])

        for message in messages:
            if message["role"] != MessageRole.ASSISTANT:
                continue
            if message.get("use_summary_for_history") and message.get("history_summary"):
                message["content"] = message["history_summary"]
        return messages

    def set_message_history_summary(
        self,
        conversation_id: str,
        message_id: str,
        history_summary: str,
    ) -> MessageRecord | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if not conversation:
                return None

            for message in conversation["messages"]:
                if message["message_id"] != message_id:
                    continue
                message["history_summary"] = history_summary
                message["use_summary_for_history"] = True
                conversation["updated_at"] = utc_now_iso()
                return deepcopy(message)

        return None

    def pop_last_message(
        self,
        conversation_id: str,
        *,
        expected_role: MessageRole | None = None,
        expected_message_id: str | None = None,
    ) -> MessageRecord | None:
        with self._lock:
            conversation = self._conversations.get(conversation_id)
            if not conversation or not conversation["messages"]:
                return None

            last_message = conversation["messages"][-1]
            if expected_role and last_message["role"] != expected_role:
                return None
            if expected_message_id and last_message["message_id"] != expected_message_id:
                return None

            removed_message = conversation["messages"].pop()
            conversation["updated_at"] = utc_now_iso()
            return deepcopy(removed_message)


conversation_store = ConversationStore()
