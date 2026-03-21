from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

from .models import ActiveCall, MetadataEvent, PCMChunk, PacketEmission, new_active_call
from .packet_emitter import PacketEmitter
from .wav_writer import write_pcm16_mono_wav


class BufferManager:
    def __init__(self, output_dir: Path, inactivity_timeout_s: float, default_sample_rate: int = 8000) -> None:
        self.output_dir = output_dir
        self.inactivity_timeout_s = inactivity_timeout_s
        self.default_sample_rate = default_sample_rate
        self.buffer_map: dict[int, ActiveCall] = {}
        self.packet_emitter = PacketEmitter()

    def handle_metadata(self, event: MetadataEvent) -> list[PacketEmission]:
        now = time.monotonic()
        emitted: list[PacketEmission] = []
        if event.tgid is None:
            return emitted

        if event.event_type in {"call_start", "channel_grant", "channel_update"}:
            call = self.buffer_map.get(event.tgid)
            if call is None:
                self.buffer_map[event.tgid] = new_active_call(
                    tgid=event.tgid,
                    now_monotonic=now,
                    source_radio_id=event.source_radio_id,
                    frequency=event.frequency,
                )
            else:
                call.last_pcm_at_monotonic = now
                if event.source_radio_id is not None:
                    call.source_radio_id = event.source_radio_id
                if event.frequency is not None:
                    call.frequency = event.frequency
        elif event.event_type == "call_end":
            emitted.extend(self._close_tgid(event.tgid))
        return emitted

    def handle_pcm(self, chunk: PCMChunk) -> None:
        if chunk.tgid is None:
            return
        now = time.monotonic()
        call = self.buffer_map.get(chunk.tgid)
        if call is None:
            call = new_active_call(chunk.tgid, now)
            self.buffer_map[chunk.tgid] = call
        call.pcm_chunks.append(chunk.pcm)
        call.last_pcm_at_monotonic = now
        call.sample_rate = chunk.sample_rate or self.default_sample_rate

    def poll_timeouts(self) -> list[PacketEmission]:
        now = time.monotonic()
        expired = [tgid for tgid, call in self.buffer_map.items() if now - call.last_pcm_at_monotonic > self.inactivity_timeout_s]
        emitted: list[PacketEmission] = []
        for tgid in expired:
            emitted.extend(self._close_tgid(tgid))
        return emitted

    def _close_tgid(self, tgid: int) -> list[PacketEmission]:
        call = self.buffer_map.pop(tgid, None)
        if call is None or not call.pcm_chunks:
            return []
        safe_src = call.source_radio_id or "unknown"
        filename = f"{call.timestamp_start}_{call.tgid}_{safe_src}_{call.packet_id}.wav".replace(":", "-")
        wav_path = self.output_dir / filename
        write_pcm16_mono_wav(wav_path, call.pcm_chunks, call.sample_rate or self.default_sample_rate)
        return [self.packet_emitter.build_packet(call, wav_path)]

    def active_tgids(self) -> Iterable[int]:
        return self.buffer_map.keys()
