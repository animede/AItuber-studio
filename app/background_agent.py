from __future__ import annotations

"""Background agent subsystem skeleton.

このモジュールは、主経路の会話応答を止めずに後追いで補助判断を行うための
最小骨格をまとめている。

設計上の意図は次の通り。

- 主経路は user input への即時応答を優先し、background agent はその外側で動く
- background agent 自体は直接発話せず、proposal を runtime 側へ渡すだけにする
- 会話ごとに観測ストリームと proposal キューを分離し、将来の並列処理に備える
- tool 実行は ToolWorker に寄せ、producer 側は「何が必要か」の判断に集中する

現段階では hot path にはまだ接続しておらず、観測、proposal 生成、tool 実行、
会話単位の session 管理を安全に置くための最小 API を提供している。
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum, StrEnum
from typing import Any, Awaitable, Callable
from uuid import uuid4


def _utc_now_iso() -> str:
    # proposal や observation の生成時刻を一貫した UTC ISO 文字列で持つ。
    return datetime.now(timezone.utc).isoformat()


class ObservationKind(StrEnum):
    # background agent が購読するイベント種別。
    # 将来 VLM や timer 駆動の自律行動を足しても、主経路の型を崩さず拡張できる。
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    YOUTUBE_COMMENT = "youtube_comment"
    TIMER = "timer"
    VLM_EVENT = "vlm_event"


class ProposalKind(StrEnum):
    # runtime に提案する内容の大分類。
    # FOLLOW_UP_MESSAGE は追加発話候補、TOOL_REQUEST は外部取得などの補助要求を表す。
    FOLLOW_UP_MESSAGE = "follow_up_message"
    TOOL_REQUEST = "tool_request"


class ProposalPriority(IntEnum):
    # proposal の採否や順序づけに使う優先度。
    # 数値にしておくことで、将来ソートやしきい値判定へそのまま流用できる。
    LOW = 10
    MEDIUM = 50
    HIGH = 90


@dataclass(frozen=True)
class ConversationObservation:
    # background agent が受け取る入力イベント。
    # payload の具体形は event 種別ごとに変わる前提なので、ここでは辞書に留める。
    conversation_id: str
    kind: ObservationKind
    payload: dict[str, Any]
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass(frozen=True)
class BackgroundProposal:
    # background agent が runtime 側へ返す提案単位。
    # summary は人間向けの短い説明、payload は実際の follow-up や tool 情報を持つ。
    conversation_id: str
    kind: ProposalKind
    summary: str
    payload: dict[str, Any]
    priority: ProposalPriority = ProposalPriority.MEDIUM
    expires_at: str | None = None
    proposal_id: str = field(default_factory=lambda: f"proposal_{uuid4().hex[:10]}")
    created_at: str = field(default_factory=_utc_now_iso)


ToolHandler = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]
ObserverCallback = Callable[[ConversationObservation], Awaitable[list[BackgroundProposal]]]


class ToolWorker:
    # 外部 lookup や補助ツール実行の入口。
    # producer はツール名と引数だけを渡し、同期・非同期の違いはここで吸収する。
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register_tool(self, name: str, handler: ToolHandler) -> None:
        # 具体ツール実装は manager 外で差し込む。
        self._handlers[name] = handler

    def has_tool(self, name: str) -> bool:
        return name in self._handlers

    async def run_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        # handler が async / sync のどちらでも呼べるように吸収する。
        handler = self._handlers.get(name)
        if handler is None:
            raise KeyError(f"Unknown tool: {name}")
        result = handler(**kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result


class ProposalProducer(ABC):
    # 観測から proposal を作る判定器の抽象 интерфェース。
    # 実装側は、必要なら ToolWorker を使いながら proposal を返す。
    @abstractmethod
    async def on_observation(
        self,
        observation: ConversationObservation,
        *,
        tool_worker: ToolWorker,
    ) -> list[BackgroundProposal]:
        raise NotImplementedError


class ConversationObserver:
    # 1 会話ぶんの observation を複数 producer へ配送する軽量 pub-sub。
    # 処理順は subscribe 順のままで、まずは単純な直列実行にしている。
    def __init__(self) -> None:
        self._subscribers: list[ObserverCallback] = []

    def subscribe(self, callback: ObserverCallback) -> None:
        # producer ごとの subscriber を会話単位 observer へ追加する。
        self._subscribers.append(callback)

    async def publish(self, observation: ConversationObservation) -> list[BackgroundProposal]:
        # すべての subscriber の proposal を集約して返す。
        proposals: list[BackgroundProposal] = []
        for subscriber in self._subscribers:
            proposals.extend(await subscriber(observation))
        return proposals


@dataclass
class BackgroundAgentSession:
    # 会話単位の background agent 実行状態。
    # observer は入力配送、proposal_queue は runtime が後で取り出す保留 proposal を持つ。
    conversation_id: str
    observer: ConversationObserver
    proposal_queue: asyncio.Queue[BackgroundProposal] = field(default_factory=asyncio.Queue)

    async def observe(self, observation: ConversationObservation) -> list[BackgroundProposal]:
        # 観測を publish し、生成された proposal を会話キューへ積む。
        proposals = await self.observer.publish(observation)
        for proposal in proposals:
            await self.proposal_queue.put(proposal)
        return proposals

    async def drain_proposals(self) -> list[BackgroundProposal]:
        # runtime 側がまとめて proposal を回収するためのユーティリティ。
        proposals: list[BackgroundProposal] = []
        while not self.proposal_queue.empty():
            proposals.append(self.proposal_queue.get_nowait())
        return proposals


class BackgroundAgentManager:
    # 会話単位 session のライフサイクルを管理する入口。
    # 主経路から見ると、この manager に observation を流し、必要時に proposal を回収する。
    def __init__(
        self,
        *,
        proposal_producers: list[ProposalProducer] | None = None,
        tool_worker: ToolWorker | None = None,
    ) -> None:
        self.tool_worker = tool_worker or ToolWorker()
        self._proposal_producers = proposal_producers or []
        self._sessions: dict[str, BackgroundAgentSession] = {}

    def register_producer(self, producer: ProposalProducer) -> None:
        # 既存 session にも即時反映して、会話開始順に依存しないようにする。
        self._proposal_producers.append(producer)
        for session in self._sessions.values():
            session.observer.subscribe(self._build_subscriber(producer))

    def ensure_session(self, conversation_id: str) -> BackgroundAgentSession:
        # 会話ごとの observer / proposal queue を lazily に作成する。
        session = self._sessions.get(conversation_id)
        if session is not None:
            return session

        observer = ConversationObserver()
        session = BackgroundAgentSession(conversation_id=conversation_id, observer=observer)
        for producer in self._proposal_producers:
            observer.subscribe(self._build_subscriber(producer))
        self._sessions[conversation_id] = session
        return session

    async def observe(self, observation: ConversationObservation) -> list[BackgroundProposal]:
        # 主経路からの標準入口。session 作成を隠蔽して observation を流す。
        session = self.ensure_session(observation.conversation_id)
        return await session.observe(observation)

    async def drain_proposals(self, conversation_id: str) -> list[BackgroundProposal]:
        # runtime が空きタイミングで follow-up 候補を回収する想定の API。
        session = self._sessions.get(conversation_id)
        if session is None:
            return []
        return await session.drain_proposals()

    def close_session(self, conversation_id: str) -> None:
        # 会話終了時に session を破棄し、保留 proposal もまとめて捨てる。
        self._sessions.pop(conversation_id, None)

    def _build_subscriber(self, producer: ProposalProducer) -> ObserverCallback:
        # producer が ToolWorker を直接参照できるように束縛した subscriber を作る。
        async def subscriber(observation: ConversationObservation) -> list[BackgroundProposal]:
            return await producer.on_observation(observation, tool_worker=self.tool_worker)

        return subscriber