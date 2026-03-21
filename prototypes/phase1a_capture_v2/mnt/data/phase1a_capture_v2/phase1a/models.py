from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def isoformat_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class MetadataEvent:
    json_type: str
    tgid: Optional[int] = None
    freq: Optional[int] = None
    lane_id: Optional[int] = None
    srcaddr: Optional[str] = None
    ts: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_bytes(cls, payload: bytes) -> "MetadataEvent":
        import json
        decoded = json.loads(payload)
        return cls(
            json_type=str(decoded.get("json_type", "")),
            tgid=_to_int_or_none(decoded.get("tgid")),
            freq=_to_int_or_none(decoded.get("freq")),
            lane_id=_to_int_or_none(decoded.get("lane_id")),
            srcaddr=_to_str_or_none(decoded.get("srcaddr")),
            ts=_to_float_or_none(decoded.get("ts")),
            raw=dict(decoded),
        )


@dataclass
class PCMHeader:
    lane_id: int
    tgid: int
    freq: Optional[int]
    ts: float
    source_radio_id: Optional[str] = None

    @classmethod
    def from_bytes(cls, payload: bytes) -> "PCMHeader":
        import json
        decoded = json.loads(payload)
        return cls(
            lane_id=int(decoded["lane_id"]),
            tgid=int(decoded["tgid"]),
            freq=_to_int_or_none(decoded.get("freq")),
            ts=float(decoded["ts"]),
            source_radio_id=_to_str_or_none(decoded.get("source_radio_id")),
        )


@dataclass
class ActiveCall:
    tgid: int
    lane_id: Optional[int]
    frequency: Optional[int]
    source_radio_id: Optional[str]
    started_at: float
    last_pcm_at: float
    sample_rate: int
    chunks: list[bytes] = field(default_factory=list)

    def append_pcm(self, chunk: bytes, ts: float) -> None:
        self.chunks.append(chunk)
        self.last_pcm_at = ts


@dataclass
class CompletedCall:
    tgid: int
    lane_id: Optional[int]
    frequency: Optional[int]
    source_radio_id: Optional[str]
    started_at: float
    ended_at: float
    sample_rate: int
    audio_bytes: bytes
    end_reason: str

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)


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
class IngestResult:
    status: str
    error_type: Optional[str] = None
    packet_id: Optional[str] = None


@dataclass
class PacketArtifact:
    packet: TransmissionPacket
    audio_path: Path


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None
