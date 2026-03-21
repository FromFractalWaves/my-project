from __future__ import annotations

import wave
from pathlib import Path


def write_pcm16_mono_wav(path: Path, pcm_chunks: list[bytes], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for chunk in pcm_chunks:
            wf.writeframes(chunk)
