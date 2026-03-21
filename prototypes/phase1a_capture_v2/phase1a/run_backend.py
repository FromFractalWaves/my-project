from __future__ import annotations

from .capture_backend import CaptureBackend
from .packet_emitter import JsonlPacketSink
from .settings import PACKET_JSONL_PATH


def main() -> None:
    sink = JsonlPacketSink(PACKET_JSONL_PATH)
    backend = CaptureBackend(packet_sink=sink)
    backend.run_forever()


if __name__ == "__main__":
    main()
