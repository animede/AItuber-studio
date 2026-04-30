from __future__ import annotations

from dataclasses import dataclass
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .chat_event_dispatcher import ChatEventDispatcher
from .character_registry import get_character, get_default_character
from .robot_controller import RobotController
from .chat_session_runtime import ChatSessionRuntime, ChatTurnRequestContext
from .chat_turn_state_machine import ChatTurnEvent, ChatTurnStateMachine
from .conversation_store import ConversationRecord, conversation_store
from .schemas import ChatStreamRequest, ConversationCreateRequest
from .tts_client import TTSClient


logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])
tts_client = TTSClient()
robot_controller = RobotController()
chat_session_runtime = ChatSessionRuntime(
    conversation_store=conversation_store,
    tts_client=tts_client,
)


@dataclass(frozen=True)
class ValidatedChatRequest:
    payload: ChatStreamRequest
    character_id: str
    message_text: str


async def validate_chat_request(
    dispatcher: ChatEventDispatcher,
    raw_payload: dict,
) -> ValidatedChatRequest | None:
    action = raw_payload.get("action")
    if action != "chat":
        await dispatcher.send_error("未対応のアクションです。")
        return None

    try:
        payload = ChatStreamRequest.model_validate(raw_payload)
    except ValidationError as exc:
        logger.warning("invalid chat payload: %s payload=%s", exc, raw_payload)
        await dispatcher.send_error("メッセージ形式が不正です。")
        return None

    conversation = conversation_store.get_conversation(payload.conversation_id)
    if not conversation:
        await dispatcher.send_error("Conversation not found")
        return None

    message_text = (payload.message or "").strip()
    has_audio_input = bool((payload.input_audio_b64 or "").strip())
    if not message_text and not has_audio_input:
        await dispatcher.send_error("Message or input audio is required")
        return None

    if has_audio_input and not (payload.input_audio_format or "").strip():
        await dispatcher.send_error("Audio input format is required")
        return None

    if not message_text and has_audio_input:
        message_text = "音声入力"

    return ValidatedChatRequest(
        payload=payload,
        character_id=conversation["character_id"],
        message_text=message_text,
    )


def conversation_payload(conversation: ConversationRecord) -> dict:
    character = get_character(conversation["character_id"])
    return {
        "conversation_id": conversation["conversation_id"],
        "character": character.public_dict(),
        "messages": conversation["messages"],
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
    }


def cleared_conversation_payload(conversation: ConversationRecord) -> dict:
    character = get_character(conversation["character_id"])
    return {
        "success": True,
        "new_conversation_id": conversation["conversation_id"],
        "character": character.public_dict(),
        "messages": conversation["messages"],
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
    }


@router.post("/api/conversations")
def create_conversation(payload: ConversationCreateRequest) -> dict:
    # 指定キャラ、または既定キャラで新しい会話 1 本を作る。
    try:
        character = get_default_character() if not payload.character_id else get_character(payload.character_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    conversation = conversation_store.create_conversation(character.id, greeting=character.greeting)
    return conversation_payload(conversation)


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    # 既存会話をそのまま再表示するための取得 API。
    conversation = conversation_store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation_payload(conversation)


@router.post("/api/conversations/{conversation_id}/clear")
def clear_conversation(conversation_id: str) -> dict:
    # 履歴クリアは削除ではなく、同じキャラで新規会話を作り直して返す。
    conversation = conversation_store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation_store.delete_conversation(conversation_id)
    character = get_character(conversation["character_id"])
    new_conversation = conversation_store.create_conversation(character.id, greeting=character.greeting)
    return cleared_conversation_payload(new_conversation)


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket) -> None:
    # 1 本の WebSocket 接続で、複数ターンの chat アクションを順に処理する。
    await websocket.accept()
    dispatcher = ChatEventDispatcher(websocket)

    while True:
        try:
            raw_payload = await websocket.receive_json()
        except WebSocketDisconnect:
            break
        except Exception:
            await dispatcher.send_error("不正なJSONを受信しました。")
            continue

        validated = await validate_chat_request(dispatcher, raw_payload)
        if not validated:
            continue

        try:
            payload = validated.payload
            turn_state_machine = ChatTurnStateMachine(
                conversation_id=payload.conversation_id,
                on_state_changed=robot_controller.notify_state_change,
            )
            turn_state_machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
            turn_state_machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
            await chat_session_runtime.execute_turn(
                ChatTurnRequestContext(
                    websocket=websocket,
                    payload=payload,
                    character_id=validated.character_id,
                    message_text=validated.message_text,
                    turn_state_machine=turn_state_machine,
                )
            )
        except WebSocketDisconnect:
            break
