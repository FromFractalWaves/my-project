from __future__ import annotations

import json
from typing import Iterator

import zmq

from .models import MetadataEvent, PCMChunk


class PCMSubscriber:
    """Skeleton subscriber for PCM lane.

    Assumes the frame payload is raw PCM16 mono and that TGID is provided
    as a topic prefix or out-of-band by the flowgraph. The exact framing still
    needs confirmation against the real GNU Radio wire format.
    """

    def __init__(self, endpoint: str, context: zmq.Context | None = None) -> None:
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.PULL)
        self.socket.connect(endpoint)

    def recv_chunk(self) -> PCMChunk:
        payload = self.socket.recv()
        return PCMChunk(tgid=None, pcm=payload, sample_rate=8000)


class MetadataSubscriber:
    """Skeleton subscriber for metadata lane.

    Assumes a JSON message emitted over a ZMQ message sink. Real PMT-to-wire
    mapping still needs confirmation.
    """

    def __init__(self, endpoint: str, context: zmq.Context | None = None) -> None:
        self.context = context or zmq.Context.instance()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.connect(endpoint)
        self.socket.setsockopt(zmq.SUBSCRIBE, b"")

    def recv_event(self) -> MetadataEvent:
        payload = self.socket.recv()
        data = json.loads(payload.decode("utf-8"))
        return MetadataEvent(
            event_type=data.get("event_type", "unknown"),
            tgid=data.get("tgid"),
            frequency=data.get("frequency"),
            source_radio_id=data.get("source_radio_id"),
            timestamp=data.get("timestamp"),
            raw=data,
        )
