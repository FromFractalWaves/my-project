from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class AudioChunk:
    """A single PCM chunk plus its approximate duration."""

    data: bytes
    duration_ms: int


@dataclass(slots=True)
class AudioSegment:
    """A collection of PCM chunks that can be flattened for storage."""

    chunks: list[AudioChunk] = field(default_factory=list)

    def to_bytes(self) -> bytes:
        return b"".join(chunk.data for chunk in self.chunks)

    @property
    def duration_ms(self) -> int:
        return sum(chunk.duration_ms for chunk in self.chunks)

    def extend(self, other: "AudioSegment") -> None:
        self.chunks.extend(other.chunks)


@dataclass(slots=True)
class Op25Signal:
    """Normalized JSON event emitted by OP25."""

    type: str
    timestamp: float
    talkgroup_id: int
    source_id: int | None
    frequency: float | None
    encrypted: bool
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Op25Signal":
        return cls(
            type=str(payload.get("type", "OP25_SIGNAL")),
            timestamp=float(payload["timestamp"]),
            talkgroup_id=int(payload["talkgroup_id"]),
            source_id=(int(payload["source_id"]) if payload.get("source_id") is not None else None),
            frequency=(float(payload["frequency"]) if payload.get("frequency") is not None else None),
            encrypted=bool(payload.get("encrypted", False)),
            raw=dict(payload.get("raw", payload)),
        )


@dataclass(slots=True)
class Packet:
    """Base packet contract for Albatross ingestion outputs."""

    packet_id: str
    packet_type: str
    timestamp: float
    source: dict[str, Any]
    metadata: dict[str, Any]
    payload: dict[str, Any]


@dataclass(slots=True)
class TransmissionPacket(Packet):
    """Radio-specific packet stored after a call is finalized."""

    transmission_id: str
    timestamp_start: float
    timestamp_end: float
    talkgroup_id: int
    source_ids: list[int]
    frequency: float | None
    encrypted: bool
    audio_path: str | None


@dataclass(slots=True)
class CallState:
    """Active transmission lifecycle state maintained by CallTracker."""

    transmission_id: str = field(default_factory=lambda: str(uuid4()))
    talkgroup_id: int = 0
    start_time: float = 0.0
    last_activity_time: float = 0.0
    source_ids: set[int] = field(default_factory=set)
    frequency: float | None = None
    encrypted: bool = False
    audio_segments: list[AudioChunk] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_packet_source(self) -> dict[str, Any]:
        return {
            "talkgroup_id": self.talkgroup_id,
            "source_ids": sorted(self.source_ids),
        }

    def to_debug_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["audio_segments"] = f"{len(self.audio_segments)} chunks"
        data["source_ids"] = sorted(self.source_ids)
        return data
