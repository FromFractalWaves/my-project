#!/usr/bin/env python3
"""
Phase 1A — GNU Radio capture flowgraph
P25 Phase 1 trunked radio, multi-lane visible-band capture

Architecture:
- SDR centered between control and voice channels
- One fixed control lane decoding the P25 control channel (offset from center)
- N pooled voice lanes that retune on channel grants
- TSBK parser (extracted from OP25) decodes raw binary control channel messages
- Per-lane PCM egress over ZMQ PUSH
- Parsed metadata forwarded as JSON over ZMQ PUSH
"""

from __future__ import annotations

import ctypes
import json
import math
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import zmq
from gnuradio import analog, blocks, filter, gr, zeromq
from gnuradio import op25, op25_repeater
from gnuradio.filter import firdes
from gnuradio.fft import window
import osmosdr

# OP25 p25_demod_fb hierblock (C4FM matched filter + symbol timing)
sys.path.insert(0, "/home/fractalwaves/clones/op25/op25/gr-op25_repeater/apps")
sys.path.insert(0, "/home/fractalwaves/clones/op25/op25/gr-op25_repeater/apps/tx")
from p25_demodulator import p25_demod_fb

from phase1a.settings import (
    AUDIO_RATE,
    CENTER_FREQ_HZ,
    CHANNEL_DECIM,
    CHANNEL_RATE,
    CONTROL_FREQ_HZ,
    CONTROL_OFFSET_HZ,
    META_ENDPOINT,
    P25_CHANNEL_SPACING,
    RTL_GAIN,
    SOURCE_SAMPLE_RATE,
    SYMBOL_RATE,
    VISIBLE_BW_HZ,
    VISIBLE_MAX_HZ,
    VISIBLE_MIN_HZ,
    VOICE_LANE_CAP,
    pcm_endpoint,
)
from phase1a.tsbk import TSBKParser

TWO_PI = 2.0 * 3.14159265358979323846
P25_SYMBOL_DEVIATION = 600.0

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
        window.WIN_HAMMING,
    )


def voice_offset_hz(target_freq: int) -> int:
    """Offset from SDR center frequency to a voice channel."""
    return target_freq - CENTER_FREQ_HZ


@dataclass
class VoiceLane:
    lane_id: int
    channelizer: object = field(default=None, repr=False)
    fm_demod: object = field(default=None, repr=False)
    demod: object = field(default=None, repr=False)
    frame_assembler: object = field(default=None, repr=False)
    pcm_sink: object = field(default=None, repr=False)
    active: bool = False
    tgid: Optional[int] = None
    freq: Optional[int] = None

    def assign(self, tgid: int, freq: int) -> None:
        self.active = True
        self.tgid = tgid
        self.freq = freq
        self.channelizer.set_center_freq(voice_offset_hz(freq))

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
                print(f"[lane] pool exhausted, dropping tgid={tgid} freq={freq}", flush=True)
                return None
            free.assign(tgid, freq)
            self._tgid_to_lane[tgid] = free
            print(f"[lane] lane {free.lane_id} <= tgid={tgid} freq={freq/1e6:.4f}MHz", flush=True)
            return free.lane_id

    def on_release(self, tgid: int) -> Optional[int]:
        with self._lock:
            lane = self._tgid_to_lane.pop(tgid, None)
            if lane is None:
                return None
            lane_id = lane.lane_id
            lane.release()
            print(f"[lane] lane {lane_id} released from tgid={tgid}", flush=True)
            return lane_id

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {"lane_id": lane.lane_id, "active": lane.active, "tgid": lane.tgid, "freq": lane.freq}
                for lane in self._lanes
            ]


class MetadataPoller(threading.Thread):
    """Drains the gr.msg_queue, decodes TSBKs, and forwards parsed events over ZMQ."""

    def __init__(self, msgq: gr.msg_queue, endpoint: str, lane_manager: LaneManager) -> None:
        super().__init__(daemon=True, name="metadata-poller")
        self.msgq = msgq
        self.endpoint = endpoint
        self.lane_manager = lane_manager
        self.tsbk_parser = TSBKParser()
        self._stop_event = threading.Event()
        self.socket = zmq.Context.instance().socket(zmq.PUSH)
        self.socket.bind(endpoint)

    def run(self) -> None:
        print(f"[meta] poller -> {self.endpoint}", flush=True)
        msg_count = 0
        grant_count = 0
        while not self._stop_event.is_set():
            msg = self.msgq.delete_head_nowait()
            if msg is None:
                time.sleep(0.005)
                continue
            msg_count += 1

            parsed = self.tsbk_parser.process_qmsg(msg)
            if parsed is None:
                continue

            # Annotate with lane assignment for grants
            event = dict(parsed)
            event["ts"] = time.time()

            if event["type"] in ("grant", "grant_update"):
                self._handle_grant(event)
                grant_count += 1
                # Also handle second pair in grant_update
                if event["type"] == "grant_update" and event.get("tgid2") and event.get("frequency2"):
                    event2 = dict(event)
                    event2["tgid"] = event["tgid2"]
                    event2["frequency"] = event["frequency2"]
                    self._handle_grant(event2)

            if event["type"] in ("grant", "grant_update", "iden_up"):
                try:
                    self.socket.send(json.dumps(event).encode(), zmq.NOBLOCK)
                except zmq.Again:
                    pass

            if msg_count % 200 == 0:
                ft = self.tsbk_parser.freq_table
                print(f"[meta] {msg_count} msgs, {grant_count} grants, "
                      f"{len(ft)} iden entries", flush=True)

    def _handle_grant(self, event: dict) -> None:
        tgid = event.get("tgid")
        freq = event.get("frequency")
        if not tgid or not freq:
            return

        if VISIBLE_MIN_HZ <= freq <= VISIBLE_MAX_HZ:
            lane_id = self.lane_manager.on_grant(tgid, freq)
            event["lane_id"] = lane_id
        else:
            event["lane_id"] = None
            event["drop_reason"] = "outside_visible_band"

        srcaddr = event.get("srcaddr")
        freq_mhz = freq / 1e6
        print(f"[meta] {event['type']:14s} tgid={tgid} freq={freq_mhz:.4f}MHz "
              f"lane={event.get('lane_id')} src={srcaddr}", flush=True)

    def stop(self) -> None:
        self._stop_event.set()


class P25CaptureFlowgraph(gr.top_block):
    def __init__(self) -> None:
        super().__init__("P25 Phase 1 Multi-Lane Capture")
        self.msgq = gr.msg_queue(200)
        taps = channel_taps()
        fm_demod_gain = CHANNEL_RATE / (TWO_PI * P25_SYMBOL_DEVIATION)

        # --- SDR source ---
        self.source = osmosdr.source(args="numchan=1 rtl=0")
        self.source.set_sample_rate(SOURCE_SAMPLE_RATE)
        self.source.set_center_freq(CENTER_FREQ_HZ)
        self.source.set_freq_corr(0)
        self.source.set_gain_mode(False)
        self.source.set_gain(RTL_GAIN)
        self.source.set_if_gain(20)
        self.source.set_bb_gain(20)
        self.source.set_bandwidth(0)

        # --- Control lane (offset from center to control channel freq) ---
        self.ctrl_filter = filter.freq_xlating_fir_filter_ccf(
            CHANNEL_DECIM, taps, CONTROL_OFFSET_HZ, SOURCE_SAMPLE_RATE
        )
        self.ctrl_fm = analog.quadrature_demod_cf(fm_demod_gain)
        self.ctrl_demod = p25_demod_fb(input_rate=CHANNEL_RATE)
        self.ctrl_assembler = op25_repeater.p25_frame_assembler(
            OP25_UDP_HOST, OP25_UDP_PORT, OP25_DEBUG,
            True,   # do_imbe
            False,  # do_output
            True,   # do_msgq
            self.msgq,
            False,  # do_audio_output
            False,  # do_phase2_tdma
            False,  # do_nocrypt
        )
        self.connect(self.source, self.ctrl_filter, self.ctrl_fm,
                     self.ctrl_demod, self.ctrl_assembler)

        # --- Voice lanes ---
        self.voice_lanes: list[VoiceLane] = []
        for lane_id in range(VOICE_LANE_CAP):
            chan = filter.freq_xlating_fir_filter_ccf(
                CHANNEL_DECIM, taps, 0, SOURCE_SAMPLE_RATE
            )
            fm = analog.quadrature_demod_cf(fm_demod_gain)
            demod = p25_demod_fb(input_rate=CHANNEL_RATE)
            assembler = op25_repeater.p25_frame_assembler(
                OP25_UDP_HOST, OP25_UDP_PORT, OP25_DEBUG,
                True,   # do_imbe
                True,   # do_output
                False,  # do_msgq
                self.msgq,
                True,   # do_audio_output
                False,  # do_phase2_tdma
                False,  # do_nocrypt
            )
            sink = zeromq.push_sink(
                gr.sizeof_short, 1, pcm_endpoint(lane_id), 100,
                False,  # pass_tags
                100,    # timeout (ms) — drop samples if no subscriber
            )
            self.connect(self.source, chan, fm, demod, assembler, sink)
            self.voice_lanes.append(VoiceLane(
                lane_id=lane_id, channelizer=chan, fm_demod=fm,
                demod=demod, frame_assembler=assembler, pcm_sink=sink,
            ))

        self.lane_manager = LaneManager(self.voice_lanes)
        self.meta_poller = MetadataPoller(self.msgq, META_ENDPOINT, self.lane_manager)


def main() -> None:
    print("=" * 58)
    print("  Phase 1A — P25 Multi-Lane Capture Flowgraph")
    print("=" * 58)
    print(f"  Center freq      : {CENTER_FREQ_HZ / 1e6:.4f} MHz")
    print(f"  Control channel  : {CONTROL_FREQ_HZ / 1e6:.4f} MHz (offset {CONTROL_OFFSET_HZ/1e6:+.4f} MHz)")
    print(f"  Source rate      : {SOURCE_SAMPLE_RATE / 1e6:.3f} MHz")
    print(f"  Channel rate     : {CHANNEL_RATE / 1e3:.0f} kHz (decim {CHANNEL_DECIM})")
    print(f"  Visible band     : {VISIBLE_MIN_HZ/1e6:.3f} – {VISIBLE_MAX_HZ/1e6:.3f} MHz")
    print(f"  Voice lane cap   : {VOICE_LANE_CAP}")
    print(f"  Meta endpoint    : {META_ENDPOINT}")
    print(f"  PCM endpoints    : {pcm_endpoint(0)} ... {pcm_endpoint(VOICE_LANE_CAP - 1)}")

    tb = P25CaptureFlowgraph()
    tb.meta_poller.start()

    def handle_sigint(sig, frame):
        print("\nStopping...", flush=True)
        tb.meta_poller.stop()
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)
    print("Starting flowgraph...", flush=True)
    tb.start()
    print("Flowgraph running. Ctrl+C to stop.", flush=True)

    try:
        while True:
            time.sleep(5)
            active = [lane for lane in tb.lane_manager.snapshot() if lane["active"]]
            print(f"[lanes] {len(active)}/{VOICE_LANE_CAP} active: {active}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        tb.meta_poller.stop()
        tb.stop()
        tb.wait()


if __name__ == "__main__":
    main()
