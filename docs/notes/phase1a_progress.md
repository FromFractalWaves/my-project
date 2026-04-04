# Phase 1A -- Session Handoff (2026-04-04)

*Current state, decisions made, and next steps for resuming work.*

---

## What This Is

Phase 1A of the radio dispatch intelligence pipeline (Albatross reference implementation). The goal is to capture P25 Phase 1 trunked radio transmissions and emit structured `TransmissionPacket` objects for downstream processing (ASR, TRM, storage -- all Phase 1B and beyond).

Phase 1A ends at the packet boundary. It produces WAV files and JSONL packets. Nothing downstream is its concern.

---

## Hardware and Environment

| Item | Detail |
|---|---|
| SDR | RTL-SDR Blog v4 (confirmed via `lsusb`) |
| OS | Ubuntu |
| GNU Radio | 3.10.12.0 |
| gr-op25 | boatbod fork, built and installed |
| Install path | `/usr/local/lib/python3.13/dist-packages/gnuradio/` |
| Python path fix | `export PYTHONPATH=/usr/local/lib/python3.13/dist-packages:$PYTHONPATH` |
| Control channel | 854.6125 MHz (P25 Phase 1, confirmed working) |

The PYTHONPATH export needs to be in `~/.bashrc` or set in any terminal before running flowgraph.py. Modules are imported as `from gnuradio import op25, op25_repeater` -- not `import op25` directly.

---

## Codebase

The current prototype is `phase1a_capture_v2/`. Layout:

```
phase1a_capture_v2/
├── flowgraph.py          GNU Radio multi-lane capture flowgraph
├── zmq_bridge.py         Correlates lane/TGID and repackages PCM into multipart frames
├── tsbk_dump.py          Standalone TSBK dumper for system discovery (NEW)
├── phase1a/
│   ├── settings.py       All shared constants and endpoint config
│   ├── models.py         MetadataEvent, PCMHeader, ActiveCall, CompletedCall, TransmissionPacket
│   ├── buffer_manager.py Per-talkgroup call lifecycle manager
│   ├── wav_writer.py     Writes mono int16 WAV files
│   ├── packet_builder.py Constructs TransmissionPacket from CompletedCall
│   ├── packet_emitter.py JSONL and stdout sinks
│   ├── tsbk.py           Standalone TSBK parser extracted from OP25 (GPL v3)
│   ├── capture_backend.py Bridge-facing backend runtime
│   └── run_backend.py    CLI entrypoint
├── tests/
│   ├── test_buffer_manager.py
│   └── test_packet_and_wav.py
└── requirements.txt      pyzmq>=25
```

---

## Session Summary (2026-04-04)

**Goal:** Get the Phase 1A capture flowgraph running against a real P25 radio for the first time.

**Starting state:** The `prototypes/phase1a_capture_v2/` prototype existed but had never been tested against live RF. Unit tests existed but had not been run.

### What was done, in order

#### 1. Unit tests -- PASSED

All 3 tests in `tests/` passed: `test_buffer_manager`, `test_packet_and_wav`.

#### 2. RTL-SDR detection -- CONFIRMED

RTL-SDR Blog v4 confirmed detected via `lsusb`.

#### 3. Flowgraph fixes (multiple rounds)

Several issues found and fixed in `flowgraph.py`:

- **`firdes.WIN_HAMMING` moved in GNU Radio 3.10+.** Changed to `window.WIN_HAMMING`, imported from `gnuradio.fft.window`.

- **Missing FM discriminator.** `freq_xlating_fir_filter_ccf` outputs complex, `fsk4_demod_ff` expects float. Added `analog.quadrature_demod_cf` between them with gain = `channel_rate / (2*pi*600)` where 600 Hz is the P25 symbol deviation.

- **Missing slicer.** `fsk4_demod_ff` outputs float, `p25_frame_assembler` expects bytes. Added `op25_repeater.fsk4_slicer_fb(0, 0, [-2.0, 0.0, 2.0, 4.0])`.

- **Missing C4FM matched filter.** The hand-built chain (channelizer -> FM demod -> fsk4_demod -> slicer) produced raw TSBKs but signal quality was marginal. Switched to using OP25's `p25_demod_fb` hierblock from `p25_demodulator.py` which includes AGC, symbol filter (C4FM taps), and fsk4_demod internally. Requires `sys.path.insert` for the OP25 apps dir.

- **`float_to_short` removed from voice lanes.** `p25_frame_assembler` with `do_audio_output=True` already outputs int16 (2 bytes per sample, `sizeof=2`).

- **Voice lanes commented out.** ZMQ push sinks with no subscriber caused blocking. Disabled for the metadata discovery phase.

- **Separate `autotuneq` for `fsk4_demod_ff`.** Was incorrectly sharing the metadata msg_queue.

#### 4. Key discovery: msg_queue produces raw binary TSBKs, NOT JSON

This was the single biggest finding. The `p25_frame_assembler` writes raw P25 TSBK PDUs into the msg_queue:

- Message type = 7
- 12 bytes each: 2-byte NAC prefix + 10-byte TSBK body
- JSON metadata does NOT come from the C++ blocks -- it comes from OP25's Python-level `trunking.py` module which parses these TSBKs
- The original MetadataPoller's JSON parsing approach was fundamentally wrong and must be replaced with TSBK decoding

#### 5. TSBK dumper and system discovery

Wrote `tsbk_dump.py`, a standalone TSBK dumper that decodes raw TSBKs from the msg_queue. Used this to discover and confirm the target system parameters (see next section).

---

## Confirmed System Parameters

Discovered via live TSBK decoding on 2026-04-04.

| Parameter | Value |
|---|---|
| NAC | `0x01F0` |
| WACN | `0xBEE00` |
| SysID | `0x1F5` |
| RFSS | 80 |
| Site | 16 |
| Control channel | 854.6125 MHz |
| Freq identifier 0 | base=851.006250 MHz, step=6.250 kHz, offset=-45 MHz |
| Freq identifier 1 | base=762.006250 MHz, step=6.250 kHz, offset=+30 MHz |
| Freq table opcode | `0x3d` (`iden_up`), NOT `0x34` (`iden_up_vu`) |

**Active talkgroups observed:** 6096, 6121, 6296, 6005, 6038, 6040, 6122, 6258

**Source radio IDs:** Present on `grp_v_ch_grant` (opcode `0x00`), `srcaddr` field confirmed.

**Voice channel frequencies observed:** 856.3125, 856.3375, 857.3125 MHz

---

## Visible Band (Resolved)

Voice channels sit at 856-857 MHz, roughly 2 MHz above the 854.6125 MHz control channel. The original visible band of +/-1.024 MHz (2.048 Msps) did not cover them.

**Resolution:** Center frequency shifted to 855.75 MHz, sample rate increased to 3.2 Msps (decim 50, same 64 kHz channel rate). Visible band is now 854.150-857.350 MHz, covering both the control channel (-1.1375 MHz offset) and all observed voice channels. Validated live — 6 voice lanes assigned simultaneously with no sample drops at 3.2 Msps on the RTL-SDR v4.

---

## Architecture

```
RTL-SDR
  └── flowgraph.py  (GNU Radio + gr-op25 blocks)
        ├── metadata lane  →  ZMQ PUSH :5557  (raw TSBK bytes, NOT JSON)
        └── per-lane PCM   →  ZMQ PUSH :5560–:556N  (raw int16)

zmq_bridge.py
  ├── subscribes to :5557 (metadata)
  ├── subscribes to :5560–:556N (per-lane PCM)
  ├── maintains LaneState  {lane_id → tgid, freq, srcaddr}
  ├── forwards metadata →  ZMQ PUSH :5581  (backend ctrl)
  └── forwards tagged PCM → ZMQ PUSH :5580  (multipart: JSON header + raw PCM)

capture_backend.py  (python -m phase1a.run_backend)
  ├── subscribes to :5581 (ctrl)
  ├── subscribes to :5580 (PCM)
  ├── BufferManager  →  ActiveCall per TGID
  ├── on call end / inactivity timeout:
  │     WavWriter   →  out/wav/{timestamp}_{tgid}_{src}_{uuid}.wav
  │     PacketBuilder → TransmissionPacket
  │     JsonlPacketSink → out/packets.jsonl
  └── stdout log per packet
```

### Key design decisions

**gr-op25 blocks directly, not the stock app layer.** The OP25 repo (`boatbod` fork) has `gr-op25`, `gr-op25_repeater`, and a higher-level `apps/` layer (`rx.py`, `multi_rx.py`). The stock app layer was rejected because its output format required translation work. The flowgraph uses the blocks directly.

**One fixed control lane + N pooled voice lanes.** The control lane is offset from center frequency (-1.1375 MHz) and drives the metadata queue. Voice lanes are allocated from a pool on channel grants and returned on call end or inactivity. Lane count is configurable via `VOICE_LANE_CAP` in `settings.py` (currently 8).

**Lane pool sizing.** `VOICE_LANE_CAP` is a runtime cap, not an RF channel count. Theoretical max = `floor(3_200_000 / 12_500)` = 256 RF slots. Practical decoder budget is much lower. Start at 8, increase as validated.

**Sample rate math is exact.** `3_200_000 / 50 = 64_000 sps`. Passed truthfully to `p25_demod_fb`. No rounding.

**OP25 dependency.** The compiled gr-op25 and gr-op25_repeater GNU Radio blocks must be installed (they are). The Python-level TSBK parsing from `trunking.py` is being extracted into a standalone `phase1a/tsbk.py` module to avoid requiring the full OP25 apps directory at runtime.

---

## Confirmed Block Signatures and Signal Chain

### Control lane (metadata)

```
source → freq_xlating_fir_filter_ccf (decim 32) → quadrature_demod_cf → p25_demod_fb → p25_frame_assembler → [msg_queue for TSBKs]
```

### Voice lanes (PCM)

```
source → freq_xlating_fir_filter_ccf → quadrature_demod_cf → p25_demod_fb → p25_frame_assembler(do_audio_output=True) → [int16 PCM out, sizeof=2]
```

### Block signatures confirmed via `help()`:

```python
op25.fsk4_demod_ff(queue, sample_rate_Hz, symbol_rate_Hz, bfsk=False)

op25_repeater.p25_frame_assembler(
    udp_host, port, debug, do_imbe, do_output,
    do_msgq, queue, do_audio_output, do_phase2_tdma, do_nocrypt
)
```

- `p25_demod_fb` (OP25 hierblock from `p25_demodulator.py`): includes AGC, C4FM symbol filter taps, and `fsk4_demod_ff` internally. Uses `sys.path.insert` for the OP25 apps dir.
- `quadrature_demod_cf` gain: `channel_rate / (2*pi*600)` where 600 Hz = P25 symbol deviation.
- `fsk4_slicer_fb` levels: `[-2.0, 0.0, 2.0, 4.0]`

---

## Files Modified/Created This Session

| File | Status |
|---|---|
| `flowgraph.py` | Modified -- fixed signal chain (FM demod, slicer, p25_demod_fb, window import) |
| `tsbk_dump.py` | New -- standalone TSBK dumper for system discovery |
| `phase1a/tsbk.py` | New -- standalone TSBK parser extracted from OP25, tested against live data |

---

## Open Questions Resolved (2026-04-04)

- **gr-op25 output format:** Raw binary TSBKs (type=7, 12 bytes: 2-byte NAC + 10-byte body), NOT JSON.
- **`iden_up` opcode:** This system uses `0x3d`, not `0x34` (`iden_up_vu`).
- **`srcaddr` availability:** Present on `grp_v_ch_grant` (opcode `0x00`).
- **Frequency table:** 2 identifiers, base/step/offset confirmed.
- **`p25_frame_assembler` audio output:** int16 (2 bytes per sample), not float.
- **Unit tests:** All 3 pass.

---

## Open Questions Remaining

- **PCM sample rate** from `p25_frame_assembler` with `do_audio_output=True`. Likely 8 kHz but unconfirmed.
- **`set_center_freq()` on a live channelizer.** Does retuning a voice lane channelizer mid-stream cause artifacts or block the GR scheduler?
- **Right inactivity timeout.** 1.5s is the starting guess. P25 calls can have natural gaps; too tight will fragment transmissions.
- **Phase 1A/1B handoff mechanism.** Currently JSONL to disk. Phase 1B will need to consume packets -- handoff mechanism TBD (queue, database write, direct call).
- **Whether `srcaddr` is present on voice channel metadata** (not just control channel grants).
- **Voice lane count tuning.** 8 is the starting cap -- is it enough for this system's traffic?

---

## Completed Since Initial Session

- **TSBK parser** (`phase1a/tsbk.py`) — complete and tested against live data. Decodes opcodes 0x00, 0x02, 0x03, 0x33, 0x34, 0x3d.
- **Visible band** — resolved. Center shifted to 855.75 MHz, sample rate to 3.2 Msps (decim 50). All observed voice channels within band.
- **MetadataPoller** — rewritten to use `TSBKParser` instead of JSON parsing. Decodes raw TSBKs and emits structured grant events over ZMQ.
- **Voice lanes** — re-enabled with `p25_demod_fb` hierblock chain. 6 lanes assigned simultaneously in testing. ZMQ push sinks use 100ms timeout to avoid blocking when no subscriber.

---

## Next Steps (in order)

### 1. Run full 3-component stack

```bash
# Terminal 1
python flowgraph.py

# Terminal 2
python zmq_bridge.py

# Terminal 3
python -m phase1a.run_backend
```

Validate: WAV files in `out/wav/`, packets in `out/packets.jsonl`, correct TGID/srcaddr/frequency fields.

### 6. Confirm srcaddr on voice channel metadata

Verify that source radio ID is available not just on control channel grants but also in voice channel metadata. If not, the grant-time srcaddr must be carried through the bridge's LaneState.

### 7. Tune inactivity timeout

`INACTIVITY_TIMEOUT_S = 1.5` in `settings.py`. Tune against real traffic patterns. Too tight fragments transmissions; too loose delays packet emission.
