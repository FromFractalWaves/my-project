#!/usr/bin/env python3
"""
Phase 1A — ZMQ bridge

Sits between the GNU Radio flowgraph and the Python capture backend.

Inputs
------
  - One ZMQ PULL socket per voice lane (raw int16 PCM)
  - One ZMQ PULL socket for metadata (JSON from MetadataPoller)

Output
------
  - One ZMQ PUSH socket to capture backend
    Multipart frames:
      part 1: JSON  {"lane_id": 0, "tgid": 41003, "freq": 854612500, "ts": 1234567890.123}
      part 2: bytes  raw int16 PCM

Correlation model
-----------------
  The metadata lane tells the bridge which TGID currently owns each lane.
  The bridge maintains a lane_state dict keyed by lane_id.
  When PCM arrives on lane N, the bridge looks up lane_state[N] for the
  current TGID/freq and prepends a JSON header before forwarding.

  If a lane has no current TGID assignment (not yet granted), PCM from
  that lane is dropped — it's noise from an idle/untuned lane.

Usage
-----
  python3 zmq_bridge.py

  Runs independently of the flowgraph. Start after the flowgraph is up.
"""

from __future__ import annotations

import json
import threading
import time

import zmq


# ---------------------------------------------------------------------------
# Configuration — must match flowgraph.py
# ---------------------------------------------------------------------------

META_ENDPOINT       = "tcp://127.0.0.1:5557"
PCM_BASE_PORT       = 5560
VOICE_LANE_CAP      = 8                 # must match flowgraph VOICE_LANE_CAP

BACKEND_ENDPOINT    = "tcp://127.0.0.1:5580"   # capture backend pulls from here

POLL_TIMEOUT_MS     = 10


# ---------------------------------------------------------------------------
# Lane state
# Keyed by lane_id. Updated by metadata thread, read by PCM threads.
# ---------------------------------------------------------------------------

class LaneState:
    def __init__(self) -> None:
        self._state: dict[int, dict] = {}
        self._lock = threading.RLock()

    def update(self, lane_id: int, tgid: int | None, freq: int | None) -> None:
        with self._lock:
            self._state[lane_id] = {"tgid": tgid, "freq": freq}

    def get(self, lane_id: int) -> dict | None:
        with self._lock:
            return self._state.get(lane_id)

    def apply_meta_event(self, msg: dict) -> None:
        """Update lane ownership from a metadata event."""
        jtype = msg.get("json_type", "")
        tgid  = msg.get("tgid")
        freq  = msg.get("freq")
        lane_id = msg.get("lane_id")   # flowgraph should emit this on grants

        if lane_id is None:
            return  # can't correlate without lane_id in the metadata event

        if jtype in ("channel_grant", "update") and tgid and freq:
            self.update(lane_id, tgid, freq)
        elif jtype in ("call_end", "release") and lane_id is not None:
            self.update(lane_id, None, None)


# ---------------------------------------------------------------------------
# Metadata subscriber thread
# ---------------------------------------------------------------------------

class MetadataSubscriber(threading.Thread):
    def __init__(self, endpoint: str, lane_state: LaneState) -> None:
        super().__init__(daemon=True, name="meta-sub")
        self.endpoint = endpoint
        self.lane_state = lane_state
        self._stop = threading.Event()

        ctx = zmq.Context.instance()
        self.socket = ctx.socket(zmq.PULL)
        self.socket.connect(endpoint)
        self.socket.setsockopt(zmq.RCVTIMEO, POLL_TIMEOUT_MS)

    def run(self) -> None:
        print(f"[bridge/meta] ← {self.endpoint}")
        while not self._stop.is_set():
            try:
                raw = self.socket.recv()
                payload = raw if isinstance(raw, bytes) else raw.encode()
                try:
                    msg = json.loads(payload)
                    self.lane_state.apply_meta_event(msg)
                except (json.JSONDecodeError, TypeError):
                    pass
            except zmq.Again:
                continue

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# PCM subscriber thread — one per lane
# ---------------------------------------------------------------------------

class PCMLaneSubscriber(threading.Thread):
    def __init__(
        self,
        lane_id: int,
        endpoint: str,
        lane_state: LaneState,
        out_socket: zmq.Socket,
        out_lock: threading.Lock,
    ) -> None:
        super().__init__(daemon=True, name=f"pcm-lane-{lane_id}")
        self.lane_id = lane_id
        self.endpoint = endpoint
        self.lane_state = lane_state
        self.out_socket = out_socket
        self.out_lock = out_lock
        self._stop = threading.Event()

        ctx = zmq.Context.instance()
        self.socket = ctx.socket(zmq.PULL)
        self.socket.connect(endpoint)
        self.socket.setsockopt(zmq.RCVTIMEO, POLL_TIMEOUT_MS)

    def run(self) -> None:
        print(f"[bridge/pcm{self.lane_id}] ← {self.endpoint}")
        while not self._stop.is_set():
            try:
                pcm_bytes = self.socket.recv()
            except zmq.Again:
                continue

            state = self.lane_state.get(self.lane_id)
            if state is None or state.get("tgid") is None:
                # Lane not currently assigned — drop
                continue

            header = json.dumps({
                "lane_id": self.lane_id,
                "tgid":    state["tgid"],
                "freq":    state["freq"],
                "ts":      time.time(),
            }).encode()

            try:
                with self.out_lock:
                    self.out_socket.send_multipart(
                        [header, pcm_bytes], zmq.NOBLOCK
                    )
            except zmq.Again:
                pass  # backend not connected yet — drop frame

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 52)
    print("  Phase 1A — ZMQ Bridge")
    print("=" * 52)
    print(f"  Meta in     : {META_ENDPOINT}")
    print(f"  PCM in      : ports {PCM_BASE_PORT}–{PCM_BASE_PORT + VOICE_LANE_CAP - 1}")
    print(f"  Backend out : {BACKEND_ENDPOINT}")
    print()

    ctx = zmq.Context.instance()

    # Output socket to capture backend
    out_socket = ctx.socket(zmq.PUSH)
    out_socket.bind(BACKEND_ENDPOINT)
    out_lock = threading.Lock()

    lane_state = LaneState()

    # Start metadata subscriber
    meta_sub = MetadataSubscriber(META_ENDPOINT, lane_state)
    meta_sub.start()

    # Start one PCM subscriber per lane
    pcm_subs = []
    for i in range(VOICE_LANE_CAP):
        sub = PCMLaneSubscriber(
            lane_id=i,
            endpoint=f"tcp://127.0.0.1:{PCM_BASE_PORT + i}",
            lane_state=lane_state,
            out_socket=out_socket,
            out_lock=out_lock,
        )
        sub.start()
        pcm_subs.append(sub)

    print("Bridge running. Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(5)
            snapshot = {
                lid: s for lid, s in
                ((i, lane_state.get(i)) for i in range(VOICE_LANE_CAP))
                if s and s.get("tgid")
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