from __future__ import annotations

from fastapi import APIRouter

from .character_registry import get_default_character
from .llm_client import llm_health_status
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
