# Albatross 1A — P25 Capture Prototype

Radio capture prototype for the [Albatross](https://github.com/fractalwaves/albatross) intelligence pipeline. Captures P25 Phase 1 trunked radio transmissions and emits structured `TransmissionPacket` objects (WAV + metadata).

This repo is a working prototype. Once capture is validated end-to-end, the functionality will be transplanted into the main Albatross repo via spec extraction.

## What's Here

The capture prototype lives in `prototypes/phase1a_capture_v2/`. It decodes a P25 trunked radio system using an RTL-SDR, captures voice audio per talkgroup, and emits packets.

```
RTL-SDR → flowgraph.py (GNU Radio + gr-op25) → tsbk parser + voice lanes
    → zmq_bridge.py → capture_backend.py → WAV files + JSONL packets
```

See `docs/notes/phase1a_progress.md` for current state and next steps.

## Status

- Control channel decoding: working
- TSBK parsing (channel grants, frequency table): working
- Voice lane assignment from live grants: working
- PCM capture and WAV writing: not yet validated end-to-end
- Bridge + backend integration: not yet tested against live RF
