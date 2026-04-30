from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api_characters import router as character_router
from app.api_chat import router as chat_router
from app.api_meta import router as meta_router
from app.api_youtube import router as youtube_router
from app.settings import APP_HOST, APP_PORT


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="AI Character Chat")
app.include_router(meta_router)
app.include_router(character_router)
app.include_router(chat_router)
app.include_router(youtube_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    uvicorn.run("webapp_main:app", host=APP_HOST, port=APP_PORT, reload=False)
