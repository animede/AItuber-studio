from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def _replace_url_host_port(base_url: str, *, host: str | None, port: int | None) -> str:
    parsed = urlsplit(base_url)
    scheme = parsed.scheme or "http"
    current_hostname = parsed.hostname or "127.0.0.1"
    current_port = parsed.port
    if current_port is None:
        current_port = 443 if scheme == "https" else 80

    resolved_host = host or current_hostname
    resolved_port = port if port is not None else current_port
    netloc = f"{resolved_host}:{resolved_port}"
    return urlunsplit(
        SplitResult(
            scheme=scheme,
            netloc=netloc,
            path=parsed.path,
            query=parsed.query,
            fragment=parsed.fragment,
        )
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AI Character Chat web app")
    parser.add_argument("--tts-host", help="Override TTS host")
    parser.add_argument("--tts-port", type=int, help="Override TTS port")
    parser.add_argument("--llm-host", help="Override LLM host")
    parser.add_argument("--llm-port", type=int, help="Override LLM port")
    return parser


def _apply_runtime_overrides(args: argparse.Namespace) -> None:
    default_tts_url = os.getenv("TTS_URL", "http://127.0.0.1:10101")
    default_llm_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")

    if args.tts_host or args.tts_port is not None:
        os.environ["TTS_URL"] = _replace_url_host_port(
            default_tts_url,
            host=args.tts_host,
            port=args.tts_port,
        )

    if args.llm_host or args.llm_port is not None:
        os.environ["LLM_BASE_URL"] = _replace_url_host_port(
            default_llm_url,
            host=args.llm_host,
            port=args.llm_port,
        )


def create_app() -> FastAPI:
    from app.api_characters import router as character_router
    from app.api_chat import router as chat_router
    from app.api_meta import router as meta_router
    from app.api_youtube import router as youtube_router

    app = FastAPI(title="AI Character Chat")
    app.include_router(meta_router)
    app.include_router(character_router)
    app.include_router(chat_router)
    app.include_router(youtube_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _apply_runtime_overrides(args)

    from app.settings import APP_HOST, APP_PORT

    uvicorn.run(create_app(), host=APP_HOST, port=APP_PORT, reload=False)


if __name__ == "__main__":
    main()
else:
    app = create_app()
