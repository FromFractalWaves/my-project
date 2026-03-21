#!/usr/bin/env python3
"""
Phase 1A — GNU Radio capture flowgraph
P25 Phase 1 trunked radio, multi-lane visible-band capture

Skeleton aligned to the current design:
- one fixed control lane that emits metadata into a gr.msg_queue
- N pooled voice lanes that retune on grants inside the visible band
- per-lane PCM egress over ZMQ PUSH
- metadata queue drained by a Python thread and forwarded as JSON bytes over ZMQ PUSH

Important:
- This file is a scaffold and has not been validated against live RF in the test suite.
- The metadata poller injects `lane_id` into forwarded grant/release events so the bridge can correlate PCM to TGIDs.
"""

from __future__ import annotations

import json
import math
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import zmq
from gnuradio import blocks, filter, gr, zeromq
from gnuradio import op25, op25_repeater
from gnuradio.filter import firdes
import osmosdr

from phase1a.settings import (
    AUDIO_RATE,
    CHANNEL_DECIM,
    CHANNEL_RATE,
    CONTROL_FREQ_HZ,
    GRANT_EVENT_TYPES,
    META_ENDPOINT,
    P25_CHANNEL_SPACING,
    POLL_TIMEOUT_MS,
    RELEASE_EVENT_TYPES,
    RTL_GAIN,
    SOURCE_SAMPLE_RATE,
    SYMBOL_RATE,
    VOICE_LANE_CAP,
    VISIBLE_BW_HZ,
    pcm_endpoint,
)

OP25_UDP_HOST = "127.0.0.1"
OP25_UDP_PORT = 0
OP25_DEBUG = 0
THEORETICAL_MAX_LANES = math.floor(VISIBLE_BW_HZ / P25_CHANNEL_SPACING)


def channel_taps() -> list:
    return firdes.low_pass(
        1.0,
        SOURCE_SAMPLE_RATE,
        6_250,
        1_500,
        firdes.WIN_HAMMING,
    )


def freq_offset_hz(target_freq: int) -> int:
    return target_freq - CONTROL_FREQ_HZ


@dataclass
class VoiceLane:
    lane_id: int
    channelizer: object = field(default=None, repr=False)
    demod: object = field(default=None, repr=False)
    frame_assembler: object = field(default=None, repr=False)
    f2s: object = field(default=None, repr=False)
    pcm_sink: object = field(default=None, repr=False)
    active: bool = False
    tgid: Optional[int] = None
    freq: Optional[int] = None

    def assign(self, tgid: int, freq: int) -> None:
        self.active = True
        self.tgid = tgid
        self.freq = freq
        self.channelizer.set_center_freq(freq_offset_hz(freq))

    def release(self) -> None:
        self.active = False
        self.tgid = None
        self.freq = None


class LaneManager:
    def __init__(self, lanes: list[VoiceLane]) -> None:
        self._lanes = lanes
        self._tgid_to_lane: dict[int, VoiceLane] = {}
        self._lock = threading.Lock()

    def on_grant(self, tgid: int, freq: int) -> Optional[int]:
        with self._lock:
            if tgid in self._tgid_to_lane:
                lane = self._tgid_to_lane[tgid]
                lane.assign(tgid, freq)
                return lane.lane_id
            free = next((l for l in self._lanes if not l.active), None)
            if free is None:
                print(f"[lane] pool exhausted, dropping tgid={tgid} freq={freq}")
                return None
            free.assign(tgid, freq)
            self._tgid_to_lane[tgid] = free
            print(f"[lane] lane {free.lane_id} <= tgid={tgid} freq={freq}")
            return free.lane_id

    def on_release(self, tgid: int) -> Optional[int]:
        with self._lock:
            lane = self._tgid_to_lane.pop(tgid, None)
            if lane is None:
                return None
            lane_id = lane.lane_id
            lane.release()
            print(f"[lane] lane {lane_id} released from tgid={tgid}")
            return lane_id

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {"lane_id": lane.lane_id, "active": lane.active, "tgid": lane.tgid, "freq": lane.freq}
                for lane in self._lanes
            ]


class MetadataPoller(threading.Thread):
    def __init__(self, msgq: gr.msg_queue, endpoint: str, lane_manager: LaneManager) -> None:
        super().__init__(daemon=True, name="metadata-poller")
        self.msgq = msgq
        self.endpoint = endpoint
        self.lane_manager = lane_manager
        self._stop_event = threading.Event()
        self.socket = zmq.Context.instance().socket(zmq.PUSH)
        self.socket.bind(endpoint)

    def run(self) -> None:
        print(f"[meta] poller -> {self.endpoint}")
        while not self._stop_event.is_set():
            msg = self.msgq.delete_head_nowait()
            if msg is None:
                time.sleep(0.005)
                continue
            raw = msg.to_string()
            payload = raw if isinstance(raw, bytes) else raw.encode()
            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                try:
                    self.socket.send(payload, zmq.NOBLOCK)
                except zmq.Again:
                    pass
                continue

            parsed = self._annotate(parsed)
            try:
                self.socket.send(json.dumps(parsed).encode(), zmq.NOBLOCK)
            except zmq.Again:
                pass

    def stop(self) -> None:
        self._stop_event.set()

    def _annotate(self, msg: dict) -> dict:
        event = dict(msg)
        jtype = str(event.get("json_type", ""))
        tgid = _int_or_none(event.get("tgid"))
        freq = _int_or_none(event.get("freq"))
        event["ts"] = time.time()

        if jtype in GRANT_EVENT_TYPES and tgid and freq:
            visible_min = CONTROL_FREQ_HZ - VISIBLE_BW_HZ // 2
            visible_max = CONTROL_FREQ_HZ + VISIBLE_BW_HZ // 2
            if visible_min <= freq <= visible_max:
                lane_id = self.lane_manager.on_grant(tgid, freq)
                event["lane_id"] = lane_id
            else:
                event["lane_id"] = None
                event["drop_reason"] = "outside_visible_band"
        elif jtype in RELEASE_EVENT_TYPES and tgid:
            event["lane_id"] = self.lane_manager.on_release(tgid)

        print(
            f"[meta] {jtype:16s} tgid={event.get('tgid')} freq={event.get('freq')} "
            f"lane={event.get('lane_id')} src={event.get('srcaddr')}"
        )
        return event


class P25CaptureFlowgraph(gr.top_block):
    def __init__(self) -> None:
        super().__init__("P25 Phase 1 Multi-Lane Capture")
        self.msgq = gr.msg_queue(200)
        taps = channel_taps()

        self.source = osmosdr.source(args="numchan=1 rtl=0")
        self.source.set_sample_rate(SOURCE_SAMPLE_RATE)
        self.source.set_center_freq(CONTROL_FREQ_HZ)
        self.source.set_freq_corr(0)
        self.source.set_gain_mode(False)
        self.source.set_gain(RTL_GAIN)
        self.source.set_if_gain(20)
        self.source.set_bb_gain(20)
        self.source.set_bandwidth(0)

        self.ctrl_filter = filter.freq_xlating_fir_filter_ccf(CHANNEL_DECIM, taps, 0, SOURCE_SAMPLE_RATE)
        self.ctrl_demod = op25.fsk4_demod_ff(self.msgq, CHANNEL_RATE, SYMBOL_RATE)
        self.ctrl_assembler = op25_repeater.p25_frame_assembler(
            OP25_UDP_HOST,
            OP25_UDP_PORT,
            OP25_DEBUG,
            True,
            False,
            True,
            self.msgq,
            False,
            False,
            False,
        )
        self.connect(self.source, self.ctrl_filter)
        self.connect(self.ctrl_filter, self.ctrl_demod)
        self.connect(self.ctrl_demod, self.ctrl_assembler)

        self.voice_lanes: list[VoiceLane] = []
        for lane_id in range(VOICE_LANE_CAP):
            chan = filter.freq_xlating_fir_filter_ccf(CHANNEL_DECIM, taps, 0, SOURCE_SAMPLE_RATE)
            demod = op25.fsk4_demod_ff(self.msgq, CHANNEL_RATE, SYMBOL_RATE)
            assembler = op25_repeater.p25_frame_assembler(
                OP25_UDP_HOST,
                OP25_UDP_PORT,
                OP25_DEBUG,
                True,
                True,
                False,
                self.msgq,
                True,
                False,
                False,
            )
            f2s = blocks.float_to_short(1, 32767.0)
            sink = zeromq.push_sink(gr.sizeof_short, 1, pcm_endpoint(lane_id), 100, False, -1)
            self.connect(self.source, chan)
            self.connect(chan, demod)
            self.connect(demod, assembler)
            self.connect(assembler, f2s)
            self.connect(f2s, sink)
            self.voice_lanes.append(
                VoiceLane(
                    lane_id=lane_id,
                    channelizer=chan,
                    demod=demod,
                    frame_assembler=assembler,
                    f2s=f2s,
                    pcm_sink=sink,
                )
            )

        self.lane_manager = LaneManager(self.voice_lanes)
        self.meta_poller = MetadataPoller(self.msgq, META_ENDPOINT, self.lane_manager)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    print("=" * 58)
    print("  Phase 1A — P25 Multi-Lane Capture Flowgraph")
    print("=" * 58)
    print(f"  Control channel  : {CONTROL_FREQ_HZ / 1e6:.4f} MHz")
    print(f"  Source rate      : {SOURCE_SAMPLE_RATE / 1e6:.3f} MHz")
    print(f"  Channel rate     : {CHANNEL_RATE / 1e3:.0f} kHz (decim {CHANNEL_DECIM})")
    print(f"  Symbol rate      : {SYMBOL_RATE} sym/s")
    print(f"  Visible band     : ±{VISIBLE_BW_HZ / 2 / 1e6:.3f} MHz (~{THEORETICAL_MAX_LANES} RF slots)")
    print(f"  Voice lane cap   : {VOICE_LANE_CAP}")
    print(f"  Meta endpoint    : {META_ENDPOINT}")
    print(f"  PCM endpoints    : {pcm_endpoint(0)} ... {pcm_endpoint(VOICE_LANE_CAP - 1)}")

    tb = P25CaptureFlowgraph()
    tb.meta_poller.start()

    def handle_sigint(sig, frame):
        print("\nStopping...")
        tb.meta_poller.stop()
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)
    tb.start()
    print("Flowgraph running. Ctrl+C to stop.")

    try:
        while True:
            time.sleep(5)
            active = [lane for lane in tb.lane_manager.snapshot() if lane["active"]]
            print(f"[lanes] {len(active)}/{VOICE_LANE_CAP} active: {active}")
    except KeyboardInterrupt:
        pass
    finally:
        tb.meta_poller.stop()
        tb.stop()
        tb.wait()


if __name__ == "__main__":
    main()
