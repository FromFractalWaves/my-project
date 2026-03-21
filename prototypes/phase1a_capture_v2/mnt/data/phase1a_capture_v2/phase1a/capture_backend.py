from __future__ import annotations

import signal
import time
import uuid
from typing import Iterable

import zmq

from .buffer_manager import BufferManager
from .models import CompletedCall, MetadataEvent, PCMHeader
from .packet_builder import PacketBuilder
from .packet_emitter import PacketSink
from .settings import (
    BACKEND_CTRL_ENDPOINT,
    BACKEND_PCM_ENDPOINT,
    INACTIVITY_TIMEOUT_S,
    POLL_TIMEOUT_MS,
    WAV_OUTPUT_DIR,
)
from .wav_writer import WavWriter


class CaptureBackend:
    def __init__(self, packet_sink: PacketSink) -> None:
        self.packet_sink = packet_sink
        self.buffer_manager = BufferManager(inactivity_timeout_s=INACTIVITY_TIMEOUT_S)
        self.wav_writer = WavWriter(WAV_OUTPUT_DIR)
        self.packet_builder = PacketBuilder()
        self._stop = False

        self.ctx = zmq.Context.instance()
        self.pcm_socket = self.ctx.socket(zmq.PULL)
        self.pcm_socket.connect(BACKEND_PCM_ENDPOINT)
        self.ctrl_socket = self.ctx.socket(zmq.PULL)
        self.ctrl_socket.connect(BACKEND_CTRL_ENDPOINT)

        self.poller = zmq.Poller()
        self.poller.register(self.pcm_socket, zmq.POLLIN)
        self.poller.register(self.ctrl_socket, zmq.POLLIN)

    def run_forever(self) -> None:
        self._install_signal_handlers()
        print(f"[backend] PCM  <- {BACKEND_PCM_ENDPOINT}")
        print(f"[backend] CTRL <- {BACKEND_CTRL_ENDPOINT}")
        print("[backend] running... Ctrl+C to stop")

        last_housekeeping = time.time()
        while not self._stop:
            events = dict(self.poller.poll(POLL_TIMEOUT_MS))

            if self.ctrl_socket in events:
                payload = self.ctrl_socket.recv()
                self._on_control(payload)

            if self.pcm_socket in events:
                parts = self.pcm_socket.recv_multipart()
                if len(parts) == 2:
                    self._on_pcm(parts[0], parts[1])

            now = time.time()
            if now - last_housekeeping >= 0.5:
                self._finalize_calls(self.buffer_manager.flush_timeouts(now))
                last_housekeeping = now

        # Final drain on shutdown.
        self._finalize_calls(self.buffer_manager.flush_timeouts(time.time() + 3600))

    def stop(self) -> None:
        self._stop = True

    def _install_signal_handlers(self) -> None:
        def handler(sig, frame):
            self.stop()
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _on_control(self, payload: bytes) -> None:
        try:
            event = MetadataEvent.from_bytes(payload)
        except Exception as exc:
            print(f"[backend] bad control payload: {exc}")
            return
        completed = self.buffer_manager.handle_metadata(event)
        self._finalize_calls(completed)

    def _on_pcm(self, header_bytes: bytes, pcm_bytes: bytes) -> None:
        try:
            header = PCMHeader.from_bytes(header_bytes)
        except Exception as exc:
            print(f"[backend] bad PCM header: {exc}")
            return
        self.buffer_manager.handle_pcm(header, pcm_bytes)

    def _finalize_calls(self, completed: Iterable[CompletedCall]) -> None:
        for call in completed:
            if not call.audio_bytes:
                # Skip empty artifacts for now; keep policy simple in the skeleton.
                continue
            packet_id = str(uuid.uuid4())
            audio_path = self.wav_writer.write(call, packet_id)
            packet = self.packet_builder.build(call, audio_path, packet_id)
            self.packet_sink.emit(packet)
            print(
                f"[backend] saved packet {packet.packet_id} "
                f"tgid={call.tgid} lane={call.lane_id} dur={call.duration_seconds:.2f}s"
            )
