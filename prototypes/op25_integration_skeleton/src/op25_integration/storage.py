from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from typing import Protocol

from .models import TransmissionPacket


class StorageBackend(Protocol):
    def insert_packet(self, packet: TransmissionPacket) -> None:
        ...


class InMemoryStorage:
    """Tiny storage implementation useful for tests and demos."""

    def __init__(self) -> None:
        self._packets: list[TransmissionPacket] = []

    def insert_packet(self, packet: TransmissionPacket) -> None:
        self._packets.append(packet)

    def all_packets(self) -> Sequence[TransmissionPacket]:
        return tuple(self._packets)

    def dump_dicts(self) -> list[dict]:
        return [asdict(packet) for packet in self._packets]
