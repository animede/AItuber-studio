from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .background_agent import (
    BackgroundAgentManager,
    BackgroundProposal,
    ConversationObservation,
    IdleFollowupProducer,
    ObservationKind,
    ProposalKind,
    emit_background_followup_log,
)
from .audio_pipeline import AudioPipeline
from .chat_event_dispatcher import ChatEventDispatcher
from .character_registry import get_character, get_default_character
from .robot_controller import RobotController
from .chat_session_runtime import ChatSessionRuntime, ChatTurnRequestContext, ImageTurnRequestContext
from .chat_turn_state_machine import ChatTurnEvent, ChatTurnStateMachine
from .conversation_store import ConversationRecord, conversation_store, new_message_id
from .llm_client import generate_idle_followup
from .schemas import ChatStreamRequest, ConversationCreateRequest, ImageAnalysisStreamRequest
from .settings import (
    BACKGROUND_IDLE_FOLLOWUP_COOLDOWN_SECONDS,
    BACKGROUND_IDLE_FOLLOWUP_SECONDS,
    BACKGROUND_IDLE_MAX_HISTORY_MESSAGES,
)
from .tts_client import TTSClient


logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])
tts_client = TTSClient()
robot_controller = RobotController()
chat_session_runtime = ChatSessionRuntime(
    conversation_store=conversation_store,
    tts_client=tts_client,
)
background_agent_manager = BackgroundAgentManager(
    proposal_producers=[
        IdleFollowupProducer(
            idle_seconds=BACKGROUND_IDLE_FOLLOWUP_SECONDS,
            cooldown_seconds=BACKGROUND_IDLE_FOLLOWUP_COOLDOWN_SECONDS,
        )
    ]
)


@dataclass(frozen=True)
class ValidatedChatRequest:
    payload: ChatStreamRequest
    character_id: str
    message_text: str


@dataclass(frozen=True)
class ValidatedImageAnalysisRequest:
    payload: ImageAnalysisStreamRequest
    character_id: str


async def validate_chat_request(
    dispatcher: ChatEventDispatcher,
    raw_payload: dict,
) -> ValidatedChatRequest | None:
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


async def validate_image_analysis_request(
    dispatcher: ChatEventDispatcher,
    raw_payload: dict,
) -> ValidatedImageAnalysisRequest | None:
    try:
        payload = ImageAnalysisStreamRequest.model_validate(raw_payload)
    except ValidationError as exc:
        logger.warning("invalid image analysis payload: %s payload=%s", exc, raw_payload)
        await dispatcher.send_error("メッセージ形式が不正です。")
        return None

    conversation = conversation_store.get_conversation(payload.conversation_id)
    if not conversation:
        await dispatcher.send_error("Conversation not found")
        return None

    return ValidatedImageAnalysisRequest(
        payload=payload,
        character_id=conversation["character_id"],
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


async def observe_assistant_output(
    conversation_id: str,
    *,
    kind: ObservationKind = ObservationKind.ASSISTANT_MESSAGE,
) -> None:
    messages = conversation_store.recent_messages(conversation_id, 1)
    if not messages:
        return
    last_message = messages[-1]
    if last_message["role"] != "assistant":
        return
    await background_agent_manager.observe(
        ConversationObservation(
            conversation_id=conversation_id,
            kind=kind,
            payload={"text": last_message["content"]},
        )
    )


async def activate_background_followup_conversation(
    conversation_id: str,
    active_conversation_ids: set[str],
) -> None:
    conversation = conversation_store.get_conversation(conversation_id)
    if not conversation:
        emit_background_followup_log(
            f"[background-followup] activation skipped conversation_id={conversation_id} reason=missing-conversation"
        )
        active_conversation_ids.discard(conversation_id)
        background_agent_manager.close_session(conversation_id)
        return

    active_conversation_ids.add(conversation_id)
    emit_background_followup_log(
        f"[background-followup] activated conversation_id={conversation_id} message_count={len(conversation['messages'])} updated_at={conversation['updated_at']}"
    )
    if not conversation["messages"]:
        return

    # モード有効化直後でも、直近 user 発話から待機時間を再開できるように復元する。
    last_user_message = next(
        (message for message in reversed(conversation["messages"]) if message["role"] == "user"),
        None,
    )
    if last_user_message is None:
        emit_background_followup_log(
            f"[background-followup] activation waiting conversation_id={conversation_id} reason=no-user-message-yet"
        )
        return

    await background_agent_manager.observe(
        ConversationObservation(
            conversation_id=conversation_id,
            kind=ObservationKind.USER_MESSAGE,
            payload={"text": last_user_message["content"]},
            created_at=last_user_message["timestamp"],
        )
    )


async def execute_background_followup(
    dispatcher: ChatEventDispatcher,
    proposal: BackgroundProposal,
) -> None:
    if proposal.kind is not ProposalKind.FOLLOW_UP_MESSAGE:
        return

    conversation = conversation_store.get_conversation(proposal.conversation_id)
    if not conversation:
        background_agent_manager.close_session(proposal.conversation_id)
        return

    character = get_character(conversation["character_id"])
    emit_background_followup_log(
        f"[background-followup] executing proposal conversation_id={proposal.conversation_id} character_id={character.id} reason={proposal.payload.get('reason')}"
    )
    response_text = await generate_idle_followup(
        system_prompt=character.system_prompt,
        messages=conversation_store.recent_messages_for_prompt(
            proposal.conversation_id,
            BACKGROUND_IDLE_MAX_HISTORY_MESSAGES,
        ),
        last_image_analysis_text=proposal.payload.get("last_image_analysis_text"),
    )
    if not response_text:
        emit_background_followup_log(
            f"[background-followup] empty response conversation_id={proposal.conversation_id}"
        )
        return

    emit_background_followup_log(
        f"[background-followup] response generated conversation_id={proposal.conversation_id} text={response_text[:120]!r}"
    )

    message_id = new_message_id()
    audio_enabled = tts_client.has_live_engine()
    # 背景自己発話も通常ターンと同じ audio event 形式でフロントへ流す。
    audio_pipeline = AudioPipeline(
        dispatcher=dispatcher,
        assistant_message_id=message_id,
        selected_style_id=None,
        audio_enabled=audio_enabled,
        split_on_soft_boundaries=False,
        tts_client=tts_client,
    )
    audio_pipeline.start()
    await dispatcher.send_start(
        conversation_id=proposal.conversation_id,
        character_id=character.id,
        message_id=message_id,
        audio_enabled=audio_enabled,
        selected_style_id=None,
    )
    await dispatcher.send_delta(message_id=message_id, delta=response_text)
    await audio_pipeline.push_text_chunk(response_text)
    conversation_store.append_assistant_message(
        proposal.conversation_id,
        response_text,
        message_id=message_id,
    )
    await background_agent_manager.observe(
        ConversationObservation(
            conversation_id=proposal.conversation_id,
            kind=ObservationKind.ASSISTANT_MESSAGE,
            payload={"text": response_text, "source": "background_agent"},
        )
    )
    await dispatcher.send_text_end(message_id=message_id)
    await audio_pipeline.finish()
    await dispatcher.send_end(message_id=message_id, finish_reason="stop")


async def process_background_agent_tick(
    dispatcher: ChatEventDispatcher,
    active_conversation_ids: set[str],
    *,
    background_followup_enabled: bool,
) -> None:
    if not background_followup_enabled:
        return
    for conversation_id in tuple(active_conversation_ids):
        if not conversation_store.has_conversation(conversation_id):
            active_conversation_ids.discard(conversation_id)
            background_agent_manager.close_session(conversation_id)
            continue

        # WebSocket の receive timeout を 1 秒 tick として使い、自己発話候補だけを回収する。
        await background_agent_manager.observe(
            ConversationObservation(
                conversation_id=conversation_id,
                kind=ObservationKind.TIMER,
                payload={},
            )
        )
        proposals = await background_agent_manager.drain_proposals(conversation_id)
        if proposals:
            emit_background_followup_log(
                f"[background-followup] ready conversation_id={conversation_id} proposals={len(proposals)}"
            )
        for proposal in proposals:
            await execute_background_followup(dispatcher, proposal)


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
    active_conversation_ids: set[str] = set()
    background_followup_enabled = False

    while True:
        try:
            raw_payload = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
        except TimeoutError:
            await process_background_agent_tick(
                dispatcher,
                active_conversation_ids,
                background_followup_enabled=background_followup_enabled,
            )
            continue
        except WebSocketDisconnect:
            break
        except Exception:
            await dispatcher.send_error("不正なJSONを受信しました。")
            continue

        action = raw_payload.get("action")

        try:
            if action == "background_followup_mode":
                background_followup_enabled = bool(raw_payload.get("enabled"))
                conversation_id = str(raw_payload.get("conversation_id") or "").strip()
                emit_background_followup_log(
                    f"[background-followup] mode updated enabled={background_followup_enabled} conversation_id={conversation_id or '-'}"
                )
                if background_followup_enabled and conversation_id:
                    await activate_background_followup_conversation(conversation_id, active_conversation_ids)
                if not background_followup_enabled:
                    for conversation_id in tuple(active_conversation_ids):
                        await background_agent_manager.drain_proposals(conversation_id)
                continue

            if action == "chat":
                validated = await validate_chat_request(dispatcher, raw_payload)
                if not validated:
                    continue

                payload = validated.payload
                active_conversation_ids.add(payload.conversation_id)
                await background_agent_manager.observe(
                    ConversationObservation(
                        conversation_id=payload.conversation_id,
                        kind=ObservationKind.USER_MESSAGE,
                        payload={"text": validated.message_text},
                    )
                )
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
                await observe_assistant_output(payload.conversation_id)
                continue

            if action == "image_analysis":
                validated = await validate_image_analysis_request(dispatcher, raw_payload)
                if not validated:
                    continue

                payload = validated.payload
                active_conversation_ids.add(payload.conversation_id)
                turn_state_machine = ChatTurnStateMachine(
                    conversation_id=payload.conversation_id,
                    on_state_changed=robot_controller.notify_state_change,
                )
                turn_state_machine.apply(ChatTurnEvent.USER_MESSAGE_RECEIVED)
                turn_state_machine.apply(ChatTurnEvent.REQUEST_VALIDATED)
                await chat_session_runtime.execute_image_turn(
                    ImageTurnRequestContext(
                        websocket=websocket,
                        payload=payload,
                        character_id=validated.character_id,
                        turn_state_machine=turn_state_machine,
                    )
                )
                await observe_assistant_output(payload.conversation_id)
                await observe_assistant_output(payload.conversation_id, kind=ObservationKind.VLM_EVENT)
                continue

            await dispatcher.send_error("未対応のアクションです。")
            continue

        except WebSocketDisconnect:
            break
