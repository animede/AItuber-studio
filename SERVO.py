"""Physical robot servo bridge stub.

This module is imported by app.robot_controller with the default module name
"SERVO". Replace the print-based implementation below with actual servo control
code when the hardware layer is ready.
"""

from __future__ import annotations


def _emit_servo_message(message: str) -> None:
    """サーボ制御とは別に、状態メッセージを常に標準出力へ残す。"""
    print(message, flush=True)


def _apply_talking_motion() -> None:
    """発話中モーションの実サーボ制御をここへ実装する。"""


def _apply_neutral_pose(state_name: str) -> None:
    """待機姿勢へ戻す実サーボ制御をここへ実装する。"""


def _apply_state_passthrough(state_name: str) -> None:
    """個別実装が無い state の実サーボ制御をここへ実装する。"""


def move_for_state(state_name: str) -> None:
    """Handle a chat turn state change.

    State names are defined in app.chat_turn_state_machine.ChatTurnState and,
    in the current implementation, the following values may be passed here:
    - idle
    - validating_request
    - preparing_prompt
    - streaming_text
    - streaming_audio
    - summarizing
    - completed
    - failed
    - cancelled

    If ChatTurnState is updated later, keep this list in sync.

    Keep `_emit_servo_message(...)` calls even after replacing the stub with
    actual servo commands so state transitions remain visible in logs.
    """

    if state_name == "streaming_audio":
        _apply_talking_motion()
        _emit_servo_message("SERVO: start talking motion")
        return

    if state_name in {"idle", "completed", "cancelled", "failed"}:
        _apply_neutral_pose(state_name)
        _emit_servo_message(f"SERVO: move to neutral pose for state={state_name}")
        return

    _apply_state_passthrough(state_name)
    _emit_servo_message(f"SERVO: received state={state_name}")