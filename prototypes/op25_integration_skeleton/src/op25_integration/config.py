from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Op25IntegrationConfig:
    """Runtime configuration for the OP25 integration module."""

    call_end_timeout_ms: int = 1200
    audio_buffer_seconds: int = 10
    pre_roll_seconds: int = 3
    post_roll_seconds: int = 1
    poll_interval_ms: int = 100
    recordings_root: Path = Path("./recordings")
    failed_packets_root: Path = Path("./failed_packets")

    # Assumed PCM format for WAV output.
    sample_rate_hz: int = 8000
    channels: int = 1
    sample_width_bytes: int = 2

    @property
    def call_end_timeout_seconds(self) -> float:
        return self.call_end_timeout_ms / 1000.0

    @property
    def poll_interval_seconds(self) -> float:
        return self.poll_interval_ms / 1000.0
