from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from .audio_pipeline import AudioPipeline
from .chat_event_dispatcher import ChatEventDispatcher, log_history_summary, log_llm_first_chunk_timing
from .character_registry import CharacterDefinition, get_character
from .chat_turn_state_machine import ChatTurnEvent, ChatTurnStateMachine
from .conversation_store import ConversationStore, MessageRecord, MessageRole, new_message_id
from .llm_client import (
    analyze_image_snapshot_fast,
    build_audio_input_content,
    build_character_fast_image_analysis_messages,
    build_character_image_analysis_messages,
    build_messages,
    stream_chat_chunks,
    summarize_assistant_response,
)
from .schemas import ChatStreamRequest, ImageAnalysisStreamRequest
from .settings import ASSISTANT_SUMMARY_MAX_CHARS, ASSISTANT_SUMMARY_THRESHOLD_CHARS, MAX_HISTORY_PAIRS
from .tts_client import TTSClient


ChunkCallback = Callable[[str], Awaitable[None]]
FirstChunkCallback = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ChatTurnRequestContext:
    websocket: WebSocket
    payload: ChatStreamRequest
    character_id: str
    message_text: str
    turn_state_machine: ChatTurnStateMachine


@dataclass(frozen=True)
class ImageTurnRequestContext:
    websocket: WebSocket
    payload: ImageAnalysisStreamRequest
    character_id: str
    turn_state_machine: ChatTurnStateMachine


@dataclass
class ChatTurnExecutionContext:
    dispatcher: ChatEventDispatcher
    character: CharacterDefinition
    user_message: MessageRecord | None
    assistant_message_id: str
    system_prompt: str
    max_history_pairs: int
    audio_enabled: bool
    summary_threshold_chars: int
    summary_max_chars: int
    selected_style_id: int | None
    audio_pipeline: AudioPipeline
    full_response: str = ""
    summary_task: asyncio.Task | None = None


class ChatSessionRuntime:
    def __init__(
        self,
        *,
        conversation_store: ConversationStore,
        tts_client: TTSClient,
    ) -> None:
        self.conversation_store = conversation_store
        self.tts_client = tts_client

    async def execute_turn(self, request_context: ChatTurnRequestContext) -> None:
        execution_context = self._build_execution_context(request_context)

        try:
            llm_messages = await self._start_turn(request_context, execution_context)
            execution_context.full_response = await self._run_turn_stream(
                request_context,
                execution_context,
                llm_messages,
            )
            await self._finish_turn(request_context, execution_context)
        except WebSocketDisconnect:
            self._abort_turn(
                request_context=request_context,
                execution_context=execution_context,
                event=ChatTurnEvent.CLIENT_DISCONNECTED,
            )
            raise
        except Exception as exc:
            self._abort_turn(
                request_context=request_context,
                execution_context=execution_context,
                event=ChatTurnEvent.TURN_FAILED,
            )
            await execution_context.dispatcher.send_error(
                str(exc),
                message_id=execution_context.assistant_message_id,
                stage="llm",
                fatal=True,
            )

    async def execute_image_turn(self, request_context: ImageTurnRequestContext) -> None:
        execution_context = self._build_image_execution_context(request_context)

        try:
            llm_messages = await self._build_image_turn_messages(request_context)
            await self._start_generic_turn(
                conversation_id=request_context.payload.conversation_id,
                character_id=execution_context.character.id,
                message_id=execution_context.assistant_message_id,
                audio_enabled=execution_context.audio_enabled,
                selected_style_id=execution_context.selected_style_id,
                turn_state_machine=request_context.turn_state_machine,
                dispatcher=execution_context.dispatcher,
                audio_pipeline=execution_context.audio_pipeline,
            )
            execution_context.full_response = await self._run_generic_turn_stream(
                conversation_id=request_context.payload.conversation_id,
                message_id=execution_context.assistant_message_id,
                turn_state_machine=request_context.turn_state_machine,
                dispatcher=execution_context.dispatcher,
                audio_pipeline=execution_context.audio_pipeline,
                audio_enabled=execution_context.audio_enabled,
                llm_messages=llm_messages,
            )
            await self._finish_generic_turn(
                conversation_id=request_context.payload.conversation_id,
                turn_state_machine=request_context.turn_state_machine,
                execution_context=execution_context,
            )
        except WebSocketDisconnect:
            self._abort_turn(
                request_context=request_context,
                execution_context=execution_context,
                event=ChatTurnEvent.CLIENT_DISCONNECTED,
            )
            raise
        except Exception as exc:
            self._abort_turn(
                request_context=request_context,
                execution_context=execution_context,
                event=ChatTurnEvent.TURN_FAILED,
            )
            await execution_context.dispatcher.send_error(
                str(exc),
                message_id=execution_context.assistant_message_id,
                stage="llm",
                fatal=True,
            )

    async def _start_turn(
        self,
        request_context: ChatTurnRequestContext,
        execution_context: ChatTurnExecutionContext,
    ) -> list[dict]:
        llm_messages = self._build_turn_messages(
            request_context=request_context,
            conversation_id=request_context.payload.conversation_id,
            system_prompt=execution_context.system_prompt,
            max_history_pairs=execution_context.max_history_pairs,
        )
        await self._start_generic_turn(
            conversation_id=request_context.payload.conversation_id,
            character_id=execution_context.character.id,
            message_id=execution_context.assistant_message_id,
            audio_enabled=execution_context.audio_enabled,
            selected_style_id=execution_context.selected_style_id,
            turn_state_machine=request_context.turn_state_machine,
            dispatcher=execution_context.dispatcher,
            audio_pipeline=execution_context.audio_pipeline,
        )
        return llm_messages

    async def _run_turn_stream(
        self,
        request_context: ChatTurnRequestContext,
        execution_context: ChatTurnExecutionContext,
        llm_messages: list[dict],
    ) -> str:
        return await self._run_generic_turn_stream(
            conversation_id=request_context.payload.conversation_id,
            message_id=execution_context.assistant_message_id,
            turn_state_machine=request_context.turn_state_machine,
            dispatcher=execution_context.dispatcher,
            audio_pipeline=execution_context.audio_pipeline,
            audio_enabled=execution_context.audio_enabled,
            llm_messages=llm_messages,
        )

    async def _finish_turn(
        self,
        request_context: ChatTurnRequestContext,
        execution_context: ChatTurnExecutionContext,
    ) -> None:
        await self._finish_generic_turn(
            conversation_id=request_context.payload.conversation_id,
            turn_state_machine=request_context.turn_state_machine,
            execution_context=execution_context,
        )

    async def _start_generic_turn(
        self,
        *,
        conversation_id: str,
        character_id: str,
        message_id: str,
        audio_enabled: bool,
        selected_style_id: int | None,
        turn_state_machine: ChatTurnStateMachine,
        dispatcher: ChatEventDispatcher,
        audio_pipeline: AudioPipeline,
    ) -> None:
        turn_state_machine.apply(ChatTurnEvent.PROMPT_PREPARED)
        audio_pipeline.start()
        await dispatcher.send_start(
            conversation_id=conversation_id,
            character_id=character_id,
            message_id=message_id,
            audio_enabled=audio_enabled,
            selected_style_id=selected_style_id,
        )
        turn_state_machine.apply(ChatTurnEvent.LLM_STREAM_STARTED)

    async def _run_generic_turn_stream(
        self,
        *,
        conversation_id: str,
        message_id: str,
        turn_state_machine: ChatTurnStateMachine,
        dispatcher: ChatEventDispatcher,
        audio_pipeline: AudioPipeline,
        audio_enabled: bool,
        llm_messages: list[dict],
    ) -> str:
        async def handle_first_chunk() -> None:
            turn_state_machine.apply(ChatTurnEvent.LLM_FIRST_CHUNK_RECEIVED)

        async def handle_stream_chunk(chunk: str) -> None:
            await dispatcher.send_delta(message_id=message_id, delta=chunk)
            if audio_enabled:
                turn_state_machine.apply(ChatTurnEvent.AUDIO_SEGMENT_ENQUEUED)
            await audio_pipeline.push_text_chunk(chunk)

        return await self._stream_response(
            conversation_id=conversation_id,
            message_id=message_id,
            messages=llm_messages,
            on_chunk=handle_stream_chunk,
            on_first_chunk=handle_first_chunk,
        )

    async def _finish_generic_turn(
        self,
        *,
        conversation_id: str,
        turn_state_machine: ChatTurnStateMachine,
        execution_context: ChatTurnExecutionContext,
    ) -> None:
        if execution_context.full_response:
            self.conversation_store.append_assistant_message(
                conversation_id,
                execution_context.full_response,
                message_id=execution_context.assistant_message_id,
            )

        turn_state_machine.apply(ChatTurnEvent.LLM_STREAM_FINISHED)
        await execution_context.dispatcher.send_text_end(message_id=execution_context.assistant_message_id)

        execution_context.summary_task = self._create_summary_task(
            conversation_id=conversation_id,
            turn_state_machine=turn_state_machine,
            execution_context=execution_context,
        )

        await execution_context.audio_pipeline.finish()
        turn_state_machine.apply(ChatTurnEvent.AUDIO_PIPELINE_FINISHED)
        if execution_context.summary_task is not None:
            await execution_context.summary_task

        self._complete_summary_state(turn_state_machine, execution_context)
        await execution_context.dispatcher.send_end(
            message_id=execution_context.assistant_message_id,
            finish_reason="stop",
        )

    def _create_summary_task(
        self,
        *,
        conversation_id: str,
        turn_state_machine: ChatTurnStateMachine,
        execution_context: ChatTurnExecutionContext,
    ) -> asyncio.Task | None:
        if not execution_context.full_response:
            return None

        turn_state_machine.apply(ChatTurnEvent.SUMMARY_STARTED)
        return asyncio.create_task(
            self._maybe_summarize_turn(
                conversation_id=conversation_id,
                dispatcher=execution_context.dispatcher,
                assistant_message_id=execution_context.assistant_message_id,
                response_text=execution_context.full_response,
                summary_threshold_chars=execution_context.summary_threshold_chars,
                summary_max_chars=execution_context.summary_max_chars,
            )
        )

    def _complete_summary_state(
        self,
        turn_state_machine: ChatTurnStateMachine,
        execution_context: ChatTurnExecutionContext,
    ) -> None:
        if execution_context.full_response:
            turn_state_machine.apply(ChatTurnEvent.SUMMARY_FINISHED)
            return

        turn_state_machine.apply(ChatTurnEvent.SUMMARY_STARTED)
        turn_state_machine.apply(ChatTurnEvent.SUMMARY_FINISHED)

    def _abort_turn(
        self,
        *,
        request_context: ChatTurnRequestContext | ImageTurnRequestContext,
        execution_context: ChatTurnExecutionContext,
        event: ChatTurnEvent,
    ) -> None:
        execution_context.audio_pipeline.cancel()
        if execution_context.summary_task is not None:
            execution_context.summary_task.cancel()
        if execution_context.user_message is not None:
            self._rollback_user_message(
                request_context.payload.conversation_id,
                execution_context.user_message["message_id"],
            )
        request_context.turn_state_machine.apply(event)

    def _build_execution_context(self, request_context: ChatTurnRequestContext) -> ChatTurnExecutionContext:
        character = get_character(request_context.character_id)
        dispatcher = ChatEventDispatcher(request_context.websocket)
        user_message = self.conversation_store.append_user_message(
            request_context.payload.conversation_id,
            request_context.message_text,
        )
        assistant_message_id = new_message_id()
        request_context.turn_state_machine.bind_message_id(assistant_message_id)
        system_prompt = request_context.payload.role.strip() if request_context.payload.role else character.system_prompt
        max_history_pairs = (
            request_context.payload.max_history
            if request_context.payload.max_history is not None
            else MAX_HISTORY_PAIRS
        )
        audio_enabled = self._resolve_audio_enabled(request_context.payload)
        summary_threshold_chars, summary_max_chars = self._resolve_summary_settings(request_context.payload)
        selected_style_id = request_context.payload.selected_style_id
        audio_pipeline = AudioPipeline(
            dispatcher=dispatcher,
            assistant_message_id=assistant_message_id,
            selected_style_id=selected_style_id,
            audio_enabled=audio_enabled,
            split_on_soft_boundaries=request_context.payload.tts_split_on_soft_boundaries,
            tts_client=self.tts_client,
        )

        return ChatTurnExecutionContext(
            dispatcher=dispatcher,
            character=character,
            user_message=user_message,
            assistant_message_id=assistant_message_id,
            system_prompt=system_prompt,
            max_history_pairs=max_history_pairs,
            audio_enabled=audio_enabled,
            summary_threshold_chars=summary_threshold_chars,
            summary_max_chars=summary_max_chars,
            selected_style_id=selected_style_id,
            audio_pipeline=audio_pipeline,
        )

    def _build_image_execution_context(self, request_context: ImageTurnRequestContext) -> ChatTurnExecutionContext:
        character = get_character(request_context.character_id)
        dispatcher = ChatEventDispatcher(request_context.websocket)
        assistant_message_id = new_message_id()
        request_context.turn_state_machine.bind_message_id(assistant_message_id)
        system_prompt = request_context.payload.role.strip() if request_context.payload.role else character.system_prompt
        summary_threshold_chars, summary_max_chars = self._resolve_summary_settings(request_context.payload)
        selected_style_id = request_context.payload.selected_style_id
        audio_enabled = self._resolve_audio_enabled(request_context.payload)
        audio_pipeline = AudioPipeline(
            dispatcher=dispatcher,
            assistant_message_id=assistant_message_id,
            selected_style_id=selected_style_id,
            audio_enabled=audio_enabled,
            split_on_soft_boundaries=request_context.payload.tts_split_on_soft_boundaries,
            tts_client=self.tts_client,
        )

        return ChatTurnExecutionContext(
            dispatcher=dispatcher,
            character=character,
            user_message=None,
            assistant_message_id=assistant_message_id,
            system_prompt=system_prompt,
            max_history_pairs=0,
            audio_enabled=audio_enabled,
            summary_threshold_chars=summary_threshold_chars,
            summary_max_chars=summary_max_chars,
            selected_style_id=selected_style_id,
            audio_pipeline=audio_pipeline,
        )

    def _build_turn_messages(
        self,
        *,
        request_context: ChatTurnRequestContext,
        conversation_id: str,
        system_prompt: str,
        max_history_pairs: int,
    ) -> list[dict]:
        recent_messages = self.conversation_store.recent_messages_for_prompt(
            conversation_id,
            max_history_pairs * 2,
        )
        latest_user_content_override = None
        input_audio_b64 = (request_context.payload.input_audio_b64 or "").strip()
        if input_audio_b64:
            latest_user_content_override = build_audio_input_content(
                audio_b64=input_audio_b64,
                audio_format=(request_context.payload.input_audio_format or "wav").strip(),
                prompt_text=request_context.message_text if request_context.message_text != "音声入力" else None,
            )

        return build_messages(
            system_prompt,
            recent_messages,
            latest_user_content_override=latest_user_content_override,
        )

    async def _build_image_turn_messages(self, request_context: ImageTurnRequestContext) -> list[dict[str, object]]:
        if request_context.payload.fast_image_analysis:
            scene_description_en = await analyze_image_snapshot_fast(
                image_b64=request_context.payload.image_b64,
            )
            return build_character_fast_image_analysis_messages(
                system_prompt=get_character(request_context.character_id).system_prompt,
                scene_description_en=scene_description_en,
            )

        return build_character_image_analysis_messages(
            image_b64=request_context.payload.image_b64,
            image_format=(request_context.payload.image_format or "jpeg").strip(),
            role_text=request_context.payload.role,
        )

    async def _stream_response(
        self,
        *,
        conversation_id: str,
        message_id: str,
        messages: list[dict],
        on_chunk: ChunkCallback,
        on_first_chunk: FirstChunkCallback | None = None,
    ) -> str:
        full_response = ""
        request_started_at = time.perf_counter()
        first_chunk_seen = False

        async for chunk in stream_chat_chunks(messages):
            if not first_chunk_seen:
                first_chunk_seen = True
                log_llm_first_chunk_timing(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    elapsed_ms=(time.perf_counter() - request_started_at) * 1000,
                )
                if on_first_chunk is not None:
                    await on_first_chunk()

            full_response += chunk
            await on_chunk(chunk)

        return full_response

    async def _maybe_summarize_turn(
        self,
        *,
        conversation_id: str,
        dispatcher: ChatEventDispatcher,
        assistant_message_id: str,
        response_text: str,
        summary_threshold_chars: int,
        summary_max_chars: int,
    ) -> None:
        if summary_threshold_chars <= 0 or len(response_text) < summary_threshold_chars:
            return

        try:
            summary = await summarize_assistant_response(response_text, max_chars=summary_max_chars)
        except Exception as exc:
            await dispatcher.send_error(
                str(exc),
                message_id=assistant_message_id,
                stage="summary",
                fatal=False,
            )
            return

        if not summary:
            return

        self.conversation_store.set_message_history_summary(
            conversation_id,
            assistant_message_id,
            summary,
        )
        log_history_summary(
            conversation_id=conversation_id,
            message_id=assistant_message_id,
            threshold_chars=summary_threshold_chars,
            max_chars=summary_max_chars,
            summary=summary,
        )

    def _resolve_audio_enabled(self, payload: ChatStreamRequest) -> bool:
        return payload.audio_enabled and self.tts_client.has_live_engine()

    def _resolve_summary_settings(self, payload: ChatStreamRequest) -> tuple[int, int]:
        threshold_chars = payload.summary_threshold_chars
        max_chars = payload.summary_max_chars
        resolved_threshold = (
            threshold_chars if threshold_chars is not None else ASSISTANT_SUMMARY_THRESHOLD_CHARS
        )
        resolved_max = max_chars if max_chars is not None else ASSISTANT_SUMMARY_MAX_CHARS
        return resolved_threshold, resolved_max

    def _rollback_user_message(self, conversation_id: str, user_message_id: str) -> None:
        self.conversation_store.pop_last_message(
            conversation_id,
            expected_role=MessageRole.USER,
            expected_message_id=user_message_id,
        )