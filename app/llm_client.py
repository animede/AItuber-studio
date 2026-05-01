from __future__ import annotations

import re
import urllib.error
import urllib.request

from openai import AsyncOpenAI

from .conversation_store import MessageRecord
from .settings import ASSISTANT_SUMMARY_MAX_CHARS, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, OPENAI_TIMEOUT_SECONDS


MessageContent = str | list[dict[str, object]]


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


def build_messages(
    system_prompt: str,
    messages: list[MessageRecord],
    *,
    latest_user_content_override: MessageContent | None = None,
) -> list[dict]:
    payload = [{"role": "system", "content": system_prompt}]
    last_user_message_id = None
    if latest_user_content_override is not None:
        for message in reversed(messages):
            if message["role"] == "user":
                last_user_message_id = message["message_id"]
                break

    for message in messages:
        content: MessageContent = message["content"]
        if latest_user_content_override is not None and message["message_id"] == last_user_message_id:
            content = latest_user_content_override
        payload.append({"role": message["role"], "content": content})
    return payload


def build_audio_input_content(*, audio_b64: str, audio_format: str, prompt_text: str | None = None) -> list[dict[str, object]]:
    content: list[dict[str, object]] = []
    if prompt_text and prompt_text.strip():
        content.append({"type": "text", "text": prompt_text.strip()})
    content.append(
        {
            "type": "input_audio",
            "input_audio": {
                "data": audio_b64,
                "format": audio_format,
            },
        }
    )
    return content


def build_image_analysis_content(
    *,
    image_b64: str,
    image_format: str,
    prompt_text: str | None = None,
) -> list[dict[str, object]]:
    normalized_format = image_format.strip().lower() or "jpeg"
    if normalized_format == "jpg":
        normalized_format = "jpeg"

    content: list[dict[str, object]] = []
    if prompt_text and prompt_text.strip():
        content.append({"type": "text", "text": prompt_text.strip()})
    content.append(
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/{normalized_format};base64,{image_b64}",
            },
        }
    )
    return content


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


async def analyze_image_snapshot(*, image_b64: str, image_format: str = "jpeg") -> str:
    client = create_async_client()
    completion = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "あなたはカメラ画像を確認する補助役です。"
                    "画像に写っている主要な対象、状況、変化だけを日本語で簡潔に説明してください。"
                    "推測を広げすぎず、見えている内容を優先し、2文以内で返してください。"
                    "出力は短い説明本文のみ。"
                ),
            },
            {
                "role": "user",
                "content": build_image_analysis_content(
                    image_b64=image_b64,
                    image_format=image_format,
                    prompt_text="このカメラ画像を簡潔に説明してください。",
                ),
            },
        ],
        max_tokens=120,
        temperature=0.2,
        stream=False,
    )
    return (completion.choices[0].message.content or "").strip()


def build_character_image_analysis_messages(
    *,
    image_b64: str,
    image_format: str,
    role_text: str | None,
) -> list[dict[str, object]]:
    normalized_role_text = (role_text or "").strip()
    system_prompt = (
        "あなたはカメラ画像を見たキャラクタ本人として話します。"
        "画像に写っている場面を、自分が今見た光景として日本語で自然に説明してください。"
        "見えている内容を優先し、推測は最小限に留め、2文以内で返してください。"
        "説明本文のみを返してください。"
    )
    if normalized_role_text:
        system_prompt = (
            "以下のキャラクター設定に必ず従って話してください。\n"
            f"{normalized_role_text}\n\n"
            "このキャラクタが今見た場面として、画像に写っている内容を自然に説明してください。"
            "見えている内容を優先し、推測は最小限に留め、2文以内で返してください。"
            "説明本文のみを返してください。"
        )

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": build_image_analysis_content(
                image_b64=image_b64,
                image_format=image_format,
                prompt_text="この画像を、いま自分が見た場面として説明してください。",
            ),
        },
    ]


async def analyze_character_image_snapshot(
    *,
    image_b64: str,
    image_format: str = "jpeg",
    role_text: str | None = None,
) -> str:
    client = create_async_client()
    completion = await client.chat.completions.create(
        model=LLM_MODEL,
        messages=build_character_image_analysis_messages(
            image_b64=image_b64,
            image_format=image_format,
            role_text=role_text,
        ),
        max_tokens=120,
        temperature=0.2,
        stream=False,
    )
    return (completion.choices[0].message.content or "").strip()


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
