from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MetadataEvent:
    event_type: str
    tgid: int | None = None
    frequency: int | None = None
    source_radio_id: str | None = None
    timestamp: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PCMChunk:
    tgid: int | None
    pcm: bytes
    sample_rate: int
    timestamp: float | None = None


@dataclass
class ActiveCall:
    packet_id: str
    tgid: int
    source_radio_id: str | None
    frequency: int | None
    timestamp_start: str
    started_at_monotonic: float
    last_pcm_at_monotonic: float
    sample_rate: int | None = None
    pcm_chunks: list[bytes] = field(default_factory=list)


@dataclass
class TransmissionPacket:
    packet_id: str
    packet_type: str
    timestamp_start: str
    timestamp_end: str
    source: dict[str, Any]
    metadata: dict[str, Any]
    payload: dict[str, Any]
    status: str = "captured"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PacketEmission:
    packet: TransmissionPacket
    wav_path: Path



def new_active_call(tgid: int, now_monotonic: float, source_radio_id: str | None = None, frequency: int | None = None) -> ActiveCall:
    return ActiveCall(
        packet_id=str(uuid4()),
        tgid=tgid,
        source_radio_id=source_radio_id,
        frequency=frequency,
        timestamp_start=utc_now_iso(),
        started_at_monotonic=now_monotonic,
        last_pcm_at_monotonic=now_monotonic,
    )
