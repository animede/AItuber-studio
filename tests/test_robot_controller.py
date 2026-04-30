from __future__ import annotations

import asyncio
import sys
import types
import unittest
from unittest.mock import patch

from app.robot_controller import RobotController, robot


class RobotControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_robot_invokes_servo_module_handler(self) -> None:
        calls: list[str] = []
        servo_module = types.SimpleNamespace(move_for_state=calls.append)

        with patch.dict(sys.modules, {"SERVO": servo_module}):
            await robot("streaming_audio")

        self.assertEqual(calls, ["streaming_audio"])

    async def test_notify_state_change_schedules_runner_without_blocking(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        async def runner(state_name: str) -> None:
            calls.append(state_name)
            started.set()
            await release.wait()

        controller = RobotController(runner=runner)
        controller.notify_state_change("streaming_audio")

        self.assertEqual(calls, [])

        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertEqual(calls, ["streaming_audio"])

        release.set()
        await asyncio.wait_for(asyncio.gather(*controller._tasks), timeout=1)


if __name__ == "__main__":
    unittest.main()