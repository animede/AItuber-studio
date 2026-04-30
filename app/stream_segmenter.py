from __future__ import annotations

import re


HARD_BOUNDARIES = "。！？?!\n"
SOFT_BOUNDARIES = "、,"


class SentenceSegmenter:
    def __init__(self, *, max_chars: int = 80, split_on_soft_boundaries: bool = False) -> None:
        # ストリーム途中の断片をため、TTS に流しやすい文単位へ切り出す。
        self.max_chars = max_chars
        self.split_on_soft_boundaries = split_on_soft_boundaries
        self.buffer = ""

    def push(self, text: str) -> list[str]:
        if not text:
            return []
        # ストリーム断片を追記し、その時点で切り出せる文だけ返す。
        self.buffer += text
        return self._drain(final=False)

    def flush(self) -> list[str]:
        # ストリーム終了時に、残った未確定バッファも強制排出する。
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[str]:
        segments: list[str] = []
        while True:
            split_index = self._find_split_index(final=final)
            if split_index is None:
                break
            # 区切れた範囲だけ正規化して返し、残りは buffer に残す。
            segment = self._normalize(self.buffer[:split_index])
            self.buffer = self.buffer[split_index:]
            if segment:
                segments.append(segment)

        if final:
            # 最終 flush では、区切れなかった末尾も 1 文として回収する。
            tail = self._normalize(self.buffer)
            self.buffer = ""
            if tail:
                segments.append(tail)

        return segments

    def _find_split_index(self, *, final: bool) -> int | None:
        if self.split_on_soft_boundaries:
            for index, char in enumerate(self.buffer):
                if char in HARD_BOUNDARIES or char in SOFT_BOUNDARIES:
                    return index + 1
        else:
            # 既定モードでは、句点・感嘆符・改行などの強い区切りだけで切る。
            for index, char in enumerate(self.buffer):
                if char in HARD_BOUNDARIES:
                    return index + 1

        if not final and len(self.buffer) >= self.max_chars:
            # 長く伸びすぎたときだけ、読点や最大文字数で妥協して区切る。
            boundary_candidates = [self.buffer.rfind(boundary, 0, self.max_chars) for boundary in HARD_BOUNDARIES + SOFT_BOUNDARIES]
            split_at = max(boundary_candidates)
            if split_at >= 0:
                return split_at + 1
            return self.max_chars

        return None

    @staticmethod
    def _normalize(text: str) -> str:
        # TTS に不要な連続空白や改行を潰して、読み上げを安定させる。
        cleaned = text.replace("\r", " ").replace("\n", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()