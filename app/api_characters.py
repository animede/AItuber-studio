from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .character_registry import (
    build_character_asset_url,
    extract_role_character_name,
    find_character_asset_file,
    get_character,
    get_character_assets_dir,
    list_public_characters,
    remove_character,
    save_character,
    suggest_available_character_id,
    validate_character_id,
)
from .llm_client import romanize_japanese_name


router = APIRouter(prefix="/api", tags=["characters"])

REQUIRED_LIPSYNC_SPRITES = ("closed", "half", "open")
OPTIONAL_LIPSYNC_SPRITES = ("e", "u")

ASSET_FIELD_MAP = {
    "visual_upload": ("visual_path", "main"),
    "talking_upload": ("talking_visual_path", "talking"),
    "waiting_upload": ("waiting_visual_path", "waiting"),
}


def infer_visual_type_from_suffix(suffix: str) -> str:
    return "video" if suffix.lower() in {".mp4", ".webm", ".ogg", ".mov"} else "image"


async def persist_asset(character_id: str, asset_name: str, upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower() or ".bin"
    target_dir = get_character_assets_dir(character_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    for existing_path in target_dir.glob(f"{asset_name}.*"):
        existing_path.unlink(missing_ok=True)
    target_path = target_dir / f"{asset_name}{suffix}"
    with target_path.open("wb") as file_obj:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            file_obj.write(chunk)
    await upload.close()
    return build_character_asset_url(character_id, asset_name)


def parse_character_json(character_json: str) -> dict:
    try:
        payload = json.loads(character_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="character_json is invalid") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="character_json must be an object")
    return payload


def get_character_data_dir(character_id: str) -> Path:
    return get_character_assets_dir(character_id).parent


def find_waiting_lipsync_video_file(character_id: str) -> Path | None:
    character_dir = get_character_data_dir(character_id)
    if not character_dir.exists():
        return None

    patterns = (
        f"{character_id}_waiting_loop_mouthless_h264.mp4",
        "*_waiting_loop_mouthless_h264.mp4",
        "*waiting*mouthless*h264*.mp4",
        "*waiting*mouthless*.mp4",
    )
    for pattern in patterns:
        matches = sorted(
            character_dir.glob(pattern),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        if matches:
            return matches[0]
    return None


def get_lipsync_track_file(character_id: str) -> Path:
    return get_character_data_dir(character_id) / "mouth_track.json"


def get_lipsync_mouth_dir(character_id: str) -> Path:
    return get_character_data_dir(character_id) / "mouth"


def build_waiting_lipsync_manifest(character_id: str) -> dict:
    get_character(character_id)

    waiting_video_file = find_waiting_lipsync_video_file(character_id)
    track_file = get_lipsync_track_file(character_id)
    mouth_dir = get_lipsync_mouth_dir(character_id)

    sprite_urls: dict[str, str] = {}
    sprite_files: dict[str, Path] = {}
    for sprite_name in (*REQUIRED_LIPSYNC_SPRITES, *OPTIONAL_LIPSYNC_SPRITES):
        candidate = mouth_dir / f"{sprite_name}.png"
        if candidate.exists():
            sprite_files[sprite_name] = candidate
            sprite_urls[sprite_name] = f"/api/characters/{character_id}/lipsync/mouth/{sprite_name}"

    track_payload = None
    if track_file.exists():
        with track_file.open("r", encoding="utf-8") as file_obj:
            track_payload = json.load(file_obj)

    available = bool(waiting_video_file and track_payload and all(name in sprite_files for name in REQUIRED_LIPSYNC_SPRITES))
    return {
        "available": available,
        "character_id": character_id,
        "waiting_video_url": f"/api/characters/{character_id}/lipsync/waiting-video" if waiting_video_file else None,
        "sprite_urls": sprite_urls,
        "track": track_payload,
    }


async def build_character_id_from_role(role_text: str) -> tuple[str, str]:
    role_name = extract_role_character_name(role_text)
    if not role_name:
        raise HTTPException(status_code=400, detail="role must include a character name")
    try:
        suggested_id = await romanize_japanese_name(role_name)
        if not suggested_id:
            raise ValueError("empty registration name")
        return suggest_available_character_id(suggested_id), role_name
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"failed to generate character registration name: {exc}") from exc


@router.get("/characters")
def characters() -> dict:
    return {"characters": list_public_characters()}


@router.post("/characters/suggest-id")
async def suggest_character_id(payload: dict = Body(...)) -> dict:
    role_text = str(payload.get("role_text", "")).strip()
    suggested_id, role_name = await build_character_id_from_role(role_text)
    return {
        "suggested_id": suggested_id,
        "role_name": role_name,
    }


@router.get("/characters/{character_id}/assets/{asset_name}")
def character_asset(character_id: str, asset_name: str) -> FileResponse:
    if asset_name not in {"main", "talking", "waiting"}:
        raise HTTPException(status_code=404, detail="asset not found")
    asset_file = find_character_asset_file(character_id, asset_name)
    if asset_file is None or not asset_file.exists():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(asset_file)


@router.get("/characters/{character_id}/lipsync/manifest")
def character_lipsync_manifest(character_id: str) -> dict:
    try:
        return build_waiting_lipsync_manifest(character_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/characters/{character_id}/lipsync/waiting-video")
def character_lipsync_waiting_video(character_id: str) -> FileResponse:
    try:
        get_character(character_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    waiting_video_file = find_waiting_lipsync_video_file(character_id)
    if waiting_video_file is None or not waiting_video_file.exists():
        raise HTTPException(status_code=404, detail="lipsync waiting video not found")
    return FileResponse(waiting_video_file)


@router.get("/characters/{character_id}/lipsync/mouth/{sprite_name}")
def character_lipsync_mouth_sprite(character_id: str, sprite_name: str) -> FileResponse:
    try:
        get_character(character_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if sprite_name not in {*REQUIRED_LIPSYNC_SPRITES, *OPTIONAL_LIPSYNC_SPRITES}:
        raise HTTPException(status_code=404, detail="mouth sprite not found")

    sprite_file = get_lipsync_mouth_dir(character_id) / f"{sprite_name}.png"
    if not sprite_file.exists():
        raise HTTPException(status_code=404, detail="mouth sprite not found")
    return FileResponse(sprite_file)


@router.post("/characters")
async def create_character(
    character_json: str = Form(...),
    visual_upload: UploadFile | None = File(default=None),
    talking_upload: UploadFile | None = File(default=None),
    waiting_upload: UploadFile | None = File(default=None),
) -> dict:
    payload = parse_character_json(character_json)
    character_id = str(payload.get("id", "")).strip()
    if character_id:
        try:
            payload["id"] = validate_character_id(character_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        payload["id"], _ = await build_character_id_from_role(str(payload.get("role_text", "")).strip())

    uploads = {
        "visual_upload": visual_upload,
        "talking_upload": talking_upload,
        "waiting_upload": waiting_upload,
    }
    for field_name, upload in uploads.items():
        if upload is None or not upload.filename:
            continue
        _, asset_name = ASSET_FIELD_MAP[field_name]
        if not payload.get("id"):
            raise HTTPException(status_code=400, detail="character registration name is required when uploading assets")
        payload[ASSET_FIELD_MAP[field_name][0]] = await persist_asset(payload["id"], asset_name, upload)
        if field_name == "visual_upload" and not payload.get("visual_type"):
            payload["visual_type"] = infer_visual_type_from_suffix(Path(upload.filename).suffix)

    try:
        return {"character": save_character(payload, create=True)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/characters/{character_id}")
async def update_character(
    character_id: str,
    character_json: str = Form(...),
    visual_upload: UploadFile | None = File(default=None),
    talking_upload: UploadFile | None = File(default=None),
    waiting_upload: UploadFile | None = File(default=None),
) -> dict:
    try:
        existing = get_character(character_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = parse_character_json(character_json)
    payload["id"] = existing.id

    uploads = {
        "visual_upload": visual_upload,
        "talking_upload": talking_upload,
        "waiting_upload": waiting_upload,
    }
    for field_name, upload in uploads.items():
        if upload is None or not upload.filename:
            continue
        _, asset_name = ASSET_FIELD_MAP[field_name]
        payload[ASSET_FIELD_MAP[field_name][0]] = await persist_asset(character_id, asset_name, upload)
        if field_name == "visual_upload" and not payload.get("visual_type"):
            payload["visual_type"] = infer_visual_type_from_suffix(Path(upload.filename).suffix)

    try:
        return {"character": save_character(payload, create=False)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/characters/{character_id}")
def delete_character(character_id: str) -> dict:
    try:
        next_character = remove_character(character_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "deleted_character_id": character_id,
        "next_character": next_character,
        "characters": list_public_characters(),
    }