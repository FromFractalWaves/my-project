from __future__ import annotations

from collections import deque

from .models import AudioChunk, AudioSegment


class AudioBuffer:
    """Rolling FIFO buffer of recent audio chunks."""

    def __init__(self, max_duration_seconds: int) -> None:
        self.max_duration_ms = max_duration_seconds * 1000
        self._chunks: deque[AudioChunk] = deque()
        self._total_duration_ms = 0

    def append(self, chunk: AudioChunk) -> None:
        self._chunks.append(chunk)
        self._total_duration_ms += chunk.duration_ms
        self._evict_if_needed()

    def get_last(self, seconds: float) -> AudioSegment:
        target_ms = max(0, int(seconds * 1000))
        if target_ms == 0 or not self._chunks:
            return AudioSegment([])

        collected: list[AudioChunk] = []
        running_ms = 0
        for chunk in reversed(self._chunks):
            collected.append(chunk)
            running_ms += chunk.duration_ms
            if running_ms >= target_ms:
                break

        collected.reverse()
        return AudioSegment(collected)

    def _evict_if_needed(self) -> None:
        while self._total_duration_ms > self.max_duration_ms and self._chunks:
            removed = self._chunks.popleft()
            self._total_duration_ms -= removed.duration_ms
