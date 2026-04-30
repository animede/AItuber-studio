from __future__ import annotations

import unittest

from app.background_agent import (
    BackgroundAgentManager,
    BackgroundProposal,
    ConversationObservation,
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


if __name__ == "__main__":
    unittest.main()