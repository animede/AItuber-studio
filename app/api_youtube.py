from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .conversation_store import conversation_store
from .schemas import YouTubeCommentStartRequest, YouTubeCommentStopRequest
from .youtube_comment_service import youtube_comment_service


router = APIRouter(prefix="/api/youtube", tags=["youtube"])


def ensure_conversation_exists(conversation_id: str) -> None:
    if not conversation_store.has_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.post("/start")
def start_youtube_comments(payload: YouTubeCommentStartRequest) -> dict:
    ensure_conversation_exists(payload.conversation_id)
    try:
        snapshot = youtube_comment_service.start(payload.conversation_id, payload.video_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, **snapshot}


@router.post("/stop")
def stop_youtube_comments(payload: YouTubeCommentStopRequest) -> dict:
    snapshot = youtube_comment_service.stop(payload.conversation_id)
    return {"success": True, **snapshot}


@router.get("/comments/{conversation_id}")
def get_youtube_comments(
    conversation_id: str,
    since_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    ensure_conversation_exists(conversation_id)
    snapshot = youtube_comment_service.get_comments(conversation_id, since_seq=since_seq, limit=limit)
    return {"success": True, **snapshot}