from __future__ import annotations

import time
from typing import Iterable, Optional

from .models import ActiveCall, CompletedCall, MetadataEvent, PCMHeader
from .settings import AUDIO_RATE, GRANT_EVENT_TYPES, INACTIVITY_TIMEOUT_S, RELEASE_EVENT_TYPES


class BufferManager:
    """Owns active calls keyed by talkgroup and correlated by lane."""

    def __init__(
        self,
        sample_rate: int = AUDIO_RATE,
        inactivity_timeout_s: float = INACTIVITY_TIMEOUT_S,
    ) -> None:
        self.sample_rate = sample_rate
        self.inactivity_timeout_s = inactivity_timeout_s
        self._by_tgid: dict[int, ActiveCall] = {}
        self._tgid_by_lane: dict[int, int] = {}

    def handle_metadata(self, event: MetadataEvent) -> list[CompletedCall]:
        completed: list[CompletedCall] = []
        now = event.ts if event.ts is not None else time.time()

        if event.json_type in GRANT_EVENT_TYPES and event.tgid is not None:
            maybe = self._grant_or_update(event, now)
            if maybe is not None:
                completed.append(maybe)
        elif event.json_type in RELEASE_EVENT_TYPES and event.tgid is not None:
            maybe = self._release_by_tgid(event.tgid, now, event.json_type)
            if maybe is not None:
                completed.append(maybe)
        else:
            if event.tgid is not None and event.tgid in self._by_tgid and event.srcaddr:
                self._by_tgid[event.tgid].source_radio_id = event.srcaddr
        return completed

    def handle_pcm(self, header: PCMHeader, pcm_bytes: bytes) -> None:
        call = self._by_tgid.get(header.tgid)
        if call is None:
            call = ActiveCall(
                tgid=header.tgid,
                lane_id=header.lane_id,
                frequency=header.freq,
                source_radio_id=header.source_radio_id,
                started_at=header.ts,
                last_pcm_at=header.ts,
                sample_rate=self.sample_rate,
            )
            self._by_tgid[header.tgid] = call
            self._tgid_by_lane[header.lane_id] = header.tgid
        else:
            if call.lane_id != header.lane_id and header.lane_id is not None:
                if call.lane_id is not None:
                    self._tgid_by_lane.pop(call.lane_id, None)
                call.lane_id = header.lane_id
                self._tgid_by_lane[header.lane_id] = call.tgid
            if header.freq is not None:
                call.frequency = header.freq
            if header.source_radio_id:
                call.source_radio_id = header.source_radio_id
        call.append_pcm(pcm_bytes, header.ts)

    def flush_timeouts(self, now: Optional[float] = None) -> list[CompletedCall]:
        ts = now if now is not None else time.time()
        expired: list[CompletedCall] = []
        for tgid, call in list(self._by_tgid.items()):
            if ts - call.last_pcm_at > self.inactivity_timeout_s:
                maybe = self._close_call(tgid, ts, "inactivity_timeout")
                if maybe is not None:
                    expired.append(maybe)
        return expired

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "tgid": call.tgid,
                "lane_id": call.lane_id,
                "frequency": call.frequency,
                "source_radio_id": call.source_radio_id,
                "started_at": call.started_at,
                "last_pcm_at": call.last_pcm_at,
                "bytes": sum(len(c) for c in call.chunks),
            }
            for call in self._by_tgid.values()
        ]

    def _grant_or_update(self, event: MetadataEvent, now: float) -> Optional[CompletedCall]:
        assert event.tgid is not None
        # If a lane is already bound to a different TGID, close that call.
        if event.lane_id is not None:
            prior_tgid = self._tgid_by_lane.get(event.lane_id)
            if prior_tgid is not None and prior_tgid != event.tgid:
                closed = self._close_call(prior_tgid, now, "lane_reassigned")
            else:
                closed = None
        else:
            closed = None

        call = self._by_tgid.get(event.tgid)
        if call is None:
            call = ActiveCall(
                tgid=event.tgid,
                lane_id=event.lane_id,
                frequency=event.freq,
                source_radio_id=event.srcaddr,
                started_at=now,
                last_pcm_at=now,
                sample_rate=self.sample_rate,
            )
            self._by_tgid[event.tgid] = call
        else:
            if event.lane_id is not None:
                if call.lane_id is not None and call.lane_id != event.lane_id:
                    self._tgid_by_lane.pop(call.lane_id, None)
                call.lane_id = event.lane_id
            if event.freq is not None:
                call.frequency = event.freq
            if event.srcaddr:
                call.source_radio_id = event.srcaddr

        if event.lane_id is not None:
            self._tgid_by_lane[event.lane_id] = event.tgid
        return closed

    def _release_by_tgid(self, tgid: int, ended_at: float, reason: str) -> Optional[CompletedCall]:
        return self._close_call(tgid, ended_at, reason)

    def _close_call(self, tgid: int, ended_at: float, reason: str) -> Optional[CompletedCall]:
        call = self._by_tgid.pop(tgid, None)
        if call is None:
            return None
        if call.lane_id is not None:
            self._tgid_by_lane.pop(call.lane_id, None)
        return CompletedCall(
            tgid=call.tgid,
            lane_id=call.lane_id,
            frequency=call.frequency,
            source_radio_id=call.source_radio_id,
            started_at=call.started_at,
            ended_at=ended_at,
            sample_rate=call.sample_rate,
            audio_bytes=b"".join(call.chunks),
            end_reason=reason,
        )
