from __future__ import annotations

import logging
import time
from collections import defaultdict

from .audio_buffer import AudioBuffer
from .config import Op25IntegrationConfig
from .event_bus import EventBus
from .models import AudioChunk, CallState, Op25Signal
from .packet_assembler import PacketAssembler

logger = logging.getLogger(__name__)


class CallTracker:
    """Tracks active calls and converts them into packets when they end."""

    def __init__(
        self,
        config: Op25IntegrationConfig,
        audio_buffer: AudioBuffer,
        packet_assembler: PacketAssembler,
        event_bus: EventBus,
    ) -> None:
        self.config = config
        self.audio_buffer = audio_buffer
        self.packet_assembler = packet_assembler
        self.event_bus = event_bus
        self.active_calls: dict[int, list[CallState]] = defaultdict(list)

    def handle_signal(self, signal: Op25Signal) -> None:
        call = self.resolve_call(signal)
        if call is None:
            self.start_call(signal)
            return
        self.update_call(call, signal)

    def handle_audio_chunk(self, chunk: AudioChunk) -> None:
        """Attach chunk references to all currently active calls.

        In a single-voice-path V1 this is usually sufficient. If your upstream can
        attribute audio to a specific call or channel, replace this fan-out with
        targeted routing.
        """

        for call_list in self.active_calls.values():
            for call in call_list:
                call.audio_segments.append(chunk)

    def resolve_call(self, signal: Op25Signal) -> CallState | None:
        calls = self.active_calls.get(signal.talkgroup_id, [])
        return calls[0] if calls else None

    def start_call(self, signal: Op25Signal) -> CallState:
        now = time.time()
        call = CallState(
            talkgroup_id=signal.talkgroup_id,
            start_time=signal.timestamp or now,
            last_activity_time=now,
            source_ids={signal.source_id} if signal.source_id is not None else set(),
            frequency=signal.frequency,
            encrypted=signal.encrypted,
            metadata={"op25_raw": signal.raw},
        )

        pre_roll = self.audio_buffer.get_last(self.config.pre_roll_seconds)
        call.audio_segments.extend(pre_roll.chunks)
        self.active_calls[signal.talkgroup_id].append(call)

        self.event_bus.emit(
            "CALL_STARTED",
            {
                "transmission_id": call.transmission_id,
                "talkgroup_id": call.talkgroup_id,
                "frequency": call.frequency,
                "encrypted": call.encrypted,
            },
        )
        return call

    def update_call(self, call: CallState, signal: Op25Signal) -> None:
        call.last_activity_time = time.time()
        if signal.source_id is not None:
            call.source_ids.add(signal.source_id)
        if signal.frequency is not None:
            call.frequency = signal.frequency
        call.encrypted = call.encrypted or signal.encrypted
        call.metadata["op25_raw"] = signal.raw

        self.event_bus.emit(
            "CALL_UPDATED",
            {
                "transmission_id": call.transmission_id,
                "talkgroup_id": call.talkgroup_id,
                "frequency": call.frequency,
                "encrypted": call.encrypted,
            },
        )

    def end_call(self, call: CallState) -> None:
        post_roll = self.audio_buffer.get_last(self.config.post_roll_seconds)
        call.audio_segments.extend(post_roll.chunks)

        call_list = self.active_calls.get(call.talkgroup_id, [])
        if call in call_list:
            call_list.remove(call)
        if not call_list and call.talkgroup_id in self.active_calls:
            del self.active_calls[call.talkgroup_id]

        self.event_bus.emit(
            "CALL_ENDED",
            {
                "transmission_id": call.transmission_id,
                "talkgroup_id": call.talkgroup_id,
                "frequency": call.frequency,
                "encrypted": call.encrypted,
            },
        )

        try:
            self.packet_assembler.finalize_call(call)
        except Exception:
            logger.exception("Packet finalization failed for call %s", call.transmission_id)

    def poll_timeouts(self) -> None:
        now = time.time()
        for talkgroup_id, call_list in list(self.active_calls.items()):
            for call in list(call_list):
                if now - call.last_activity_time > self.config.call_end_timeout_seconds:
                    self.end_call(call)
            if not self.active_calls.get(talkgroup_id):
                self.active_calls.pop(talkgroup_id, None)
