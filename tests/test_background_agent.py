from __future__ import annotations

import unittest

from app.background_agent import (
    BackgroundAgentManager,
    BackgroundProposal,
    ConversationObservation,
    IdleFollowupProducer,
    ObservationKind,
    ProposalKind,
    ProposalPriority,
    ProposalProducer,
    ToolWorker,
)


class EchoProposalProducer(ProposalProducer):
    async def on_observation(self, observation, *, tool_worker: ToolWorker) -> list[BackgroundProposal]:
        if observation.kind is not ObservationKind.USER_MESSAGE:
            return []
        if not tool_worker.has_tool("echo"):
            return []
        tool_result = await tool_worker.run_tool("echo", text=observation.payload["text"])
        return [
            BackgroundProposal(
                conversation_id=observation.conversation_id,
                kind=ProposalKind.FOLLOW_UP_MESSAGE,
                summary="echo follow-up",
                payload=tool_result,
                priority=ProposalPriority.LOW,
            )
        ]


class BackgroundAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_creates_and_queues_proposal(self) -> None:
        tool_worker = ToolWorker()
        tool_worker.register_tool("echo", lambda text: {"text": text})
        manager = BackgroundAgentManager(
            proposal_producers=[EchoProposalProducer()],
            tool_worker=tool_worker,
        )

        proposals = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.USER_MESSAGE,
                payload={"text": "hello"},
            )
        )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].payload["text"], "hello")

        drained = await manager.drain_proposals("conv_test")
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0].summary, "echo follow-up")

    async def test_ensure_session_returns_same_session(self) -> None:
        manager = BackgroundAgentManager()

        first = manager.ensure_session("conv_test")
        second = manager.ensure_session("conv_test")

        self.assertIs(first, second)

    async def test_close_session_discards_pending_proposals(self) -> None:
        tool_worker = ToolWorker()
        tool_worker.register_tool("echo", lambda text: {"text": text})
        manager = BackgroundAgentManager(
            proposal_producers=[EchoProposalProducer()],
            tool_worker=tool_worker,
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.USER_MESSAGE,
                payload={"text": "bye"},
            )
        )
        manager.close_session("conv_test")

        drained = await manager.drain_proposals("conv_test")
        self.assertEqual(drained, [])

    async def test_idle_followup_producer_uses_last_image_analysis(self) -> None:
        manager = BackgroundAgentManager(
            proposal_producers=[IdleFollowupProducer(idle_seconds=5, cooldown_seconds=10)],
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.USER_MESSAGE,
                payload={"text": "what do you see?"},
                created_at="2026-05-03T10:00:00+00:00",
            )
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.VLM_EVENT,
                payload={"text": "a red mug on a desk"},
                created_at="2026-05-03T10:00:03+00:00",
            )
        )

        proposals = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:06+00:00",
            )
        )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].kind, ProposalKind.FOLLOW_UP_MESSAGE)
        self.assertEqual(proposals[0].payload["last_image_analysis_text"], "a red mug on a desk")

    async def test_idle_followup_producer_respects_cooldown(self) -> None:
        manager = BackgroundAgentManager(
            proposal_producers=[IdleFollowupProducer(idle_seconds=5, cooldown_seconds=10)],
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.USER_MESSAGE,
                payload={"text": "hello"},
                created_at="2026-05-03T10:00:00+00:00",
            )
        )

        first = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:06+00:00",
            )
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.ASSISTANT_MESSAGE,
                payload={"text": "follow-up", "source": "background_agent"},
                created_at="2026-05-03T10:00:07+00:00",
            )
        )

        second = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:08+00:00",
            )
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    async def test_idle_followup_cooldown_starts_after_background_message(self) -> None:
        manager = BackgroundAgentManager(
            proposal_producers=[IdleFollowupProducer(idle_seconds=5, cooldown_seconds=10)],
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.USER_MESSAGE,
                payload={"text": "hello"},
                created_at="2026-05-03T10:00:00+00:00",
            )
        )

        first = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:06+00:00",
            )
        )
        second = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:07+00:00",
            )
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.ASSISTANT_MESSAGE,
                payload={"text": "follow-up", "source": "background_agent"},
                created_at="2026-05-03T10:00:08+00:00",
            )
        )

        third = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:12+00:00",
            )
        )

        self.assertEqual(third, [])

    async def test_idle_followup_ignores_assistant_and_image_events_for_idle_anchor(self) -> None:
        manager = BackgroundAgentManager(
            proposal_producers=[IdleFollowupProducer(idle_seconds=5, cooldown_seconds=10)],
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.USER_MESSAGE,
                payload={"text": "hello"},
                created_at="2026-05-03T10:00:00+00:00",
            )
        )
        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.ASSISTANT_MESSAGE,
                payload={"text": "hi there"},
                created_at="2026-05-03T10:00:03+00:00",
            )
        )
        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.VLM_EVENT,
                payload={"text": "a red mug on a desk"},
                created_at="2026-05-03T10:00:04+00:00",
            )
        )

        proposals = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:06+00:00",
            )
        )

        self.assertEqual(len(proposals), 1)

    async def test_idle_followup_requires_user_activity(self) -> None:
        manager = BackgroundAgentManager(
            proposal_producers=[IdleFollowupProducer(idle_seconds=5, cooldown_seconds=10)],
        )

        await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.ASSISTANT_MESSAGE,
                payload={"text": "initial greeting"},
                created_at="2026-05-03T10:00:00+00:00",
            )
        )

        proposals = await manager.observe(
            ConversationObservation(
                conversation_id="conv_test",
                kind=ObservationKind.TIMER,
                payload={},
                created_at="2026-05-03T10:00:06+00:00",
            )
        )

        self.assertEqual(proposals, [])


if __name__ == "__main__":
    unittest.main()