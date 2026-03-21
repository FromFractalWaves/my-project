from __future__ import annotations

from pathlib import Path

from .models import ActiveCall, PacketEmission, TransmissionPacket, utc_now_iso


class PacketEmitter:
    def build_packet(self, call: ActiveCall, wav_path: Path) -> PacketEmission:
        total_bytes = sum(len(c) for c in call.pcm_chunks)
        sample_rate = call.sample_rate or 8000
        duration_seconds = total_bytes / 2 / sample_rate
        packet = TransmissionPacket(
            packet_id=call.packet_id,
            packet_type="transmission",
            timestamp_start=call.timestamp_start,
            timestamp_end=utc_now_iso(),
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
            },
            payload={
                "audio_path": str(wav_path),
                "duration_seconds": duration_seconds,
                "sample_rate": sample_rate,
            },
        )
        return PacketEmission(packet=packet, wav_path=wav_path)
