#!/usr/bin/env python3
"""
Minimal TSBK dumper — decodes raw P25 TSBKs from the msg_queue
to discover what the control channel is broadcasting.

Run this instead of the full flowgraph to understand the system.
"""
from __future__ import annotations

import ctypes
import json
import signal
import sys
import time

sys.path.insert(0, "/home/fractalwaves/clones/op25/op25/gr-op25_repeater/apps")
sys.path.insert(0, "/home/fractalwaves/clones/op25/op25/gr-op25_repeater/apps/tx")

from gnuradio import analog, blocks, filter, gr
from gnuradio import op25, op25_repeater
from gnuradio.filter import firdes
from gnuradio.fft import window
import osmosdr
from p25_demodulator import p25_demod_fb

from phase1a.settings import (
    CHANNEL_DECIM, CHANNEL_RATE, CONTROL_FREQ_HZ,
    RTL_GAIN, SOURCE_SAMPLE_RATE, SYMBOL_RATE,
)

TWO_PI = 2.0 * 3.14159265358979323846
P25_SYMBOL_DEVIATION = 600.0


def get_ordinals(s):
    """Convert bytes to integer, big-endian."""
    if isinstance(s, str):
        s = s.encode("latin-1")
    n = 0
    for b in s:
        n = (n << 8) + b
    return n


# Channel identifier table — populated from iden_up TSBKs
freq_table = {}

OPCODE_NAMES = {
    0x00: "grp_v_ch_grant",
    0x02: "grp_v_ch_grant_updt",
    0x03: "grp_v_ch_grant_updt_exp",
    0x16: "sndcp_data_ch",
    0x20: "ack_response",
    0x22: "grp_aff_response",
    0x28: "unit_reg_response",
    0x2c: "sys_srv_bcast",
    0x2f: "u_de_reg_ack",
    0x33: "iden_up_tdma",
    0x34: "iden_up_vu",
    0x05: "mot_grp_cn_grant",
    0x09: "mot_grp_cn_grant_updt",
    0x0b: "mot_grp_cn_grant_updt_exp",
    0x14: "sndcp_data_pgact",
    0x27: "deny_response",
    0x30: "pwr_meas_report",
    0x39: "secondary_cc",
    0x3a: "rfss_sts_bcast",
    0x3b: "net_sts_bcast",
    0x3c: "adj_sts_bcast",
    0x3d: "iden_up",
}


def channel_id_to_frequency(ch):
    iden = (ch >> 12) & 0xf
    channel = ch & 0xfff
    if iden not in freq_table:
        return None
    base = freq_table[iden]['frequency']
    step = freq_table[iden]['step']
    offset = freq_table[iden]['offset']
    freq = base + (step * channel)
    return freq


def decode_tsbk(s):
    """Decode a single TSBK and print human-readable output."""
    nac = get_ordinals(s[:2])
    tsbk_bytes = s[2:]
    tsbk = get_ordinals(tsbk_bytes)
    tsbk = tsbk << 16  # for missing crc

    opcode = (tsbk >> 88) & 0x3f
    mfrid = (tsbk >> 80) & 0xff
    name = OPCODE_NAMES.get(opcode, f"unknown_0x{opcode:02x}")

    if opcode == 0x34:  # iden_up vhf/uhf
        iden = (tsbk >> 76) & 0xf
        bwvu = (tsbk >> 72) & 0xf
        toff0 = (tsbk >> 58) & 0x3fff
        spac = (tsbk >> 48) & 0x3ff
        freq = (tsbk >> 16) & 0xffffffff
        toff_sign = (toff0 >> 13) & 1
        toff = toff0 & 0x1fff
        if toff_sign == 0:
            toff = 0 - toff
        freq_table[iden] = {
            'offset': toff * spac * 125,
            'step': spac * 125,
            'frequency': freq * 5,
        }
        print(f"  [{name}] id={iden} base={freq*0.000005:.6f}MHz step={spac*0.125:.3f}kHz offset={toff*spac*0.125*1e-3:.3f}kHz", flush=True)

    elif opcode == 0x33:  # iden_up_tdma
        iden = (tsbk >> 76) & 0xf
        toff0 = (tsbk >> 58) & 0x3fff
        spac = (tsbk >> 48) & 0x3ff
        f1 = (tsbk >> 16) & 0xffffffff
        toff_sign = (toff0 >> 13) & 1
        toff = toff0 & 0x1fff
        if toff_sign == 0:
            toff = 0 - toff
        freq_table[iden] = {
            'offset': toff * spac * 125,
            'step': spac * 125,
            'frequency': f1 * 5,
        }
        print(f"  [{name}] id={iden} base={f1*0.000005:.6f}MHz step={spac*0.125:.3f}kHz", flush=True)

    elif opcode == 0x00:  # group voice channel grant
        if mfrid != 0x90:
            opts = (tsbk >> 72) & 0xff
            ch = (tsbk >> 56) & 0xffff
            ga = (tsbk >> 40) & 0xffff
            sa = (tsbk >> 16) & 0xffffff
            f = channel_id_to_frequency(ch)
            f_str = f"{f/1e6:.6f}MHz" if f else f"ch=0x{ch:04x}(unknown)"
            print(f"  [{name}] freq={f_str} tgid={ga} srcaddr={sa} opts=0x{opts:02x}", flush=True)
        else:
            print(f"  [mot_grg_add_cmd] mfrid=0x90", flush=True)

    elif opcode == 0x02:  # group voice channel grant update
        if mfrid == 0x90:
            ch = (tsbk >> 56) & 0xffff
            sg = (tsbk >> 40) & 0xffff
            sa = (tsbk >> 16) & 0xffffff
            f = channel_id_to_frequency(ch)
            f_str = f"{f/1e6:.6f}MHz" if f else f"ch=0x{ch:04x}(unknown)"
            print(f"  [mfid90_grg_grant] freq={f_str} sg={sg} srcaddr={sa}", flush=True)
        else:
            ch1 = (tsbk >> 64) & 0xffff
            ga1 = (tsbk >> 48) & 0xffff
            ch2 = (tsbk >> 32) & 0xffff
            ga2 = (tsbk >> 16) & 0xffff
            f1 = channel_id_to_frequency(ch1)
            f2 = channel_id_to_frequency(ch2)
            f1_str = f"{f1/1e6:.6f}MHz" if f1 else f"ch=0x{ch1:04x}"
            f2_str = f"{f2/1e6:.6f}MHz" if f2 else f"ch=0x{ch2:04x}"
            print(f"  [{name}] f1={f1_str} tgid1={ga1} f2={f2_str} tgid2={ga2}", flush=True)

    elif opcode == 0x03:  # grant update explicit
        if mfrid == 0:
            opts = (tsbk >> 72) & 0xff
            ch1 = (tsbk >> 48) & 0xffff
            ch2 = (tsbk >> 32) & 0xffff
            ga = (tsbk >> 16) & 0xffff
            f = channel_id_to_frequency(ch1)
            f_str = f"{f/1e6:.6f}MHz" if f else f"ch=0x{ch1:04x}"
            print(f"  [{name}] freq={f_str} tgid={ga} opts=0x{opts:02x}", flush=True)

    elif opcode == 0x3a:  # rfss status broadcast
        lra = (tsbk >> 72) & 0xff
        sysid = (tsbk >> 60) & 0xfff
        rfssid = (tsbk >> 52) & 0xff
        siteid = (tsbk >> 44) & 0xff
        ch = (tsbk >> 28) & 0xffff
        ssc = (tsbk >> 16) & 0xff
        f = channel_id_to_frequency(ch)
        f_str = f"{f/1e6:.6f}MHz" if f else f"ch=0x{ch:04x}"
        print(f"  [{name}] sysid=0x{sysid:03x} rfss={rfssid} site={siteid} ch={f_str}", flush=True)

    elif opcode == 0x3b:  # network status broadcast
        lra = (tsbk >> 72) & 0xff
        wacn = (tsbk >> 52) & 0xfffff
        sysid = (tsbk >> 40) & 0xfff
        ch = (tsbk >> 24) & 0xffff
        ssc = (tsbk >> 16) & 0xff
        f = channel_id_to_frequency(ch)
        f_str = f"{f/1e6:.6f}MHz" if f else f"ch=0x{ch:04x}"
        print(f"  [{name}] wacn=0x{wacn:05x} sysid=0x{sysid:03x} ch={f_str}", flush=True)

    elif opcode == 0x3d:  # iden_up
        iden = (tsbk >> 76) & 0xf
        bw = (tsbk >> 67) & 0x1ff
        toff0 = (tsbk >> 58) & 0x1ff
        spac = (tsbk >> 48) & 0x3ff
        freq = (tsbk >> 16) & 0xffffffff
        toff_sign = (toff0 >> 8) & 1
        toff = toff0 & 0xff
        if toff_sign == 0:
            toff = 0 - toff
        freq_table[iden] = {
            'offset': toff * 250000,
            'step': spac * 125,
            'frequency': freq * 5,
        }
        print(f"  [{name}] id={iden} base={freq*0.000005:.6f}MHz step={spac*0.125:.3f}kHz offset={toff*0.25:.3f}MHz bw={bw}", flush=True)

    elif opcode == 0x3c:  # adjacent status broadcast
        lra = (tsbk >> 72) & 0xff
        sysid = (tsbk >> 52) & 0xfff
        rfssid = (tsbk >> 44) & 0xff
        siteid = (tsbk >> 36) & 0xff
        ch = (tsbk >> 20) & 0xffff
        ssc = (tsbk >> 16) & 0xf
        f = channel_id_to_frequency(ch)
        f_str = f"{f/1e6:.6f}MHz" if f else f"ch=0x{ch:04x}"
        print(f"  [{name}] sysid=0x{sysid:03x} rfss={rfssid} site={siteid} ch={f_str}", flush=True)

    else:
        print(f"  [{name}] nac=0x{nac:04x} mfrid=0x{mfrid:02x} raw={tsbk_bytes.hex()}", flush=True)


def process_qmsg(msg):
    m_type = ctypes.c_int16(msg.type() & 0xffff).value
    if m_type == 7:  # TSBK
        s = msg.to_string()
        if isinstance(s, str):
            s = s.encode("latin-1")
        decode_tsbk(s)
    elif m_type == -3:  # call signalling (JSON)
        try:
            js = json.loads(msg.to_string())
            print(f"  [call_sig] {js}", flush=True)
        except Exception:
            pass
    elif m_type == -4:  # sync established
        print("  [sync] P25 sync established", flush=True)
    elif m_type == -1:  # timeout
        pass  # normal
    elif m_type > 0:
        print(f"  [duid] type={m_type} len={len(msg.to_string())}", flush=True)


def main():
    msgq = gr.msg_queue(200)
    taps = firdes.low_pass(1.0, SOURCE_SAMPLE_RATE, 6250, 1500, window.WIN_HAMMING)
    fm_demod_gain = CHANNEL_RATE / (TWO_PI * P25_SYMBOL_DEVIATION)

    source = osmosdr.source(args="numchan=1 rtl=0")
    source.set_sample_rate(SOURCE_SAMPLE_RATE)
    source.set_center_freq(CONTROL_FREQ_HZ)
    source.set_freq_corr(0)
    source.set_gain_mode(False)
    source.set_gain(RTL_GAIN)
    source.set_if_gain(20)
    source.set_bb_gain(20)

    chan_filter = filter.freq_xlating_fir_filter_ccf(CHANNEL_DECIM, taps, 0, SOURCE_SAMPLE_RATE)
    fm = analog.quadrature_demod_cf(fm_demod_gain)
    demod = p25_demod_fb(input_rate=CHANNEL_RATE)
    assembler = op25_repeater.p25_frame_assembler(
        "127.0.0.1", 0, 0,
        True,   # do_imbe
        False,  # do_output
        True,   # do_msgq
        msgq,
        False,  # do_audio_output
        False,  # do_phase2_tdma
        False,  # do_nocrypt
    )

    tb = gr.top_block("TSBK Dump")
    tb.connect(source, chan_filter, fm, demod, assembler)

    print("=" * 50, flush=True)
    print("  TSBK Dumper — Control Channel Discovery", flush=True)
    print("=" * 50, flush=True)
    print(f"  Freq: {CONTROL_FREQ_HZ/1e6:.4f} MHz", flush=True)
    print(f"  Rate: {CHANNEL_RATE/1e3:.0f} kHz (decim {CHANNEL_DECIM})", flush=True)
    print(flush=True)

    def handle_sigint(sig, frame):
        print("\nStopping...", flush=True)
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)
    tb.start()
    print("Listening... Ctrl+C to stop.\n", flush=True)

    try:
        count = 0
        while True:
            msg = msgq.delete_head_nowait()
            if msg is None:
                time.sleep(0.005)
                continue
            count += 1
            process_qmsg(msg)
            if count % 100 == 0:
                print(f"--- {count} messages processed, {len(freq_table)} iden entries ---", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()


if __name__ == "__main__":
    main()
