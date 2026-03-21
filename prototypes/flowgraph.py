#!/usr/bin/env python3
"""
Phase 1A — GNU Radio capture flowgraph
P25 Phase 1 trunked radio, multi-lane visible-band capture

Architecture
------------
One control lane (fixed) drives the metadata queue.
N voice lanes (pooled, configurable cap) are allocated on channel grants
within the visible band and returned on call end or inactivity.

Transport lanes
---------------
  Metadata : gr.msg_queue → MetadataPoller thread → ZMQ PUSH tcp://127.0.0.1:5557
  PCM      : one ZMQ PUSH socket per voice lane    → tcp://127.0.0.1:556{0..N}
             raw int16 PCM, no TGID header in GNU Radio
             TGID/lane correlation is carried by the metadata lane

ZMQ bridge (zmq_bridge.py, separate process)
---------------
  Subscribes to all per-lane PCM sockets + metadata socket.
  Re-emits multipart frames to the capture backend:
    part 1: JSON  {"tgid": ..., "freq": ..., "lane_id": ..., "ts": ...}
    part 2: bytes  raw PCM

Sample rate math
----------------
  Source rate    : 2_048_000 sps
  Decimation     : 32
  Channel rate   : 64_000 sps   (2_048_000 / 32, exact)
  Symbol rate    : 4_800 sym/s  (P25 Phase 1)
  Samples/symbol : 64_000 / 4_800 = 13.333...
  Audio out      : 8_000 sps

  Note: 10 samples/symbol (exact) would require 48_000 channel rate
  at 1_920_000 source rate. 64 kHz is the cleanest option at 2.048 Msps
  and is passed truthfully to the demodulator.

Block signatures (confirmed against installed build)
----------------------------------------------------
  op25.fsk4_demod_ff(queue, sample_rate_Hz, symbol_rate_Hz, bfsk=False)
  op25_repeater.p25_frame_assembler(udp_host, port, debug, do_imbe,
      do_output, do_msgq, queue, do_audio_output, do_phase2_tdma, do_nocrypt)
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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTROL_FREQ_HZ     = 854_612_500       # P25 Phase 1 control channel
SOURCE_SAMPLE_RATE  = 2_048_000         # RTL-SDR sample rate
CHANNEL_DECIM       = 32                # decimation factor
CHANNEL_RATE        = SOURCE_SAMPLE_RATE // CHANNEL_DECIM   # 64_000 sps (exact)
SYMBOL_RATE         = 4_800             # P25 Phase 1 symbol rate
AUDIO_RATE          = 8_000             # decoded voice output rate

P25_CHANNEL_SPACING = 12_500            # Hz between P25 voice channels

RTL_GAIN            = 30               # dB — tune for your environment

# Visible band = full RTL-SDR capture bandwidth centered on control channel
VISIBLE_BW_HZ       = SOURCE_SAMPLE_RATE   # 2.048 MHz
THEORETICAL_MAX_LANES = math.floor(VISIBLE_BW_HZ / P25_CHANNEL_SPACING)  # ~163

# Configurable runtime cap — start low, increase as you validate
VOICE_LANE_CAP      = 8

# ZMQ endpoints
META_ENDPOINT       = "tcp://127.0.0.1:5557"
PCM_BASE_PORT       = 5560              # lane 0 = 5560, lane 1 = 5561, ...

# op25 internal UDP (set port=0 to disable; we use msg_queue)
OP25_UDP_HOST       = "127.0.0.1"
OP25_UDP_PORT       = 0
OP25_DEBUG          = 0                # raise to 10 for verbose op25 logging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pcm_endpoint(lane_id: int) -> str:
    return f"tcp://127.0.0.1:{PCM_BASE_PORT + lane_id}"


def channel_taps() -> list:
    """Low-pass taps for P25 channel filter."""
    return firdes.low_pass(
        1.0,
        SOURCE_SAMPLE_RATE,
        6_250,      # cutoff — half of 12.5 kHz channel spacing
        1_500,      # transition width
        firdes.WIN_HAMMING,
    )


def freq_offset_hz(target_freq: int) -> int:
    """Offset from SDR center frequency to target channel."""
    return target_freq - CONTROL_FREQ_HZ


# ---------------------------------------------------------------------------
# Voice lane
# A single pooled decoder: channelizer → demod → frame assembler → PCM sink
# ---------------------------------------------------------------------------

@dataclass
class VoiceLane:
    lane_id: int
    # GNU Radio blocks
    channelizer: object = field(default=None, repr=False)
    demod: object = field(default=None, repr=False)
    frame_assembler: object = field(default=None, repr=False)
    f2s: object = field(default=None, repr=False)
    pcm_sink: object = field(default=None, repr=False)
    # Current grant state
    active: bool = False
    tgid: Optional[int] = None
    freq: Optional[int] = None

    def assign(self, tgid: int, freq: int) -> None:
        self.active = True
        self.tgid = tgid
        self.freq = freq
        # Retune channelizer to the granted frequency
        offset = freq_offset_hz(freq)
        self.channelizer.set_center_freq(offset)

    def release(self) -> None:
        self.active = False
        self.tgid = None
        self.freq = None


# ---------------------------------------------------------------------------
# Metadata queue poller
# Drains gr.msg_queue, forwards JSON over ZMQ PUSH.
# Also drives lane allocation by parsing channel grant messages.
# ---------------------------------------------------------------------------

class MetadataPoller(threading.Thread):
    def __init__(
        self,
        msgq: gr.msg_queue,
        endpoint: str,
        lane_manager: "LaneManager",
    ) -> None:
        super().__init__(daemon=True, name="metadata-poller")
        self.msgq = msgq
        self.endpoint = endpoint
        self.lane_manager = lane_manager
        self._stop = threading.Event()

        ctx = zmq.Context.instance()
        self.socket = ctx.socket(zmq.PUSH)
        self.socket.bind(endpoint)

    def run(self) -> None:
        print(f"[meta] poller → {self.endpoint}")
        while not self._stop.is_set():
            msg = self.msgq.delete_head_nowait()
            if msg is not None:
                raw = msg.to_string()
                payload = raw if isinstance(raw, bytes) else raw.encode()
                try:
                    parsed = json.loads(payload)
                    self._handle_event(parsed)
                    self.socket.send(json.dumps(parsed).encode(), zmq.NOBLOCK)
                except (json.JSONDecodeError, TypeError):
                    # Forward raw for inspection during discovery
                    self.socket.send(payload, zmq.NOBLOCK)
                    print(f"[meta] raw: {payload[:120]}")
                except zmq.Again:
                    pass
            else:
                time.sleep(0.005)

    def _handle_event(self, msg: dict) -> None:
        jtype = msg.get("json_type", "")
        tgid  = msg.get("tgid")
        freq  = msg.get("freq")

        if jtype:
            print(f"[meta] {jtype:20s} tgid={tgid} freq={freq} "
                  f"src={msg.get('srcaddr', '-')}")

        # Channel grant → allocate a voice lane
        if jtype in ("channel_grant", "update") and tgid and freq:
            visible_min = CONTROL_FREQ_HZ - VISIBLE_BW_HZ // 2
            visible_max = CONTROL_FREQ_HZ + VISIBLE_BW_HZ // 2
            if visible_min <= freq <= visible_max:
                self.lane_manager.on_grant(tgid, freq)
            else:
                print(f"[meta] grant {tgid} @ {freq/1e6:.4f} MHz outside visible band — ignored")

        # Call end / de-grant → release lane
        elif jtype in ("call_end", "release") and tgid:
            self.lane_manager.on_release(tgid)

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Lane manager
# Owns the voice lane pool. Called by MetadataPoller on grant/release.
# ---------------------------------------------------------------------------

class LaneManager:
    def __init__(self, lanes: list[VoiceLane]) -> None:
        self._lanes = lanes
        self._tgid_to_lane: dict[int, VoiceLane] = {}
        self._lock = threading.Lock()

    def on_grant(self, tgid: int, freq: int) -> None:
        with self._lock:
            # Already have a lane for this TGID — retune if freq changed
            if tgid in self._tgid_to_lane:
                lane = self._tgid_to_lane[tgid]
                if lane.freq != freq:
                    print(f"[lane] retune lane {lane.lane_id} "
                          f"tgid={tgid} {lane.freq/1e6:.4f}→{freq/1e6:.4f} MHz")
                    lane.assign(tgid, freq)
                return

            # Find a free lane
            free = next((l for l in self._lanes if not l.active), None)
            if free is None:
                print(f"[lane] pool exhausted — dropping grant tgid={tgid} "
                      f"freq={freq/1e6:.4f} MHz (cap={VOICE_LANE_CAP})")
                return

            free.assign(tgid, freq)
            self._tgid_to_lane[tgid] = free
            print(f"[lane] assigned lane {free.lane_id} "
                  f"tgid={tgid} freq={freq/1e6:.4f} MHz")

    def on_release(self, tgid: int) -> None:
        with self._lock:
            lane = self._tgid_to_lane.pop(tgid, None)
            if lane:
                print(f"[lane] released lane {lane.lane_id} tgid={tgid}")
                lane.release()

    def lane_state(self) -> list[dict]:
        """Snapshot of current lane assignments — for ZMQ bridge."""
        with self._lock:
            return [
                {"lane_id": l.lane_id, "active": l.active,
                 "tgid": l.tgid, "freq": l.freq}
                for l in self._lanes
            ]


# ---------------------------------------------------------------------------
# Flowgraph
# ---------------------------------------------------------------------------

class P25CaptureFlowgraph(gr.top_block):
    def __init__(self) -> None:
        super().__init__("P25 Phase 1 Multi-Lane Capture")

        # Shared message queue for control lane metadata
        self.msgq = gr.msg_queue(200)

        taps = channel_taps()

        # ----------------------------------------------------------------
        # Source — RTL-SDR Blog v4, centered on control channel
        # ----------------------------------------------------------------
        self.source = osmosdr.source(args="numchan=1 rtl=0")
        self.source.set_sample_rate(SOURCE_SAMPLE_RATE)
        self.source.set_center_freq(CONTROL_FREQ_HZ)
        self.source.set_freq_corr(0)
        self.source.set_gain_mode(False)
        self.source.set_gain(RTL_GAIN)
        self.source.set_if_gain(20)
        self.source.set_bb_gain(20)
        self.source.set_antenna("", 0)
        self.source.set_bandwidth(0)

        # ----------------------------------------------------------------
        # Control lane — fixed at center (offset = 0)
        # Drives the metadata queue; does not produce audio output.
        # ----------------------------------------------------------------
        self.ctrl_filter = filter.freq_xlating_fir_filter_ccf(
            CHANNEL_DECIM, taps, 0, SOURCE_SAMPLE_RATE
        )
        self.ctrl_demod = op25.fsk4_demod_ff(
            self.msgq, CHANNEL_RATE, SYMBOL_RATE
        )
        # Control lane: do_output=False, do_audio_output=False
        # We only want trunking messages from this lane, not audio.
        self.ctrl_assembler = op25_repeater.p25_frame_assembler(
            OP25_UDP_HOST, OP25_UDP_PORT, OP25_DEBUG,
            True,   # do_imbe
            False,  # do_output — no audio from control lane
            True,   # do_msgq
            self.msgq,
            False,  # do_audio_output
            False,  # do_phase2_tdma
            False,  # do_nocrypt
        )

        self.connect(self.source, self.ctrl_filter)
        self.connect(self.ctrl_filter, self.ctrl_demod)
        self.connect(self.ctrl_demod, self.ctrl_assembler)

        # ----------------------------------------------------------------
        # Voice lane pool
        # Each lane: channelizer → demod → frame_assembler → f2s → ZMQ PUSH
        # Channelizer offset is set to 0 initially; LaneManager retunes
        # on grant via lane.channelizer.set_center_freq(offset).
        # ----------------------------------------------------------------
        self.voice_lanes: list[VoiceLane] = []

        for i in range(VOICE_LANE_CAP):
            vf = filter.freq_xlating_fir_filter_ccf(
                CHANNEL_DECIM, taps, 0, SOURCE_SAMPLE_RATE
            )
            vd = op25.fsk4_demod_ff(
                self.msgq, CHANNEL_RATE, SYMBOL_RATE
            )
            # Voice lane: do_output=True, do_audio_output=True
            # do_msgq=False — voice lanes don't need to write to the queue
            va = op25_repeater.p25_frame_assembler(
                OP25_UDP_HOST, OP25_UDP_PORT, OP25_DEBUG,
                True,   # do_imbe
                True,   # do_output
                False,  # do_msgq — voice lanes silent on metadata
                self.msgq,  # queue still required by signature
                True,   # do_audio_output
                False,  # do_phase2_tdma
                False,  # do_nocrypt
            )
            f2s = blocks.float_to_short(1, 32767.0)
            psink = zeromq.push_sink(
                gr.sizeof_short, 1,
                pcm_endpoint(i),
                100, False, -1,
            )

            self.connect(self.source, vf)
            self.connect(vf, vd)
            self.connect(vd, va)
            self.connect(va, f2s)
            self.connect(f2s, psink)

            lane = VoiceLane(
                lane_id=i,
                channelizer=vf,
                demod=vd,
                frame_assembler=va,
                f2s=f2s,
                pcm_sink=psink,
            )
            self.voice_lanes.append(lane)

        # ----------------------------------------------------------------
        # Lane manager + metadata poller
        # ----------------------------------------------------------------
        self.lane_manager = LaneManager(self.voice_lanes)
        self.meta_poller = MetadataPoller(
            self.msgq, META_ENDPOINT, self.lane_manager
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 58)
    print("  Phase 1A — P25 Multi-Lane Capture Flowgraph")
    print("=" * 58)
    print(f"  Control channel  : {CONTROL_FREQ_HZ / 1e6:.4f} MHz")
    print(f"  Source rate      : {SOURCE_SAMPLE_RATE / 1e6:.3f} MHz")
    print(f"  Channel rate     : {CHANNEL_RATE / 1e3:.0f} kHz  "
          f"(decim {CHANNEL_DECIM}, exact)")
    print(f"  Symbol rate      : {SYMBOL_RATE} sym/s")
    print(f"  Visible band     : ±{VISIBLE_BW_HZ/2/1e6:.3f} MHz  "
          f"(~{THEORETICAL_MAX_LANES} RF slots)")
    print(f"  Voice lane cap   : {VOICE_LANE_CAP}")
    print(f"  Meta endpoint    : {META_ENDPOINT}")
    print(f"  PCM endpoints    : {pcm_endpoint(0)} … {pcm_endpoint(VOICE_LANE_CAP-1)}")
    print()

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
    print("Flowgraph running. Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(5)
            state = tb.lane_manager.lane_state()
            active = [l for l in state if l["active"]]
            print(f"[lanes] {len(active)}/{VOICE_LANE_CAP} active: "
                  + ", ".join(f"lane{l['lane_id']}=tgid{l['tgid']}" for l in active))
    except KeyboardInterrupt:
        pass
    finally:
        tb.meta_poller.stop()
        tb.stop()
        tb.wait()


if __name__ == "__main__":
    main()