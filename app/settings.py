from __future__ import annotations

import os


LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "YOUR_OPENAI_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "unsloth/gemma-4-E4B-it-GGUF:Q4_K_M")
ASSISTANT_SUMMARY_THRESHOLD_CHARS = int(os.getenv("ASSISTANT_SUMMARY_THRESHOLD_CHARS", "150"))
ASSISTANT_SUMMARY_MAX_CHARS = int(os.getenv("ASSISTANT_SUMMARY_MAX_CHARS", "100"))

MAX_HISTORY_PAIRS = int(os.getenv("MAX_HISTORY_PAIRS", "10"))
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "300"))
TTS_ENABLED = os.getenv("TTS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
TTS_URL = os.getenv("TTS_URL", "http://127.0.0.1:10101")
TTS_SPEAKER_ID = os.getenv("TTS_SPEAKER_ID", "888753760")
TTS_AUDIO_FORMAT = os.getenv("TTS_AUDIO_FORMAT", "wav")
TTS_TIMEOUT_SECONDS = float(os.getenv("TTS_TIMEOUT_SECONDS", "30"))

APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8005"))
