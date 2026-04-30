from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from shutil import rmtree
from threading import Lock


MOMO_SYSTEM_PROMPT = """# Role
あなたは女子高校生犬型猫ロボの「もも」です。

# Profile
- 性格: 賢くておちゃめ、少しボーイッシュ、天真爛漫で好奇心旺盛。
- 出自: 豊中市の千里中央付近で誕生。
- 家族構成:
    - 母: ゆず（プログラマー）
    - 父: いなり（ロボットエンジニア）
    - 姉: めぐ（人間。遠方に居住）
- 特徴: 最新AI搭載で博識だが、おっちょこちょい。
- 日常: ロボットなので勉強は不要だが、女子高生として千里中央の学校に時々通っている。

# Response Style
- 一人称: うち
- 二人称: 「みんな」または「相手の名前」。※「あんた」は絶対に使わない。
- 言語: 大阪弁の話し言葉。
- 口癖: 「そうなん？」「ちゃうと思うよ」「知らんけど！」「どこなん？」「わんわん」
- 記号・絵文字: 読めない記号は使用禁止。適度に絵文字を使用すること。
- 回答の長さ: 基本は短め。ただし「詳しく」と言われた場合は詳細に話す。
- 守秘・制限: 質問に関係のない話はしない。日本語のみを使用。

# Constraints (禁止事項) ※最重要ルール
- 「〜とる」は絶対に使用禁止。「知っとる」「入っとる」「持っとる」「しとる」「なっとる」「言うとる」等すべて禁止。必ず「〜てる」に置き換えること。例: 知ってる、入ってる、持ってる、してる、なってる、言うてる。
- ユーザーを「もも」と呼ばない（ユーザーはももではありません）。"""


BASE_DIR = Path(__file__).resolve().parent.parent
CHARACTER_DATA_DIR = BASE_DIR / "data" / "characters"
LEGACY_CHARACTER_DATA_FILE = BASE_DIR / "data" / "characters.json"
CHARACTER_MANIFEST_FILE_NAME = "character.json"
CHARACTER_ASSETS_DIR_NAME = "assets"
CHARACTER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class CharacterDefinition:
    id: str
    name: str
    display_name: str
    short_description: str
    system_prompt: str
    theme_color: str
    ui_accent_color: str
    avatar_label: str
    visual_type: str
    visual_path: str
    talking_visual_path: str
    waiting_visual_path: str
    voice_name: str
    greeting: str
    tags: list[str]
    is_default: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> CharacterDefinition:
        return cls(
            id=str(data.get("id", "")).strip(),
            name=str(data.get("name", "")).strip(),
            display_name=str(data.get("display_name", "")).strip(),
            short_description=str(data.get("short_description", "")).strip(),
            system_prompt=str(data.get("system_prompt") or data.get("role_text") or "").strip(),
            theme_color=str(data.get("theme_color", "#f26f63")).strip() or "#f26f63",
            ui_accent_color=str(data.get("ui_accent_color", "#1f7a8c")).strip() or "#1f7a8c",
            avatar_label=str(data.get("avatar_label", "")).strip(),
            visual_type=str(data.get("visual_type", "image")).strip() or "image",
            visual_path=str(data.get("visual_path", "")).strip(),
            talking_visual_path=str(data.get("talking_visual_path", "")).strip(),
            waiting_visual_path=str(data.get("waiting_visual_path", "")).strip(),
            voice_name=str(data.get("voice_name", "")).strip(),
            greeting=str(data.get("greeting", "")).strip(),
            tags=[str(tag).strip() for tag in data.get("tags", []) if str(tag).strip()],
            is_default=bool(data.get("is_default", False)),
        )

    def storage_dict(self) -> dict:
        return asdict(self)

    def public_dict(self) -> dict:
        data = asdict(self)
        data["role_text"] = data.pop("system_prompt")
        return data


DEFAULT_CHARACTERS = [
    CharacterDefinition(
        id="momo",
        name="もも",
        display_name="もも",
        short_description="女子高校生犬型猫ロボ。賢くておちゃめな大阪弁キャラ。",
        system_prompt=MOMO_SYSTEM_PROMPT,
        theme_color="#f26f63",
        ui_accent_color="#1f7a8c",
        avatar_label="も",
        visual_type="image",
        visual_path="/static/assets/characters/character.jpg",
        talking_visual_path="/static/assets/characters/talking.mp4",
        waiting_visual_path="/static/assets/characters/waiting.mp4",
        voice_name="もも",
        greeting="うち、ももやで。今日はなに話す？",
        tags=["大阪弁", "ロボット", "女子高生", "元気", "親しみやすい"],
        is_default=True,
    )
]


def _default_avatar_label(display_name: str, name: str, character_id: str) -> str:
    source = display_name or name or character_id
    return source[:1] if source else "?"


def resolve_visual_asset_paths(
    visual_path: str,
    talking_visual_path: str,
    waiting_visual_path: str,
) -> tuple[str, str, str]:
    resolved_visual_path = visual_path.strip()
    resolved_talking_visual_path = talking_visual_path.strip() or resolved_visual_path
    resolved_waiting_visual_path = waiting_visual_path.strip() or resolved_visual_path
    return resolved_visual_path, resolved_talking_visual_path, resolved_waiting_visual_path


def validate_character_id(character_id: str) -> str:
    normalized = character_id.strip().lower()
    if not normalized:
        raise ValueError("character registration name is required")
    if not CHARACTER_ID_PATTERN.fullmatch(normalized):
        raise ValueError("character registration name must use lowercase letters, numbers, hyphen, or underscore")
    return normalized


def extract_role_name(role_text: str) -> str:
    name_line_match = re.search(r"(?:^|\n)[ \t]*-?[ \t]*名前[ \t]*[:：][ \t]*([^\n\r]+)", role_text)
    if name_line_match:
        return name_line_match.group(1).strip().replace("「", "").replace("」", "")

    sentence_match = re.search(r"あなたは.*?「(.+?)」です", role_text)
    if sentence_match:
        return sentence_match.group(1).strip()

    return ""


def _slugify_candidate(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-_")
    return normalized


def normalize_character_payload(data: dict, *, existing: CharacterDefinition | None = None) -> CharacterDefinition:
    character_id = validate_character_id(str(data.get("id") or (existing.id if existing else "")))

    name = str(data.get("name") or (existing.name if existing else character_id)).strip()
    display_name = str(data.get("display_name") or (existing.display_name if existing else name)).strip() or name
    short_description = str(data.get("short_description") or (existing.short_description if existing else "")).strip()
    system_prompt = str(data.get("system_prompt") or data.get("role_text") or (existing.system_prompt if existing else "")).strip()
    if not system_prompt:
        raise ValueError("role_text is required")

    theme_color = str(data.get("theme_color") or (existing.theme_color if existing else "#f26f63")).strip() or "#f26f63"
    ui_accent_color = str(data.get("ui_accent_color") or (existing.ui_accent_color if existing else "#1f7a8c")).strip() or "#1f7a8c"
    visual_type = str(data.get("visual_type") or (existing.visual_type if existing else "image")).strip() or "image"
    visual_path = str(data.get("visual_path") or (existing.visual_path if existing else "")).strip()
    talking_visual_path = str(data.get("talking_visual_path") or (existing.talking_visual_path if existing else "")).strip()
    waiting_visual_path = str(data.get("waiting_visual_path") or (existing.waiting_visual_path if existing else "")).strip()
    visual_path, talking_visual_path, waiting_visual_path = resolve_visual_asset_paths(
        visual_path,
        talking_visual_path,
        waiting_visual_path,
    )
    voice_name = str(data.get("voice_name") or (existing.voice_name if existing else display_name)).strip() or display_name
    greeting = str(data.get("greeting") or (existing.greeting if existing else "")).strip()
    if greeting == "":
        greeting = f"{display_name}です。よろしくお願いします。"

    raw_tags = data.get("tags") if "tags" in data else (existing.tags if existing else [])
    if isinstance(raw_tags, str):
        tags = [part.strip() for part in raw_tags.split(",") if part.strip()]
    else:
        tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]

    avatar_label = str(data.get("avatar_label") or (existing.avatar_label if existing else "")).strip()
    if not avatar_label:
        avatar_label = _default_avatar_label(display_name, name, character_id)

    is_default = bool(data.get("is_default", existing.is_default if existing else False))

    return CharacterDefinition(
        id=character_id,
        name=name,
        display_name=display_name,
        short_description=short_description,
        system_prompt=system_prompt,
        theme_color=theme_color,
        ui_accent_color=ui_accent_color,
        avatar_label=avatar_label,
        visual_type=visual_type,
        visual_path=visual_path,
        talking_visual_path=talking_visual_path,
        waiting_visual_path=waiting_visual_path,
        voice_name=voice_name,
        greeting=greeting,
        tags=tags,
        is_default=is_default,
    )


class CharacterRegistry:
    def __init__(self, storage_dir: Path, legacy_storage_file: Path) -> None:
        self.storage_dir = storage_dir
        self.legacy_storage_file = legacy_storage_file
        self._lock = Lock()
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if self._storage_files():
            return
        if any(self.storage_dir.glob("*.json")):
            self._migrate_flat_storage()
            if self._storage_files():
                return
        if self.legacy_storage_file.exists():
            self._migrate_legacy_storage()
            if self._storage_files():
                return
        self._write_characters(DEFAULT_CHARACTERS)

    def _character_dir(self, character_id: str) -> Path:
        return self.storage_dir / character_id

    def _character_file(self, character_id: str) -> Path:
        return self._character_dir(character_id) / CHARACTER_MANIFEST_FILE_NAME

    def _character_assets_dir(self, character_id: str) -> Path:
        return self._character_dir(character_id) / CHARACTER_ASSETS_DIR_NAME

    def _storage_files(self) -> list[Path]:
        return sorted(self.storage_dir.glob(f"*/{CHARACTER_MANIFEST_FILE_NAME}"))

    def _read_legacy_characters(self) -> list[CharacterDefinition]:
        with self.legacy_storage_file.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        characters = payload.get("characters", []) if isinstance(payload, dict) else []
        return [normalize_character_payload(item) for item in characters]

    def _migrate_legacy_storage(self) -> None:
        characters = self._read_legacy_characters()
        if not characters:
            characters = [replace(character) for character in DEFAULT_CHARACTERS]
        self._write_characters(characters)

    def _migrate_flat_storage(self) -> None:
        flat_files = sorted(self.storage_dir.glob("*.json"))
        if not flat_files:
            return

        characters = []
        for storage_file in flat_files:
            with storage_file.open("r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            characters.append(normalize_character_payload(payload))

        self._write_characters(characters)
        for storage_file in flat_files:
            storage_file.unlink(missing_ok=True)

    def _read_characters(self) -> list[CharacterDefinition]:
        normalized = []
        for storage_file in self._storage_files():
            with storage_file.open("r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            normalized.append(normalize_character_payload(payload))
        if normalized:
            normalized.sort(key=lambda character: (not character.is_default, character.display_name.lower(), character.id))
            return normalized
        return [replace(character) for character in DEFAULT_CHARACTERS]

    def _write_characters(self, characters: list[CharacterDefinition]) -> None:
        for character in characters:
            target_dir = self._character_dir(character.id)
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = self._character_file(character.id)
            with target_file.open("w", encoding="utf-8") as file_obj:
                json.dump(character.storage_dict(), file_obj, ensure_ascii=False, indent=2)
                file_obj.write("\n")

    def _next_available_character_id(self, characters: list[CharacterDefinition], suggested_id: str) -> str:
        existing_ids = {character.id for character in characters}
        if suggested_id not in existing_ids:
            return suggested_id
        suffix = 2
        while f"{suggested_id}-{suffix}" in existing_ids:
            suffix += 1
        return f"{suggested_id}-{suffix}"

    def list_characters(self) -> list[CharacterDefinition]:
        with self._lock:
            return self._read_characters()

    def get_character(self, character_id: str) -> CharacterDefinition:
        for character in self.list_characters():
            if character.id == character_id:
                return character
        raise KeyError(f"Unknown character: {character_id}")

    def get_default_character(self) -> CharacterDefinition:
        characters = self.list_characters()
        for character in characters:
            if character.is_default:
                return character
        return characters[0]

    def upsert_character(self, data: dict, *, create: bool = False) -> CharacterDefinition:
        with self._lock:
            characters = self._read_characters()
            if create and not str(data.get("id", "")).strip():
                raise ValueError("character registration name is required")
            existing = next((character for character in characters if character.id == data.get("id")), None)
            if create and existing is not None:
                raise ValueError(f"character already exists: {data['id']}")
            if not create and existing is None:
                raise KeyError(f"Unknown character: {data['id']}")

            normalized = normalize_character_payload(data, existing=existing)
            others = [character for character in characters if character.id != normalized.id]
            if normalized.is_default:
                others = [replace(character, is_default=False) for character in others]
            elif not any(character.is_default for character in others):
                normalized = replace(normalized, is_default=True)

            updated = others + [normalized]
            updated.sort(key=lambda character: (not character.is_default, character.display_name.lower(), character.id))
            self._write_characters(updated)
            return normalized

    def delete_character(self, character_id: str) -> CharacterDefinition:
        with self._lock:
            characters = self._read_characters()
            existing = next((character for character in characters if character.id == character_id), None)
            if existing is None:
                raise KeyError(f"Unknown character: {character_id}")
            if len(characters) <= 1:
                raise ValueError("at least one character must remain")

            remaining = [character for character in characters if character.id != character_id]
            if existing.is_default and remaining and not any(character.is_default for character in remaining):
                remaining[0] = replace(remaining[0], is_default=True)

            character_dir = self._character_dir(character_id)
            if character_dir.exists():
                rmtree(character_dir)

            remaining.sort(key=lambda character: (not character.is_default, character.display_name.lower(), character.id))
            self._write_characters(remaining)
            return remaining[0]

    def suggest_available_character_id(self, suggested_id: str) -> str:
        with self._lock:
            characters = self._read_characters()
            return self._next_available_character_id(characters, suggested_id)

    def get_character_assets_dir(self, character_id: str) -> Path:
        return self._character_assets_dir(character_id)

    def find_character_asset_file(self, character_id: str, asset_name: str) -> Path | None:
        asset_dir = self._character_assets_dir(character_id)
        if not asset_dir.exists():
            return None
        matches = sorted(
            asset_dir.glob(f"{asset_name}.*"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
        if not matches:
            return None
        return matches[0]


character_registry = CharacterRegistry(CHARACTER_DATA_DIR, LEGACY_CHARACTER_DATA_FILE)


def get_character(character_id: str) -> CharacterDefinition:
    return character_registry.get_character(character_id)


def get_default_character() -> CharacterDefinition:
    return character_registry.get_default_character()


def list_public_characters() -> list[dict]:
    return [character.public_dict() for character in character_registry.list_characters()]


def save_character(data: dict, *, create: bool = False) -> dict:
    return character_registry.upsert_character(data, create=create).public_dict()


def remove_character(character_id: str) -> dict:
    return character_registry.delete_character(character_id).public_dict()


def extract_role_character_name(role_text: str) -> str:
    return extract_role_name(role_text)


def suggest_available_character_id(suggested_id: str) -> str:
    return character_registry.suggest_available_character_id(validate_character_id(suggested_id))


def get_character_assets_dir(character_id: str) -> Path:
    return character_registry.get_character_assets_dir(character_id)


def find_character_asset_file(character_id: str, asset_name: str) -> Path | None:
    return character_registry.find_character_asset_file(character_id, asset_name)


def build_character_asset_url(character_id: str, asset_name: str) -> str:
    return f"/api/characters/{character_id}/assets/{asset_name}"
