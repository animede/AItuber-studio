"""AITuber Studio を、LLM の起動からブラウザ表示までまとめて立ち上げるランチャー。

    .venv\\Scripts\\python.exe scripts\\launch.py             起動する
    .venv\\Scripts\\python.exe scripts\\launch.py --shortcut  デスクトップにアイコンを作る (Windows)

webapp_main.py を直接動かす従来の起動方法はそのまま使える。このランチャーは、
その前後の手間をまとめて引き受けるだけ。

- 会話 LLM (llama-server) を LLM_BASE_URL のポート (既定 8080) で起動する。
  すでに起動しているものがあれば、それを使う (voice_transcribe などと共用できる)
- AITuber Studio 本体 (webapp_main.py) を起動し、待受を確認してからブラウザで開く
- ランチャーが終了したら、自分が起動したものだけを止める

Windows では Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) に自分を入れておく。
窓の × や taskkill /F のように後片付けの処理が走らない終わり方をしても、OS が子プロセスを
まとめて終了させるので、4GB 超のモデルを抱えた llama-server が孤児として残らない。

AivisSpeech (TTS) は別に起動する運用なので、ここでは起動しない。

設定は環境変数で変える。アイコンから起動すると環境変数を渡しにくいので、
リポジトリ直下の launch.env (git 管理外) に KEY=VALUE の形で書いてもよい。

    LLM_MODEL         llama-server に読ませるモデル (Hugging Face の repo:quant)。
                      アプリ側の既定 (app/settings.py) と同じものを使う。Hugging Face の
                      キャッシュに mmproj と一緒にあればそれを読み、無ければ -hf で取得する
    LLM_CTX           コンテキスト長。既定 16384。付けないとモデル最大の 131072 になり、
                      KV キャッシュだけで 2GB 以上使う
    LLAMA_SERVER_EXE  llama-server の場所。既定は PATH 上のもの、無ければ ~/llama.cpp
    LLM_MODEL_PATH, LLM_MMPROJ_PATH  gguf を直接指定するとき
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOCAL_ENV_FILE = BASE_DIR / "launch.env"

SHORTCUT_NAME = "AITuber Studio"
SHORTCUT_DESCRIPTION = "AITuber Studio (アプリ + ローカル LLM) を起動"
SHORTCUT_ICON = Path(__file__).resolve().parent / "aituber.ico"

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def load_local_env() -> None:
    """launch.env に書いた KEY=VALUE を環境変数として読み込む。既に設定済みの値が優先。"""
    if not LOCAL_ENV_FILE.exists():
        return
    for raw in LOCAL_ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


# アプリと同じ設定を見るため、launch.env を読んでから app.settings を読み込む。
load_local_env()
sys.path.insert(0, str(BASE_DIR))
from app import settings  # noqa: E402

APP_URL = f"http://127.0.0.1:{settings.APP_PORT}/"

_llm = urlsplit(settings.LLM_BASE_URL)
LLM_HOST = _llm.hostname or "127.0.0.1"
LLM_PORT = _llm.port or 80
LLM_ROOT = settings.LLM_BASE_URL.rstrip("/").removesuffix("/v1")
LLM_IS_LOCAL = LLM_HOST in LOCAL_HOSTS

_tts = urlsplit(settings.TTS_URL)
TTS_HOST = _tts.hostname or "127.0.0.1"
TTS_PORT = _tts.port or 80

LLM_MODEL = settings.LLM_MODEL
LLM_CTX = os.getenv("LLM_CTX", "16384")

STARTUP_TIMEOUT_SECONDS = float(os.getenv("STARTUP_TIMEOUT_SECONDS", "600"))
DOWNLOAD_TIMEOUT_SECONDS = 6 * 60 * 60

_job_handle = None


def log(message: str = "") -> None:
    print(message, flush=True)


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    if host == "0.0.0.0":
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def setup_kill_on_close_job() -> None:
    """このプロセスが死んだら子プロセスも道連れにする job object を用意する (Windows)。"""

    global _job_handle
    from ctypes import wintypes

    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # argtypes を省くと HANDLE (64bit) を c_int に押し込もうとして OverflowError になる。
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        handle,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    if not kernel32.AssignProcessToJobObject(handle, kernel32.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())

    # handle を閉じると job も閉じて道連れが発動するため、プロセスが生きている間は保持する。
    _job_handle = handle


def llm_ready() -> bool:
    """llama-server がモデルを読み終えたか。読み込み中は /health が 503 を返す。"""
    try:
        with urllib.request.urlopen(f"{LLM_ROOT}/health", timeout=3):
            return True
    except urllib.error.HTTPError as error:
        # /health の無いサーバー (llama.cpp 以外) は、応答があれば使えるとみなす。
        return error.code != 503
    except OSError:
        return False


def wait_for_llm(process: subprocess.Popen | None, timeout: float) -> bool:
    """llama-server がモデルを読み終えるまで待つ。

    新しい llama-server は先にポートを開けて、読み込みが終わるまで 503 を返すので、
    ポートが開いただけではまだ使えない。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if llm_ready():
            return True
        if process is not None and process.poll() is not None:
            log(f"[LLM]   起動に失敗しました (終了コード {process.returncode})。")
            return False
        time.sleep(1.0)
    log(f"[LLM]   {timeout:.0f} 秒以内にモデルの読み込みが終わりませんでした。")
    return False


def llm_accepts_audio() -> bool | None:
    """llama-server の /props で、音声を扱えるか (mmproj を読み込んだか) を調べる。"""
    try:
        with urllib.request.urlopen(f"{LLM_ROOT}/props", timeout=5) as response:
            props = json.loads(response.read().decode("utf-8"))
        return bool(props.get("modalities", {}).get("audio"))
    except Exception:
        return None


def resolve_llama_server() -> Path | None:
    configured = os.getenv("LLAMA_SERVER_EXE", "").strip()
    if configured:
        return Path(configured) if Path(configured).exists() else None
    on_path = shutil.which("llama-server")
    if on_path:
        return Path(on_path)
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    for path in (Path.home() / "llama.cpp" / exe, Path.home() / "llama.cpp" / "build" / "bin" / exe):
        if path.exists():
            return path
    return None


def find_cached_model(spec: str) -> tuple[Path, Path] | None:
    """Hugging Face のキャッシュから、同じ snapshot に入っている (本体, mmproj) を探す。"""
    repo, _, quant = spec.partition(":")
    hub = os.getenv("HF_HUB_CACHE") or str(
        Path(os.getenv("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub"
    )
    snapshots = Path(hub) / f"models--{repo.replace('/', '--')}" / "snapshots"
    found = []
    for snapshot in snapshots.glob("*"):
        models = [
            path
            for path in snapshot.glob(f"*{quant}*.gguf" if quant else "*.gguf")
            if not path.name.lower().startswith("mmproj")
        ]
        mmprojs = sorted(snapshot.glob("mmproj*.gguf"))
        # snapshot をまたいで組み合わせると噛み合わないことがあるので、両方そろうものだけ使う。
        if models and mmprojs:
            found.append((models[0], mmprojs[0]))
    if not found:
        return None
    return max(found, key=lambda pair: pair[0].stat().st_mtime)


def build_llm_command() -> tuple[list[str], bool] | None:
    """llama-server の起動コマンドと、モデルを新たに取得するかどうかを返す。"""
    exe = resolve_llama_server()
    if exe is None:
        log("[LLM]   llama-server が見つかりません。llama.cpp を入れるか、LLAMA_SERVER_EXE で指定してください。")
        return None

    model_path = os.getenv("LLM_MODEL_PATH", "").strip()
    mmproj_path = os.getenv("LLM_MMPROJ_PATH", "").strip()
    if model_path:
        source = ["-m", model_path] + (["--mmproj", mmproj_path] if mmproj_path else [])
        downloading = False
    elif cached := find_cached_model(LLM_MODEL):
        source = ["-m", str(cached[0]), "--mmproj", str(cached[1])]
        downloading = False
    else:
        # -hf ならマイク音声・画像入力に要る mmproj も一緒に取得して読み込む。
        source = ["-hf", LLM_MODEL]
        downloading = True

    command = [
        str(exe),
        *source,
        # アプリが送るモデル名 (LLM_MODEL) と /v1/models の名前をそろえる。
        "-a",
        LLM_MODEL,
        "--reasoning",
        "off",
        "-c",
        LLM_CTX,
        "-np",
        "1",
        "--host",
        LLM_HOST,
        "--port",
        str(LLM_PORT),
    ]
    return command, downloading


def start_llm() -> tuple[subprocess.Popen, bool] | None:
    built = build_llm_command()
    if built is None:
        return None
    command, downloading = built

    log(f"[LLM]   起動します: {LLM_MODEL}")
    if downloading:
        log("[LLM]   キャッシュに無いので Hugging Face から取得します。初回は数 GB のダウンロードになります。")
    log_file = open(LOG_DIR / "llm.log", "w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        command,
        cwd=str(Path(command[0]).parent),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    return process, downloading


def start_app() -> subprocess.Popen:
    log("[APP]   AITuber Studio を起動します。")
    log_file = open(LOG_DIR / "app.log", "w", encoding="utf-8", errors="replace")
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.Popen(
        [sys.executable, str(BASE_DIR / "webapp_main.py")],
        cwd=str(BASE_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def wait_for_port(port: int, label: str, process: subprocess.Popen, timeout: float, host: str = "127.0.0.1") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_open(port, host):
            return True
        if process.poll() is not None:
            log(f"[{label}] 起動に失敗しました (終了コード {process.returncode})。")
            return False
        time.sleep(1.0)
    log(f"[{label}] {timeout:.0f} 秒以内に {port} が開きませんでした。")
    return False


def stop_processes(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def pause_and_fail(message: str) -> int:
    log()
    log(message)
    log("Enter キーで終了します。")
    try:
        input()
    except EOFError:
        pass
    return 1


def create_desktop_shortcut() -> int:
    """デスクトップに、このランチャーを起動するアイコンを作る (Windows のみ)。"""
    if sys.platform != "win32":
        log("デスクトップのアイコン作成は Windows だけに対応しています。")
        return 1

    venv_python = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    target = venv_python if venv_python.exists() else Path(sys.executable)
    # 値は環境変数で渡す。PowerShell の引用符の扱いや、日本語の名前の文字化けを避けられる。
    script = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
        "$desktop = [Environment]::GetFolderPath('Desktop');"
        "$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop $env:SC_NAME));"
        "$lnk.TargetPath = $env:SC_TARGET; $lnk.Arguments = $env:SC_ARGS;"
        "$lnk.WorkingDirectory = $env:SC_CWD; $lnk.IconLocation = $env:SC_ICON;"
        "$lnk.Description = $env:SC_DESC; $lnk.Save(); Write-Output $lnk.FullName"
    )
    env = dict(
        os.environ,
        SC_NAME=f"{SHORTCUT_NAME}.lnk",
        SC_TARGET=str(target),
        SC_ARGS=f'"{Path(__file__).resolve().relative_to(BASE_DIR)}"',
        SC_CWD=str(BASE_DIR),
        SC_ICON=f"{SHORTCUT_ICON},0",
        SC_DESC=SHORTCUT_DESCRIPTION,
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        capture_output=True,
    )
    if result.returncode != 0:
        log("アイコンを作れませんでした。")
        log(result.stderr.decode("utf-8", errors="replace").strip())
        return 1
    log(f"デスクトップにアイコンを作りました: {result.stdout.decode('utf-8', errors='replace').strip()}")
    return 0


def run() -> int:
    if port_is_open(settings.APP_PORT):
        log(f"{settings.APP_PORT} は既に待受中です。ブラウザで {APP_URL} を開きます。")
        webbrowser.open(APP_URL)
        return 0

    LOG_DIR.mkdir(exist_ok=True)
    if sys.platform == "win32":
        setup_kill_on_close_job()

    log("AITuber Studio を起動しています。")
    log()

    if port_is_open(TTS_PORT, TTS_HOST):
        log(f"[TTS]   AivisSpeech ({TTS_PORT}) を検出しました。")
    else:
        log(f"[TTS]   AivisSpeech ({TTS_PORT}) は未起動です。音声なしで続行します。")
        log("[TTS]   音声を使う場合は AivisSpeech を起動してからブラウザを再読み込みしてください。")

    owned: list[subprocess.Popen] = []
    llm_process = None
    try:
        if port_is_open(LLM_PORT, LLM_HOST):
            log(f"[LLM]   {settings.LLM_BASE_URL} の llama-server を使います (起動済み)。")
            if not llm_ready():
                log("[LLM]   モデルの読み込み中です。終わるまで待ちます...")
                wait_for_llm(None, STARTUP_TIMEOUT_SECONDS)
            if llm_accepts_audio() is False:
                log("[LLM]   ただし mmproj なしで起動されているため、マイク入力と画像入力は使えません。")
        elif not LLM_IS_LOCAL:
            log(f"[LLM]   {settings.LLM_BASE_URL} に接続できません。LLM なしで続行します。")
        else:
            started = start_llm()
            if started is None:
                log("[LLM]   LLM なしで続行します。")
            else:
                llm_process, downloading = started
                owned.append(llm_process)
                log("[LLM]   モデル読み込み中です。数十秒かかることがあります...")
                timeout = DOWNLOAD_TIMEOUT_SECONDS if downloading else STARTUP_TIMEOUT_SECONDS
                if not wait_for_llm(llm_process, timeout):
                    return pause_and_fail(f"詳細は {LOG_DIR / 'llm.log'} を確認してください。")

        app_process = start_app()
        owned.append(app_process)
        if not wait_for_port(settings.APP_PORT, "APP", app_process, STARTUP_TIMEOUT_SECONDS):
            return pause_and_fail(f"詳細は {LOG_DIR / 'app.log'} を確認してください。")

        log()
        log(f"  画面: {APP_URL}")
        log(f"  LLM:  {settings.LLM_BASE_URL}" + ("" if llm_process else " (起動済みのものを使用)"))
        log(f"  ログ: {LOG_DIR}")
        log()
        log("ブラウザを開きます。")
        if llm_process:
            log("この窓を閉じると、アプリと LLM の両方が停止します。")
        else:
            log("この窓を閉じると、アプリが停止します (起動済みだった LLM はそのまま残ります)。")
        log()
        webbrowser.open(APP_URL)

        shared_llm_lost = False
        while True:
            if app_process.poll() is not None:
                log("アプリが終了したため、ランチャーも終了します。")
                return 1
            if llm_process is not None and llm_process.poll() is not None:
                log("LLM が終了したため、ランチャーも終了します。")
                return 1
            if llm_process is None and LLM_IS_LOCAL and not shared_llm_lost and not port_is_open(LLM_PORT, LLM_HOST):
                # 共用していた LLM の持ち主 (voice_transcribe など) が閉じられた。
                shared_llm_lost = True
                log("使っていた LLM が停止しました。この窓を閉じて、もう一度起動すると LLM も起動します。")
            time.sleep(2.0)
    except KeyboardInterrupt:
        log()
        log("停止します。")
        return 0
    finally:
        stop_processes(owned)


def main() -> int:
    # コンソールの文字コードで表現できない文字が来ても、表示で落ちないようにする。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="AITuber Studio を LLM ごと起動する")
    parser.add_argument("--shortcut", action="store_true", help="デスクトップに起動用のアイコンを作る (Windows)")
    args = parser.parse_args()
    if args.shortcut:
        return create_desktop_shortcut()
    return run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        # アイコンから起動したときに、エラー表示ごとコンソールが一瞬で消えないようにする。
        import traceback

        traceback.print_exc()
        print()
        print("起動中にエラーが発生しました。Enter キーで終了します。")
        try:
            input()
        except EOFError:
            pass
        sys.exit(1)
