from __future__ import annotations

import re
import wave
from datetime import datetime, timezone
from pathlib import Path

from .models import CompletedCall
from .settings import PCM_CHANNELS, PCM_SAMPLE_WIDTH_BYTES


class WavWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, call: CompletedCall, packet_id: str) -> Path:
        start = datetime.fromtimestamp(call.started_at, tz=timezone.utc)
        stamp = start.strftime("%Y%m%dT%H%M%SZ")
        src = _safe(call.source_radio_id or "unknown")
        filename = f"{stamp}_{call.tgid}_{src}_{packet_id}.wav"
        path = self.output_dir / filename

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(PCM_CHANNELS)
            wf.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
            wf.setframerate(call.sample_rate)
            wf.writeframes(call.audio_bytes)
        return path


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)
