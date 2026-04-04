# Phase 1A — Capture Pipeline

*Radio dispatch intelligence pipeline — signal capture and packet emission*

*This phase covers signal processing, audio capture, and TransmissionPacket construction. Phase 1B covers the Thread Routing Module.*

---

## Objective

Build the capture layer of the radio pipeline. By the end of Phase 1A, the system will:

- Receive and decode a P25 Phase 1 trunked radio system using a single RTL-SDR
- Capture audio and metadata for every transmission within the visible band
- Buffer simultaneous transmissions independently per talkgroup
- Write each completed transmission to a WAV file
- Emit a structured `TransmissionPacket` for every completed transmission, conforming to the Albatross base Packet schema

Phase 1A ends at the packet boundary. Everything downstream — ASR, TRM, storage, UI — is Phase 1B and beyond.

---

## Hardware Constraint

**Single RTL-SDR dongle.**

The RTL-SDR is tuned to 855.75 MHz (between the control channel at 854.6125 MHz and the voice channels at 856-857 MHz) at 3.2 Msps, giving a visible band of 854.15-857.35 MHz. The control channel is decoded at a -1.1375 MHz offset from center. Any voice channel granted outside this band is silently ignored.

P25 voice channels are spaced 12.5kHz apart. At 3.2 Msps the theoretical maximum is 256 RF slots, though the practical decoder budget is much lower (currently capped at 8 voice lanes).

Additional SDR hardware (HackRF, additional RTL-SDR dongles) is available but in storage and out of scope for this phase. The architecture is designed to accommodate multi-device expansion later without structural changes.

---

## Architecture

Phase 1A is a two-piece system:

```
RTL-SDR → [GNU Radio + gr-op25 blocks] →  raw TSBK bytes (msg_queue)  → [TSBK Parser + MetadataPoller] → ZMQ PUSH (JSON)
                                        →  per-lane PCM (ZMQ PUSH)    → [ZMQ Bridge] → [Python Backend] → TransmissionPackets
```

**GNU Radio** handles all RF and signal processing. It tunes the RTL-SDR, decodes the P25 control channel (at an offset from center), decodes voice channels within the visible band, and streams audio + raw trunking data. The flowgraph uses the `gr-op25` and `gr-op25_repeater` blocks from the OP25 codebase directly — not the stock `rx.py` / `multi_rx.py` application layer. The `p25_demod_fb` hierblock from OP25's app layer is used for the C4FM matched filter and symbol timing.

**TSBK parser** (`phase1a/tsbk.py`) decodes the raw binary TSBKs from the message queue into structured channel grant events (tgid, frequency, srcaddr). Extracted from OP25's `trunking.py` (GPL v3).

**Python backend** handles everything above the signal layer. It receives parsed metadata and tagged PCM from the ZMQ bridge, manages per-talkgroup audio buffers, detects transmission boundaries, writes WAV files, and emits TransmissionPackets.

The boundary between GNU Radio and the backend is ZMQ sockets. GNU Radio has native ZMQ sink support. This gives a clean async boundary — the flowgraph never blocks on downstream processing.

---

## GNU Radio Layer

### Responsibilities

- Tune RTL-SDR to the target band
- Track the P25 control channel for trunking metadata (talkgroup assignments, channel grants)
- Decode voice channels within the visible band
- Stream per-channel audio (raw PCM) and associated P25 metadata over ZMQ

### Key Blocks

| Block | Role |
|---|---|
| `osmosdr.source` | RTL-SDR input |
| `filter.freq_xlating_fir_filter_ccf` | Channel filter — decimates and bandlimits to P25 channel |
| `op25.fsk4_demod_ff` | FSK4/C4FM demodulator — confirmed signature: `(queue, sample_rate_Hz, symbol_rate_Hz, bfsk=False)` |
| `op25_repeater.p25_frame_assembler` | P25 frame decoder and trunking engine — confirmed signature: `(udp_host, port, debug, do_imbe, do_output, do_msgq, queue, do_audio_output, do_phase2_tdma, do_nocrypt)` |
| `blocks.float_to_short` | Converts float audio samples to int16 PCM |
| `zeromq.push_sink` | PCM audio transport lane to Python backend (stream sink) |
| `gr.msg_queue` + `MetadataPoller` thread | Metadata transport lane — `p25_frame_assembler` writes trunking JSON into the queue; a background thread drains it and forwards to the Python backend over a ZMQ PUSH socket |

### Design Decisions

**gr-op25 blocks directly, not the stock OP25 app layer.** The OP25 codebase (boatbod fork) is structured as `gr-op25`, `gr-op25_repeater`, and a higher-level `apps/` layer (`rx.py`, `multi_rx.py`). The stock app layer was rejected because its data output format required significant translation work to fit the Albatross packet contract. Instead the flowgraph uses the `gr-op25` and `gr-op25_repeater` blocks directly, so the output is exactly what the Python backend needs with no intermediate translation.

**Two transport lanes, different mechanisms.** The PCM lane is a standard GNU Radio stream sink — `zeromq.push_sink` carries int16 samples directly. The metadata lane uses a `gr.msg_queue` — `p25_frame_assembler` writes raw binary TSBK PDUs (12 bytes: 2-byte NAC + 10-byte body, message type 7) into the queue. A `MetadataPoller` background thread drains the queue, decodes TSBKs via `phase1a/tsbk.py`, and forwards parsed grant events as JSON bytes over a ZMQ PUSH socket. Note: the C++ blocks do NOT produce JSON — the TSBK parser handles all decoding in Python.

**No file writing in GNU Radio.** Audio artifacts are owned by the Python backend. The flowgraph streams raw PCM and stays out of the persistence business.

**No business logic in the flowgraph.** Transmission boundary detection, UUID generation, packet construction — all Python.

---

## Python Backend

### Responsibilities

- Subscribe to the ZMQ PCM stream lane and the ZMQ metadata/control message lane independently
- Maintain a per-talkgroup audio buffer map
- Use P25 call start / call end events to open and close buffers
- On call end: flush buffer, write WAV file, emit TransmissionPacket
- Handle simultaneous transmissions on multiple talkgroups independently

### Audio Buffer Map

```
buffer_map = {
    TGID_1: [pcm_chunk, pcm_chunk, ...],
    TGID_2: [pcm_chunk, pcm_chunk, ...],
    ...
}
```

Each talkgroup gets its own independent buffer. Simultaneous transmissions on different talkgroups are just separate keys — no channel multiplexing, no stereo tricks. When a call ends on a given TGID, that buffer is flushed to WAV and cleared. There is no upper limit on simultaneous active buffers within the visible band.

### Transmission Boundary Detection

The OP25 codebase exposes trunking and channel state via a metadata stream — fields including `frequency_data`, `channel_update`, and `srcaddr` are present in recent versions of the boatbod repo. However, this metadata is chatty and the presence and reliability of specific fields (particularly source radio ID) can vary depending on which update stream is used and how the system is configured.

The Python backend should not assume a clean `call_start(TGID, source_radio_id, frequency)` / `call_end(TGID)` signal pair exists out of the box. Transmission boundaries may need to be derived from a combination of channel update events and an inactivity policy (e.g. close a buffer if no PCM arrives for a TGID within N seconds).

The exact boundary detection strategy is an implementation-discovery item. See Open Questions.

### WAV File Output

Each completed transmission is written as a mono WAV file. Naming convention:

```
{timestamp_start}_{tgid}_{source_radio_id_or_unknown}_{uuid}.wav
```

Files are written to a configurable output directory. The path is stored in the TransmissionPacket.

### TransmissionPacket Emission

On call end, the Python backend constructs a `TransmissionPacket` and emits it downstream (to a queue, message bus, or direct handoff to the ingestion service — TBD at Phase 1B boundary).

---

## TransmissionPacket Schema

Conforms to the Albatross base Packet schema with radio-specific fields:

```json
{
  "packet_id": "uuid",
  "packet_type": "transmission",
  "timestamp_start": "ISO8601",
  "timestamp_end": "ISO8601",
  "source": {
    "talkgroup_id": 12345,
    "source_radio_id": "abc123 | null",
    "frequency": 856437500
  },
  "metadata": {
    "talkgroup_id": 12345,
    "source_radio_id": "abc123 | null",
    "frequency": 856437500,
    "system": "p25_phase1"
  },
  "payload": {
    "audio_path": "/path/to/audio.wav",
    "duration_seconds": 4.2,
    "sample_rate": null
  },
  "status": "captured"
}
```

`source_radio_id` is nullable — presence depends on what the OP25 metadata stream exposes for a given transmission. `sample_rate` is populated at implementation time once the gr-op25 output rate is confirmed.

---

## What Phase 1A Does Not Cover

- ASR / transcription (Phase 1B preprocessing)
- TRM routing (Phase 1B)
- Storage / database (Phase 1B)
- UI (later phase)
- Multi-device SDR expansion
- Encrypted talkgroup handling
- Frequencies outside the visible band

---

## Success Criteria

- GNU Radio flowgraph tunes to the target band and tracks the P25 control channel
- Voice channel audio within the visible band is decoded and streamed to Python
- Simultaneous transmissions on different talkgroups are buffered independently
- Each completed transmission produces a valid WAV file on disk
- Each completed transmission produces a valid TransmissionPacket conforming to the Albatross base schema
- The system runs continuously without blocking or dropping the capture layer
- Phase 1B can consume TransmissionPackets without any changes to Phase 1A

---

## Open Questions

### Resolved

- **Metadata wire format.** The msg_queue produces raw binary TSBK PDUs (type=7, 12 bytes), NOT JSON. Decoded by `phase1a/tsbk.py`. Field inventory confirmed: opcodes 0x00 (grant with tgid/freq/srcaddr), 0x02 (grant update), 0x3d (iden_up for frequency table).
- **source_radio_id availability.** `srcaddr` is present on `grp_v_ch_grant` (opcode 0x00). Not present on `grp_v_ch_grant_updt` (opcode 0x02). The grant-time srcaddr must be carried through via the LaneManager/bridge.
- **PCM lane framing.** Each voice lane has its own ZMQ push sink on a dedicated port. PCM-to-TGID correlation is handled by the LaneManager which maps lanes to TGIDs based on channel grants.

### Remaining

- **gr-op25 PCM output sample rate.** Likely 8kHz but must be confirmed before WAV writer implementation. Set `sample_rate` in the packet schema once known.
- **Transmission boundary detection strategy.** Can call open/close be derived cleanly from the metadata stream, or does the backend need to combine metadata events with a PCM inactivity timeout? What is the right inactivity window?
- **Overlap / re-grant edge case.** What happens when a channel update for a TGID arrives while that TGID already has an open buffer? Define a handling policy before building the buffer manager.
- **Phase 1A/1B handoff.** Where does the TransmissionPacket get handed off at the boundary — a queue, a database write, or a direct function call?
- **Lane release.** Lanes are currently assigned on grants but never released. Need either explicit release events from TSBKs or inactivity-based release in the LaneManager.