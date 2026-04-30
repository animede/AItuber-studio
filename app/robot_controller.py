from __future__ import annotations

"""robot と SERVO モジュールの接続窓口。

使い方:
1. プロジェクト直下など、Python から import 可能な場所に `SERVO.py` を用意する。
2. そのモジュールに `move_for_state(state_name)`、`apply_state(state_name)`、
    `set_state(state_name)`、`robot(state_name)` のいずれか 1 つを実装する。
3. state machine から渡される state 名がそのまま引数に入るので、
    `streaming_text` や `streaming_audio` などを見てサーボ動作を分岐する。
4. 非同期関数でも同期関数でもよい。非同期なら await され、同期でも
    chat 側は background task として並列実行される。

最小実装例: これは `SERVO.py` の記述例。

    def move_for_state(state_name: str) -> None:
        if state_name == "streaming_audio":
            ...
        elif state_name == "idle":
            ...

別の例: state 名をまず確認したいときの `SERVO.py` 記述例。

    def move_for_state(state_name: str) -> None:
        print(f"SERVO received: {state_name}")

モジュール名を変えたい場合は `robot(..., servo_module_name="your_module")` を使う。
"""

import asyncio
import importlib
import inspect
import json
from collections.abc import Awaitable, Callable
from types import ModuleType


RobotRunner = Callable[[str], Awaitable[None]]


def _log_robot_event(event_type: str, *, state_name: str, detail: str | None = None) -> None:
    """robot 連携まわりの出来事を JSON で記録する。"""
    payload = {
        "type": event_type,
        "state_name": state_name,
    }
    if detail:
        payload["detail"] = detail
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _resolve_servo_handler(servo_module_name: str) -> Callable[[str], object] | None:
    """SERVO モジュールから state 名を受け取る入口関数を探す。"""
    try:
        # 文字列で指定されたモジュール名から動的 import する。
        servo_module = importlib.import_module(servo_module_name)
    except ModuleNotFoundError:
        # まだ SERVO.py が無い段階でも chat 全体は落とさない。
        return None

    # SERVO 側の命名を固定し切らず、よくありそうな入口名を順に吸収する。
    for attribute_name in ("move_for_state", "apply_state", "set_state", "robot"):
        handler = getattr(servo_module, attribute_name, None)
        if callable(handler):
            # 最初に見つかった callable を採用する。
            return handler

    if isinstance(servo_module, ModuleType) and callable(servo_module):
        # 特殊ケースとして、モジュール自体を callable にしている実装も許容する。
        return servo_module

    # import はできたが入口関数が見つからない場合も noop 扱いにする。
    return None


async def robot(state_name: str, *, servo_module_name: str = "SERVO") -> None:
    """state 名を SERVO 側へ渡す薄いアダプタ。"""
    # state 名をそのまま SERVO 側へ橋渡しするだけにして、変換責務を増やさない。
    handler = _resolve_servo_handler(servo_module_name)
    if handler is None:
        # SERVO 未実装でも状態遷移は続行し、ログだけ残す。
        _log_robot_event(
            "robot_state_change_noop",
            state_name=state_name,
            detail=f"servo module '{servo_module_name}' not available",
        )
        return

    # 同期関数ならそのまま実行され、非同期関数なら awaitable が返る。
    result = handler(state_name)
    if inspect.isawaitable(result):
        # async def で作った SERVO 実装もここで待てる。
        await result


class RobotController:
    def __init__(self, *, runner: RobotRunner = robot) -> None:
        # runner を差し替えられるようにして、テストや別実装を注入しやすくする。
        self._runner = runner
        # 起動中 task を保持し、完了時に回収できるようにする。
        self._tasks: set[asyncio.Task[None]] = set()

    def notify_state_change(self, state_name: str) -> None:
        """state 変化を受けて robot 処理を background task で起動する。"""
        try:
            # FastAPI / asyncio の実行ループ上から呼ばれる前提。
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 同期文脈など event loop が無い場所では実行できないので記録だけ残す。
            _log_robot_event(
                "robot_state_change_skipped",
                state_name=state_name,
                detail="no running event loop",
            )
            return

        # chat 本体を待たせないため、robot 呼び出しは毎回 background task で流す。
        task = loop.create_task(self._run_state_change(state_name))
        # 参照を持たないと task が早期回収され得るため集合に入れておく。
        self._tasks.add(task)
        # 終了時に集合から外し、例外もここで回収する。
        task.add_done_callback(self._finalize_task)

    async def _run_state_change(self, state_name: str) -> None:
        """1 回分の robot 呼び出しを実行し、失敗をログ化する。"""
        try:
            await self._runner(state_name)
        except Exception as exc:
            # 失敗しても chat 本体へ伝播させる前に state 名付きで残す。
            _log_robot_event(
                "robot_state_change_failed",
                state_name=state_name,
                detail=str(exc),
            )
            raise

    def _finalize_task(self, task: asyncio.Task[None]) -> None:
        """完了済み task を回収し、未処理例外を握りつぶさず取得する。"""
        self._tasks.discard(task)
        try:
            # result() を呼ぶことで task 内例外を回収する。
            task.result()
        except Exception:
            # 詳細ログは _run_state_change 側で残しているのでここでは黙って回収する。
            return