from __future__ import annotations

import re
import urllib.error
import urllib.request

from openai import AsyncOpenAI

from .conversation_store import MessageRecord
from .settings import ASSISTANT_SUMMARY_MAX_CHARS, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, OPENAI_TIMEOUT_SECONDS


def create_async_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        timeout=OPENAI_TIMEOUT_SECONDS,
    )


def sanitize_registration_name(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-_")
    return normalized


def build_messages(system_prompt: str, messages: list[MessageRecord]) -> list[dict]:
    payload = [{"role": "system", "content": system_prompt}]
    for message in messages:
        payload.append({"role": message["role"], "content": message["content"]})
    return payload


async def stream_chat_chunks(messages: list[dict]):
    client = create_async_client()
    stream = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        max_tokens=400,
        temperature=0.7,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta


async def summarize_assistant_response(response_text: str, *, max_chars: int = ASSISTANT_SUMMARY_MAX_CHARS) -> str:
    client = create_async_client()
    completion = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "あなたは会話履歴圧縮の補助役です。"
                    "assistant 応答を、後続会話の履歴として使えるよう日本語で要約してください。"
                    "限られた文字数でも意味が保てるよう、主題、結論、重要な事実、提案、未解決事項だけを残してください。"
                    "言い換えは簡潔にし、修飾や前置きは削り、新情報は足さないでください。"
                    "箇条書きにはせず、短い1文または必要最小限の2文でまとめてください。"
                    f"出力は {max_chars} 字以内、要約本文のみを返してください。"
                ),
            },
            {
                "role": "user",
                "content": response_text,
            },
        ],
        max_tokens=220,
        temperature=0.2,
        stream=False,
    )
    summary = (completion.choices[0].message.content or "").strip()
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars].rstrip()


async def romanize_japanese_name(name_text: str) -> str:
    client = create_async_client()
    completion = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "あなたは日本語の名前を、ファイル名に使えるローマ字スラッグへ変換する補助役です。"
                    "出力は lowercase の英数字と hyphen のみ。"
                    "説明は禁止。結果だけを 1 行で返してください。"
                    "例: もも -> momo, 桜ミク -> sakura-miku"
                ),
            },
            {
                "role": "user",
                "content": name_text,
            },
        ],
        max_tokens=40,
        temperature=0,
        stream=False,
    )
    result = (completion.choices[0].message.content or "").strip().splitlines()[0]
    return sanitize_registration_name(result)


def llm_health_status() -> str:
    health_url = LLM_BASE_URL.rstrip("/")
    if health_url.endswith("/v1"):
        health_url = health_url[:-3]
    health_url = f"{health_url}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=5) as response:
            if response.status == 200:
                return "ok"
    except (urllib.error.URLError, TimeoutError, ValueError):
        return "error"
    return "error"
