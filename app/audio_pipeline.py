from __future__ import annotations

import asyncio

from .chat_event_dispatcher import ChatEventDispatcher
from .stream_segmenter import SentenceSegmenter
from .tts_client import TTSClient


class AudioPipeline:
    def __init__(
        self,
        *,
        dispatcher: ChatEventDispatcher,
        assistant_message_id: str,
        selected_style_id: int | None,
        audio_enabled: bool,
        split_on_soft_boundaries: bool,
        tts_client: TTSClient,
    ) -> None:
        self.dispatcher = dispatcher
        self.assistant_message_id = assistant_message_id
        self.selected_style_id = selected_style_id
        self.audio_enabled = audio_enabled
        self.tts_client = tts_client
        self.segmenter = SentenceSegmenter(split_on_soft_boundaries=split_on_soft_boundaries)
        self.segment_index = 0
        self.queue: asyncio.Queue[tuple[int, str] | None] | None = None
        self.worker_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self.audio_enabled:
            return
        self.queue = asyncio.Queue(maxsize=16)
        self.worker_task = asyncio.create_task(self._stream_worker())

    async def push_text_chunk(self, chunk: str) -> None:
        if not self.audio_enabled or self.queue is None:
            return
        for segment in self.segmenter.push(chunk):
            await self.queue.put((self.segment_index, segment))
            self.segment_index += 1

    async def finish(self) -> None:
        if not self.audio_enabled or self.queue is None:
            return
        for segment in self.segmenter.flush():
            await self.queue.put((self.segment_index, segment))
            self.segment_index += 1
        await self.queue.put(None)
        if self.worker_task is not None:
            await self.worker_task

    def cancel(self) -> None:
        if self.worker_task is not None:
            self.worker_task.cancel()

    async def _stream_worker(self) -> None:
        if self.queue is None:
            return

        while True:
            item = await self.queue.get()
            if item is None:
                return

            segment_index, segment = item
            await self._send_audio_segment(segment_index=segment_index, segment=segment)

    async def _send_audio_segment(self, *, segment_index: int, segment: str) -> None:
        try:
            audio_bytes = await asyncio.to_thread(self.tts_client.synthesize, segment, self.selected_style_id)
        except Exception as exc:
            await self.dispatcher.send_error(
                str(exc),
                message_id=self.assistant_message_id,
                stage="tts",
                fatal=False,
            )
            return

        if not audio_bytes:
            return

        await self.dispatcher.send_audio(
            message_id=self.assistant_message_id,
            segment_index=segment_index,
            text=segment,
            audio_format=self.tts_client.audio_format,
            audio_bytes=audio_bytes,
        )