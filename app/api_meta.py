from __future__ import annotations

import base64

from fastapi import APIRouter, HTTPException

from .character_registry import get_default_character
from .llm_client import analyze_character_image_snapshot, llm_health_status
from .conversation_store import conversation_store
from .schemas import ImageAnalysisRequest
from .settings import ASSISTANT_SUMMARY_MAX_CHARS, ASSISTANT_SUMMARY_THRESHOLD_CHARS, LLM_MODEL
from .tts_client import TTSClient


router = APIRouter(prefix="/api", tags=["meta"])
tts_client = TTSClient()


@router.get("/health")
def health() -> dict:
    default_character = get_default_character()
    llm_status = llm_health_status()
    tts_status = tts_client.get_status()
    return {
        "status": "ok",
        "llm_status": llm_status,
        "model": LLM_MODEL,
        "summary_threshold_chars": ASSISTANT_SUMMARY_THRESHOLD_CHARS,
        "summary_max_chars": ASSISTANT_SUMMARY_MAX_CHARS,
        "default_character_id": default_character.id,
        "tts_available": tts_status["available"],
        "tts_status": tts_status,
    }

@router.get("/tts/voices")
def tts_voices() -> dict:
    # TTS 未接続時でも frontend が安全に扱えるよう、応答形は固定で返す。
    try:
        return tts_client.get_voice_catalog()
    except Exception as exc:
        status = tts_client.get_status()
        return {
            "engine_name": "AivisSpeech Engine",
            "protocol": status["protocol"],
            "base_url": status["base_url"],
            "available": False,
            "version": status["version"],
            "audio_format": status["audio_format"],
            "default_style_id": status["default_style_id"],
            "selected_voice": None,
            "speaker_count": 0,
            "model_count": 0,
            "speakers": [],
            "models": [],
            "error": str(exc),
        }


@router.post("/image-analysis")
async def image_analysis(payload: ImageAnalysisRequest) -> dict:
    conversation = conversation_store.get_conversation(payload.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        analysis = await analyze_character_image_snapshot(
            image_b64=payload.image_b64,
            image_format=payload.image_format,
            role_text=payload.role_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"画像解析に失敗しました: {exc}") from exc

    message = conversation_store.append_assistant_message(
        payload.conversation_id,
        analysis,
    )

    audio_b64 = None
    audio_format = None
    if payload.audio_enabled:
        try:
            audio_bytes = tts_client.synthesize(analysis, payload.selected_style_id)
        except Exception:
            audio_bytes = b""
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
            audio_format = tts_client.audio_format

    return {
        "analysis": analysis,
        "message": message,
        "audio_b64": audio_b64,
        "audio_format": audio_format,
    }
