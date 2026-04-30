from __future__ import annotations

import json
import re
from typing import Any
import urllib.parse
import urllib.request

from .settings import TTS_AUDIO_FORMAT, TTS_ENABLED, TTS_SPEAKER_ID, TTS_TIMEOUT_SECONDS, TTS_URL


def sanitize_tts_text(text: str) -> str:
    # Markdown 記号や絵文字だけを落として、日本語本文はそのまま残す。
    cleaned = text.replace("#", " ").replace("*", " ").replace("`", " ")
    # 文字ごとのフィルタで、読み上げを崩す絵文字系コードポイントを落とす。
    cleaned = "".join(_sanitize_char(char) for char in cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _sanitize_char(char: str) -> str:
    # 結合絵文字や装飾記号は TTS が崩れやすいので空白へ逃がす。
    codepoint = ord(char)
    if codepoint in {0x200D, 0xFE0F}:
        return " "
    if 0x1F300 <= codepoint <= 0x1FAFF:
        return " "
    if 0x2600 <= codepoint <= 0x27BF:
        return " "
    return char


class TTSClient:
    def __init__(
        self,
        *,
        base_url: str = TTS_URL,
        speaker_id: str = TTS_SPEAKER_ID,
        timeout_seconds: float = TTS_TIMEOUT_SECONDS,
        enabled: bool = TTS_ENABLED,
    ) -> None:
        # settings.py の既定値を受けつつ、テストでは差し替えられるようにしている。
        self.base_url = base_url.rstrip("/")
        self.speaker_id = str(speaker_id)
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.audio_format = TTS_AUDIO_FORMAT

    def is_configured(self) -> bool:
        # 明示的に TTS を有効化した設定かどうかを返す。
        return self.enabled and bool(self.base_url) and bool(self.speaker_id)

    def has_target(self) -> bool:
        # 実サーバへ疎通確認できる最小条件は、接続先 URL と speaker 指定があること。
        return bool(self.base_url) and bool(self.speaker_id)

    def has_live_engine(self) -> bool:
        # 環境変数の ON/OFF に関わらず、実サーバへ疎通できるかを返す。
        if not self.has_target():
            return False

        try:
            return bool(self.get_engine_version())
        except Exception:
            return False

    def is_available(self) -> bool:
        # 実サーバへ軽く疎通できてはじめて、Aivis / VOICEVOX 互換 TTS が使えると判断する。
        return self.has_live_engine()

    def get_engine_version(self) -> str:
        # /version は軽量なので、接続確認にもそのまま使う。
        version = self._request_json("/version")
        if not isinstance(version, str) or not version.strip():
            raise RuntimeError("TTS engine version response is invalid")
        return version

    def get_speakers(self) -> list[dict[str, Any]]:
        # AivisSpeech Engine の /speakers は、話者と style_id 一覧を返す。
        speakers = self._request_json("/speakers")
        if not isinstance(speakers, list):
            raise RuntimeError("TTS speakers response is invalid")
        return speakers

    def get_aivm_models(self) -> dict[str, dict[str, Any]]:
        # AivisSpeech Engine 独自の /aivm_models から、インストール済みモデル情報を取得する。
        models = self._request_json("/aivm_models")
        if not isinstance(models, dict):
            raise RuntimeError("TTS aivm_models response is invalid")
        return models

    def get_voice_catalog(self) -> dict[str, Any]:
        # フロントや運用確認で使いやすいよう、Aivis 固有情報を正規化して返す。
        version = self.get_engine_version()
        speakers = self.get_speakers()
        models = self.get_aivm_models()
        normalized_models = []
        for model_uuid, model in models.items():
            manifest = model.get("manifest") or {}
            model_speakers = model.get("speakers") or manifest.get("speakers") or []
            normalized_models.append(
                {
                    "model_uuid": model_uuid,
                    "name": manifest.get("name") or model.get("name") or model_uuid,
                    "description": manifest.get("description") or "",
                    "is_loaded": bool(model.get("is_loaded")),
                    "is_default_model": bool(model.get("is_default_model")),
                    "speaker_count": len(model_speakers),
                    "file_path": model.get("file_path"),
                }
            )

        selected_style_id: int | str = self.speaker_id
        try:
            selected_style_id = int(self.speaker_id)
        except ValueError:
            pass

        selected_voice = None
        for speaker in speakers:
            for style in speaker.get("styles") or []:
                if style.get("id") == selected_style_id:
                    selected_voice = {
                        "speaker_name": speaker.get("name"),
                        "speaker_uuid": speaker.get("speaker_uuid"),
                        "style_id": style.get("id"),
                        "style_name": style.get("name"),
                        "style_type": style.get("type"),
                    }
                    break
            if selected_voice is not None:
                break

        return {
            "engine_name": "AivisSpeech Engine",
            "protocol": "voicevox-compatible",
            "base_url": self.base_url,
            "available": True,
            "version": version,
            "audio_format": self.audio_format,
            "default_style_id": selected_style_id,
            "selected_voice": selected_voice,
            "speaker_count": len(speakers),
            "model_count": len(normalized_models),
            "speakers": speakers,
            "models": normalized_models,
        }

    def get_status(self) -> dict[str, Any]:
        # health 用に、失敗時もレスポンス形を固定した状態情報を返す。
        configured = self.is_configured()
        status: dict[str, Any] = {
            "enabled": self.enabled,
            "configured": configured,
            "available": False,
            "base_url": self.base_url,
            "protocol": "voicevox-compatible",
            "audio_format": self.audio_format,
            "default_style_id": self.speaker_id,
            "version": None,
            "error": None,
        }
        if not self.has_target():
            status["error"] = "TTS target URL or speaker ID is missing"
            return status

        try:
            status["version"] = self.get_engine_version()
            status["available"] = True
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def synthesize(self, text: str, speaker_id: int | str | None = None) -> bytes:
        if not self.is_available():
            raise RuntimeError("TTS is disabled")

        sanitized = sanitize_tts_text(text)
        if not sanitized:
            # 記号除去の結果、読む本文が残らなければ無音として返す。
            return b""

        effective_speaker_id = str(speaker_id) if speaker_id is not None else self.speaker_id

        # AivisSpeech / VOICEVOX 互換仕様に合わせ、text と speaker はクエリへ載せる。
        query_data = self._request_json(
            "/audio_query",
            params={"text": sanitized, "speaker": effective_speaker_id},
            method="POST",
            body=b"",
        )

        synthesis_request = urllib.request.Request(
            self._build_url("/synthesis", {"speaker": effective_speaker_id}),
            data=json.dumps(query_data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(synthesis_request, timeout=self.timeout_seconds) as response:
            # 最終レスポンスは wav などの生 bytes としてそのまま返す。
            return response.read()

    def _build_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        # query 文字列生成を共通化し、各 API 呼び出しの差分だけを目立たせる。
        url = f"{self.base_url}{path}"
        if not params:
            return url
        return f"{url}?{urllib.parse.urlencode(params)}"

    def _request_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        # JSON レスポンス前提の GET/POST をまとめ、一覧取得系 API を実装しやすくする。
        request = urllib.request.Request(
            self._build_url(path, params),
            data=body,
            headers=headers or {},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))