from __future__ import annotations

import json
import logging
import wave
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Op25IntegrationConfig
from .event_bus import EventBus
from .models import AudioSegment, CallState, Packet, TransmissionPacket
from .storage import StorageBackend

logger = logging.getLogger(__name__)


class PacketAssembler:
    """Turns completed CallState objects into persisted TransmissionPackets."""

    def __init__(
        self,
        config: Op25IntegrationConfig,
        storage: StorageBackend,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.storage = storage
        self.event_bus = event_bus

    def finalize_call(self, call_state: CallState) -> TransmissionPacket:
        ended_at = datetime.now(tz=UTC).timestamp()
        audio_segment = AudioSegment(list(call_state.audio_segments))
        metadata: dict[str, Any] = dict(call_state.metadata)
        metadata.setdefault("audio_write_failed", False)
        metadata.setdefault("db_insert_failed", False)

        audio_path: str | None = None
        try:
            audio_path = str(self._build_recording_path(call_state.transmission_id, ended_at))
            self.save_audio(audio_segment, audio_path)
        except Exception:
            logger.exception("Failed to write audio for call %s", call_state.transmission_id)
            metadata["audio_write_failed"] = True
            audio_path = None

        packet = TransmissionPacket(
            packet_id=call_state.transmission_id,
            packet_type="transmission",
            timestamp=call_state.start_time,
            source=call_state.as_packet_source(),
            metadata=metadata,
            payload={
                "audio_path": audio_path,
                "frequency": call_state.frequency,
                "encrypted": call_state.encrypted,
            },
            transmission_id=call_state.transmission_id,
            timestamp_start=call_state.start_time,
            timestamp_end=ended_at,
            talkgroup_id=call_state.talkgroup_id,
            source_ids=sorted(call_state.source_ids),
            frequency=call_state.frequency,
            encrypted=call_state.encrypted,
            audio_path=audio_path,
        )

        try:
            self.insert_packet(packet)
        except Exception:
            logger.exception("Failed to insert packet %s", packet.packet_id)
            packet.metadata["db_insert_failed"] = True
            self._write_failed_packet(packet)
            raise

        self.event_bus.emit(
            "PACKET_SAVED",
            {
                "packet_id": packet.packet_id,
                "transmission_id": packet.transmission_id,
                "talkgroup_id": packet.talkgroup_id,
                "audio_path": packet.audio_path,
            },
        )
        return packet

    def save_audio(self, audio: AudioSegment, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as wav_file:
            wav_file.setnchannels(self.config.channels)
            wav_file.setsampwidth(self.config.sample_width_bytes)
            wav_file.setframerate(self.config.sample_rate_hz)
            wav_file.writeframes(audio.to_bytes())

    def insert_packet(self, packet: TransmissionPacket) -> None:
        self.storage.insert_packet(packet)

    def _build_recording_path(self, transmission_id: str, ended_at: float) -> Path:
        dt = datetime.fromtimestamp(ended_at, tz=UTC)
        root = self.config.recordings_root
        return root / f"{dt:%Y}" / f"{dt:%m}" / f"{dt:%d}" / f"{transmission_id}.wav"

    def _write_failed_packet(self, packet: TransmissionPacket) -> None:
        root = self.config.failed_packets_root
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{packet.packet_id}.json"
        with target.open("w", encoding="utf-8") as f:
            json.dump(asdict(packet), f, indent=2, sort_keys=True)
