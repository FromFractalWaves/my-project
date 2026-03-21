from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class CaptureConfig:
    pcm_bind: str = "tcp://127.0.0.1:5556"
    metadata_bind: str = "tcp://127.0.0.1:5557"
    output_dir: Path = Path("./recordings")
    inactivity_timeout_s: float = 1.5
    poll_timeout_ms: int = 50
    default_sample_rate: int = 8000
