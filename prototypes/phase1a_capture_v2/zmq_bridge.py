#!/usr/bin/env python3
"""
Phase 1A — ZMQ bridge

Bridge between the GNU Radio flowgraph and the Python capture backend.

Inputs
------
- one ZMQ PULL socket per voice lane (raw int16 PCM)
- one ZMQ PULL socket for metadata (JSON bytes from flowgraph MetadataPoller)

Outputs
-------
- one ZMQ PUSH socket for backend PCM: multipart [header json, raw pcm]
- one ZMQ PUSH socket for backend control: JSON bytes passthrough

Correlation model
-----------------
- metadata assigns/release TGIDs to lane IDs
- lane_state stores the current owner of each lane
- PCM from lane N is forwarded only when lane_state[N] has an active TGID
"""

from __future__ import annotations

import json
import threading
import time

import zmq

from phase1a.settings import (
    BACKEND_CTRL_ENDPOINT,
    BACKEND_PCM_ENDPOINT,
    GRANT_EVENT_TYPES,
    META_ENDPOINT,
    POLL_TIMEOUT_MS,
    RELEASE_EVENT_TYPES,
    VOICE_LANE_CAP,
    pcm_endpoint,
)


class LaneState:
    def __init__(self) -> None:
        self._state: dict[int, dict] = {}
        self._lock = threading.RLock()

    def update(self, lane_id: int, tgid: int | None, freq: int | None, srcaddr: str | None = None) -> None:
        with self._lock:
            self._state[lane_id] = {"tgid": tgid, "freq": freq, "srcaddr": srcaddr}

    def get(self, lane_id: int) -> dict | None:
        with self._lock:
            return self._state.get(lane_id)

    def apply_meta_event(self, msg: dict) -> None:
        lane_id = msg.get("lane_id")
        if lane_id is None:
            return
        try:
            lane_id = int(lane_id)
        except (TypeError, ValueError):
            return

        jtype = str(msg.get("json_type", ""))
        tgid = _int_or_none(msg.get("tgid"))
        freq = _int_or_none(msg.get("freq"))
        srcaddr = _str_or_none(msg.get("srcaddr"))

        if jtype in GRANT_EVENT_TYPES and tgid is not None:
            self.update(lane_id, tgid, freq, srcaddr)
        elif jtype in RELEASE_EVENT_TYPES:
            self.update(lane_id, None, None, None)
        elif lane_id in self._state and srcaddr:
            state = dict(self._state[lane_id])
            state["srcaddr"] = srcaddr
            self._state[lane_id] = state


class MetadataSubscriber(threading.Thread):
    def __init__(self, endpoint: str, lane_state: LaneState, backend_ctrl_socket: zmq.Socket) -> None:
        super().__init__(daemon=True, name="meta-sub")
        self.endpoint = endpoint
        self.lane_state = lane_state
        self.backend_ctrl_socket = backend_ctrl_socket
        self._stop_event = threading.Event()
        self.socket = zmq.Context.instance().socket(zmq.PULL)
        self.socket.connect(endpoint)
        self.socket.setsockopt(zmq.RCVTIMEO, POLL_TIMEOUT_MS)

    def run(self) -> None:
        print(f"[bridge/meta] <- {self.endpoint}")
        while not self._stop_event.is_set():
            try:
                raw = self.socket.recv()
            except zmq.Again:
                continue
            payload = raw if isinstance(raw, bytes) else raw.encode()
            try:
                decoded = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                decoded = None
            if decoded is not None:
                self.lane_state.apply_meta_event(decoded)
            try:
                self.backend_ctrl_socket.send(payload, zmq.NOBLOCK)
            except zmq.Again:
                pass

    def stop(self) -> None:
        self._stop_event.set()


class PCMLaneSubscriber(threading.Thread):
    def __init__(
        self,
        lane_id: int,
        endpoint: str,
        lane_state: LaneState,
        backend_pcm_socket: zmq.Socket,
        backend_pcm_lock: threading.Lock,
    ) -> None:
        super().__init__(daemon=True, name=f"pcm-lane-{lane_id}")
        self.lane_id = lane_id
        self.endpoint = endpoint
        self.lane_state = lane_state
        self.backend_pcm_socket = backend_pcm_socket
        self.backend_pcm_lock = backend_pcm_lock
        self._stop_event = threading.Event()
        self.socket = zmq.Context.instance().socket(zmq.PULL)
        self.socket.connect(endpoint)
        self.socket.setsockopt(zmq.RCVTIMEO, POLL_TIMEOUT_MS)

    def run(self) -> None:
        print(f"[bridge/pcm{self.lane_id}] <- {self.endpoint}")
        while not self._stop_event.is_set():
            try:
                pcm_bytes = self.socket.recv()
            except zmq.Again:
                continue
            state = self.lane_state.get(self.lane_id)
            if state is None or state.get("tgid") is None:
                continue
            header = {
                "lane_id": self.lane_id,
                "tgid": state.get("tgid"),
                "freq": state.get("freq"),
                "source_radio_id": state.get("srcaddr"),
                "ts": time.time(),
            }
            try:
                with self.backend_pcm_lock:
                    self.backend_pcm_socket.send_multipart(
                        [json.dumps(header).encode(), pcm_bytes],
                        zmq.NOBLOCK,
                    )
            except zmq.Again:
                pass

    def stop(self) -> None:
        self._stop_event.set()


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value):
    if value is None:
        return None
    text = str(value)
    return text if text else None


def main() -> None:
    print("=" * 52)
    print("  Phase 1A — ZMQ Bridge")
    print("=" * 52)
    print(f"  Meta in      : {META_ENDPOINT}")
    print(f"  PCM in       : {pcm_endpoint(0)} ... {pcm_endpoint(VOICE_LANE_CAP - 1)}")
    print(f"  Backend PCM  : {BACKEND_PCM_ENDPOINT}")
    print(f"  Backend CTRL : {BACKEND_CTRL_ENDPOINT}")

    ctx = zmq.Context.instance()
    backend_pcm_socket = ctx.socket(zmq.PUSH)
    backend_pcm_socket.bind(BACKEND_PCM_ENDPOINT)
    backend_ctrl_socket = ctx.socket(zmq.PUSH)
    backend_ctrl_socket.bind(BACKEND_CTRL_ENDPOINT)
    backend_pcm_lock = threading.Lock()

    lane_state = LaneState()
    meta_sub = MetadataSubscriber(META_ENDPOINT, lane_state, backend_ctrl_socket)
    meta_sub.start()

    pcm_subs = []
    for lane_id in range(VOICE_LANE_CAP):
        sub = PCMLaneSubscriber(
            lane_id=lane_id,
            endpoint=pcm_endpoint(lane_id),
            lane_state=lane_state,
            backend_pcm_socket=backend_pcm_socket,
            backend_pcm_lock=backend_pcm_lock,
        )
        sub.start()
        pcm_subs.append(sub)

    print("Bridge running. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(5)
            snapshot = {
                lane_id: lane_state.get(lane_id)
                for lane_id in range(VOICE_LANE_CAP)
                if lane_state.get(lane_id) and lane_state.get(lane_id).get("tgid") is not None
            }
            if snapshot:
                print(f"[bridge] active lanes: {snapshot}")
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping bridge...")
        meta_sub.stop()
        for sub in pcm_subs:
            sub.stop()


if __name__ == "__main__":
    main()
