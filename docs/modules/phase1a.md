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

The RTL-SDR is tuned to a continuous band centered near the system's control channel. This band covers the control channel plus a subset of the system's voice channels. Any voice channel granted outside this band is silently ignored — no special handling required, the signal simply isn't present.

P25 voice channels are spaced 12.5kHz apart. A single RTL-SDR can realistically cover 1–2MHz of usable bandwidth, which is enough to capture many simultaneous voice channels from a local system.

Additional SDR hardware (HackRF, additional RTL-SDR dongles) is available but in storage and out of scope for this phase. The architecture is designed to accommodate multi-device expansion later without structural changes.

---

## Architecture

Phase 1A is a two-piece system:

```
RTL-SDR → [GNU Radio + gr-op25] → ZMQ → [Python Backend] → TransmissionPackets
```

**GNU Radio** handles all RF and signal processing. It is responsible for tuning, decoding the P25 control channel, decoding voice channels within the visible band, and streaming audio + metadata to the Python backend. No business logic lives in the flowgraph.

**Python backend** handles everything above the signal layer. It receives the stream from GNU Radio, manages per-talkgroup audio buffers, detects transmission boundaries, writes WAV files, and emits TransmissionPackets.

The boundary between the two is a ZMQ socket. GNU Radio has native ZMQ sink support. This gives a clean async boundary — the flowgraph never blocks on downstream processing.

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
| `low_pass_filter` | Band limiting |
| `op25` control channel decoder | Tracks trunking control channel, emits channel grants |
| `op25` voice channel decoder(s) | Decodes active voice channels within band |
| `zeromq.pub_sink` | Streams audio + metadata to Python backend |

### Design Decisions

**gr-op25, not OP25 or SDRTrunk.** OP25 and SDRTrunk were rejected because their data output format required significant translation work. gr-op25 is used as a library — the flowgraph emits exactly what the Python backend needs, with no intermediate format translation.

**ZMQ over plain socket.** Native GNU Radio support, clean async boundary, pub/sub model maps naturally to multi-channel output.

**No file writing in GNU Radio.** Audio artifacts are owned by the Python backend. The flowgraph streams raw PCM and stays out of the persistence business.

**No business logic in the flowgraph.** Transmission boundary detection, UUID generation, packet construction — all Python.

---

## Python Backend

### Responsibilities

- Subscribe to the ZMQ stream from GNU Radio
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

Call start and call end events come from gr-op25's control channel tracking. The Python backend uses these events as write triggers:

- `call_start(TGID, source_radio_id, frequency)` → open buffer for TGID
- `call_end(TGID)` → flush buffer → write WAV → emit TransmissionPacket

### WAV File Output

Each completed transmission is written as a mono WAV file. Naming convention:

```
{timestamp_start}_{tgid}_{source_radio_id}_{uuid}.wav
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
    "source_radio_id": "abc123",
    "frequency": 856437500
  },
  "metadata": {
    "talkgroup_id": 12345,
    "source_radio_id": "abc123",
    "frequency": 856437500,
    "system": "p25_phase1"
  },
  "payload": {
    "audio_path": "/path/to/audio.wav",
    "duration_seconds": 4.2,
    "sample_rate": 8000
  },
  "status": "captured"
}
```

The `metadata` field is what the TRM will consume in Phase 1B. It is populated here, at packet construction time, so the TRM never needs to reach back into the signal layer.

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

- What sample rate does gr-op25 output for decoded voice? (Likely 8kHz — confirm before WAV writer implementation)
- What is the exact ZMQ message format from gr-op25? Define the wire format before building the Python subscriber
- How does gr-op25 signal call boundaries — events, flags on the stream, or metadata frames? Confirm before building boundary detection
- What happens when a call_start arrives for a TGID that already has an open buffer? (Overlap / re-grant edge case — needs a handling policy)
- Where does the TransmissionPacket get handed off at the Phase 1A/1B boundary — a queue, a database write, a direct function call?