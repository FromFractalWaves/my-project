from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import TransmissionPacket


class PacketSink(Protocol):
    def emit(self, packet: TransmissionPacket) -> None: ...


class JsonlPacketSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, packet: TransmissionPacket) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(packet.to_dict(), sort_keys=True) + "\n")


class StdoutPacketSink:
    def emit(self, packet: TransmissionPacket) -> None:
        print(json.dumps(packet.to_dict(), sort_keys=True))
