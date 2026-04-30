from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from itertools import count
from typing import Any

try:
    import pytchat
except ImportError:  # pragma: no cover - optional dependency at runtime
    pytchat = None


def normalize_video_id(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError("YouTube の videoId または URL を入力してください。")

    if "youtube.com" in candidate or "youtu.be" in candidate:
        patterns = [
            r"[?&]v=([A-Za-z0-9_-]{6,})",
            r"youtu\.be/([A-Za-z0-9_-]{6,})",
            r"/live/([A-Za-z0-9_-]{6,})",
            r"/embed/([A-Za-z0-9_-]{6,})",
        ]
        for pattern in patterns:
            match = re.search(pattern, candidate)
            if match:
                return match.group(1)

    cleaned = re.sub(r"\s+", "", candidate)
    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", cleaned):
        return cleaned

    raise ValueError("YouTube の videoId を解釈できませんでした。")


@dataclass(slots=True)
class YouTubeCommentItem:
    seq: int
    comment_id: str
    author_name: str
    message: str
    published_at: str
    timestamp: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "comment_id": self.comment_id,
            "author_name": self.author_name,
            "message": self.message,
            "published_at": self.published_at,
            "timestamp": self.timestamp,
        }


class YouTubeCommentSession:
    def __init__(self, conversation_id: str, video_id: str) -> None:
        self.conversation_id = conversation_id
        self.video_id = video_id
        self._comments: deque[YouTubeCommentItem] = deque(maxlen=200)
        self._seen_ids: deque[str] = deque(maxlen=500)
        self._seen_id_set: set[str] = set()
        self._seq = count(1)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"ytchat-{conversation_id}", daemon=True)
        self.running = False
        self.last_error: str | None = None

    def start(self) -> None:
        self.running = True
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.running = False

    def snapshot(self, since_seq: int = 0, limit: int = 20) -> dict[str, Any]:
        with self._lock:
            comments = [item.to_dict() for item in self._comments if item.seq > since_seq]
        if limit > 0:
            comments = comments[:limit]
        next_seq = comments[-1]["seq"] if comments else since_seq
        return {
            "running": self.running,
            "video_id": self.video_id,
            "comments": comments,
            "next_seq": next_seq,
            "error": self.last_error,
        }

    def _remember_seen_id(self, comment_id: str) -> bool:
        if comment_id in self._seen_id_set:
            return False
        if len(self._seen_ids) >= self._seen_ids.maxlen:
            removed = self._seen_ids.popleft()
            self._seen_id_set.discard(removed)
        self._seen_ids.append(comment_id)
        self._seen_id_set.add(comment_id)
        return True

    def _append_comment(self, *, comment_id: str, author_name: str, message: str, published_at: str, timestamp: int | None) -> None:
        if not message:
            return
        if not self._remember_seen_id(comment_id):
            return
        item = YouTubeCommentItem(
            seq=next(self._seq),
            comment_id=comment_id,
            author_name=author_name,
            message=message,
            published_at=published_at,
            timestamp=timestamp,
        )
        with self._lock:
            self._comments.append(item)

    def _run(self) -> None:
        if pytchat is None:
            self.last_error = "pytchat がインストールされていません。"
            self.running = False
            return

        while not self._stop_event.is_set():
            livechat = None
            try:
                livechat = pytchat.create(video_id=self.video_id, interruptable=False)
                self.last_error = None
                while livechat.is_alive() and not self._stop_event.is_set():
                    chat_data = livechat.get()
                    for comment in getattr(chat_data, "items", []):
                        raw_comment_id = getattr(comment, "id", None)
                        author_name = getattr(getattr(comment, "author", None), "name", "") or "viewer"
                        message = (getattr(comment, "message", "") or "").strip()
                        published_at = getattr(comment, "datetime", "") or ""
                        timestamp = getattr(comment, "timestamp", None)
                        fallback_id = f"{author_name}:{published_at}:{message}"
                        comment_id = str(raw_comment_id or fallback_id)
                        self._append_comment(
                            comment_id=comment_id,
                            author_name=author_name,
                            message=message,
                            published_at=published_at,
                            timestamp=timestamp,
                        )
                    time.sleep(1.0)
            except Exception as exc:  # pragma: no cover - depends on live API state
                self.last_error = str(exc)
                time.sleep(3.0)
            finally:
                if livechat is not None:
                    try:
                        livechat.terminate()
                    except Exception:
                        pass

        self.running = False


class YouTubeCommentService:
    def __init__(self) -> None:
        self._sessions: dict[str, YouTubeCommentSession] = {}
        self._lock = threading.Lock()

    def start(self, conversation_id: str, video_id_or_url: str) -> dict[str, Any]:
        video_id = normalize_video_id(video_id_or_url)
        with self._lock:
            existing = self._sessions.get(conversation_id)
            if existing is not None:
                existing.stop()

            session = YouTubeCommentSession(conversation_id=conversation_id, video_id=video_id)
            self._sessions[conversation_id] = session
            session.start()
        return session.snapshot()

    def stop(self, conversation_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(conversation_id, None)
        if session is None:
            return {"running": False, "comments": [], "next_seq": 0, "error": None, "video_id": None}
        session.stop()
        snapshot = session.snapshot()
        snapshot["running"] = False
        return snapshot

    def get_comments(self, conversation_id: str, *, since_seq: int = 0, limit: int = 20) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(conversation_id)
        if session is None:
            return {"running": False, "comments": [], "next_seq": since_seq, "error": None, "video_id": None}
        return session.snapshot(since_seq=since_seq, limit=limit)


youtube_comment_service = YouTubeCommentService()