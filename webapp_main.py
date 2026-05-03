from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"
VERSIONED_STATIC_FILES = (
    STATIC_DIR / "app.js",
    STATIC_DIR / "style.css",
)


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


def _build_asset_version() -> str:
    configured_version = os.getenv("APP_ASSET_VERSION", "").strip()
    if configured_version:
        return configured_version

    latest_mtime_ns = 0
    for file_path in VERSIONED_STATIC_FILES:
        try:
            latest_mtime_ns = max(latest_mtime_ns, file_path.stat().st_mtime_ns)
        except FileNotFoundError:
            continue

    if latest_mtime_ns <= 0:
        return "dev"
    return str(latest_mtime_ns)


def _render_index_html(asset_version: str) -> str:
    html = INDEX_FILE.read_text(encoding="utf-8")
    html = html.replace('/static/style.css', f'/static/style.css?v={asset_version}')
    html = html.replace('/static/app.js', f'/static/app.js?v={asset_version}')
    return html


def create_app() -> FastAPI:
    from app.api_characters import router as character_router
    from app.api_chat import router as chat_router
    from app.api_meta import router as meta_router
    from app.api_youtube import router as youtube_router

    app = FastAPI(title="AI Character Chat")
    asset_version = _build_asset_version()
    rendered_index_html = _render_index_html(asset_version)

    @app.middleware("http")
    async def control_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif request.url.path in {"/static/app.js", "/static/style.css"}:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    app.include_router(meta_router)
    app.include_router(character_router)
    app.include_router(chat_router)
    app.include_router(youtube_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(rendered_index_html)

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
