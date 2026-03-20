from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .audio_buffer import AudioBuffer
from .call_tracker import CallTracker
from .config import Op25IntegrationConfig
from .event_bus import EventBus
from .json_listener import JSONListener
from .models import AudioChunk
from .packet_assembler import PacketAssembler
from .storage import InMemoryStorage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def build_demo_system(root: Path) -> tuple[JSONListener, AudioBuffer, CallTracker, InMemoryStorage]:
    config = Op25IntegrationConfig(
        recordings_root=root / "recordings",
        failed_packets_root=root / "failed_packets",
    )
    event_bus = EventBus()
    storage = InMemoryStorage()
    audio_buffer = AudioBuffer(max_duration_seconds=config.audio_buffer_seconds)
    packet_assembler = PacketAssembler(config=config, storage=storage, event_bus=event_bus)
    call_tracker = CallTracker(
        config=config,
        audio_buffer=audio_buffer,
        packet_assembler=packet_assembler,
        event_bus=event_bus,
    )
    listener = JSONListener(call_tracker=call_tracker)

    for event_type in ("CALL_STARTED", "CALL_UPDATED", "CALL_ENDED", "PACKET_SAVED"):
        event_bus.subscribe(event_type, lambda data, et=event_type: logger.info("%s %s", et, data))

    return listener, audio_buffer, call_tracker, storage


def demo() -> None:
    root = Path("./demo_output")
    listener, audio_buffer, call_tracker, storage = build_demo_system(root)

    # Simulate a short amount of rolling audio before the call starts.
    for _ in range(3):
        pre_chunk = AudioChunk(data=b"\x00\x00" * 800, duration_ms=100)
        audio_buffer.append(pre_chunk)

    signal = {
        "timestamp": time.time(),
        "talkgroup_id": 6121,
        "source_id": 10042,
        "frequency": 854.6125e6,
        "encrypted": False,
    }
    listener.handle_datagram(json.dumps(signal).encode("utf-8"))

    for _ in range(5):
        chunk = AudioChunk(data=b"\x01\x01" * 800, duration_ms=100)
        audio_buffer.append(chunk)
        call_tracker.handle_audio_chunk(chunk)
        time.sleep(0.05)
        listener.handle_datagram(json.dumps(signal).encode("utf-8"))

    logger.info("Waiting for timeout...")
    time.sleep(1.5)
    call_tracker.poll_timeouts()

    logger.info("Stored packets: %s", len(storage.all_packets()))
    for packet in storage.all_packets():
        logger.info("Packet %s -> %s", packet.packet_id, packet.audio_path)


if __name__ == "__main__":
    demo()
