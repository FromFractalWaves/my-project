from __future__ import annotations

import json
import time
from dataclasses import asdict

from .buffer_manager import BufferManager
from .config import CaptureConfig


class CaptureRuntime:
    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self.buffer_manager = BufferManager(
            output_dir=config.output_dir,
            inactivity_timeout_s=config.inactivity_timeout_s,
            default_sample_rate=config.default_sample_rate,
        )

    def handle_metadata_event(self, event) -> list[dict]:
        emissions = self.buffer_manager.handle_metadata(event)
        return [e.packet.to_dict() for e in emissions]

    def handle_pcm_chunk(self, chunk) -> None:
        self.buffer_manager.handle_pcm(chunk)

    def poll(self) -> list[dict]:
        emissions = self.buffer_manager.poll_timeouts()
        return [e.packet.to_dict() for e in emissions]
