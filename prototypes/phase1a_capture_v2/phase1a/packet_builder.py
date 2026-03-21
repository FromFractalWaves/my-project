from __future__ import annotations

from pathlib import Path

from .models import CompletedCall, TransmissionPacket, isoformat_utc


class PacketBuilder:
    def build(self, call: CompletedCall, audio_path: Path, packet_id: str) -> TransmissionPacket:
        return TransmissionPacket(
            packet_id=packet_id,
            packet_type="transmission",
            timestamp_start=isoformat_utc(call.started_at),
            timestamp_end=isoformat_utc(call.ended_at),
            source={
                "talkgroup_id": call.tgid,
                "source_radio_id": call.source_radio_id,
                "frequency": call.frequency,
            },
            metadata={
                "talkgroup_id": call.tgid,
                "source_radio_id": call.source_radio_id,
                "frequency": call.frequency,
                "system": "p25_phase1",
                "lane_id": call.lane_id,
                "end_reason": call.end_reason,
            },
            payload={
                "audio_path": str(audio_path),
                "duration_seconds": call.duration_seconds,
                "sample_rate": call.sample_rate,
            },
            status="captured",
        )
