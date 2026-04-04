# CLAUDE.md

## Project

Albatross 1A is the capture prototype for the Albatross intelligence pipeline. It captures P25 Phase 1 trunked radio transmissions using an RTL-SDR and emits structured `TransmissionPacket` objects (WAV files + JSONL metadata).

This is a working prototype, not a production system. Once capture is validated end-to-end, the functionality will be transplanted into the main Albatross repo (`~/projects/albatross`) via spec extraction.

## Current State

The capture flowgraph runs against live RF. Control channel decoding, TSBK parsing, and voice lane assignment from live channel grants are all working. The ZMQ bridge and capture backend have not yet been tested against live RF — that's the next step.

The prototype lives in `prototypes/phase1a_capture_v2/`. Everything else in the repo (docs, specs) is context and design documentation.

## Running

```bash
export PYTHONPATH=/usr/local/lib/python3.13/dist-packages:$PYTHONPATH
cd prototypes/phase1a_capture_v2

# Run tests (no hardware needed)
.venv/bin/python -m pytest tests/ -v

# Run the flowgraph (requires RTL-SDR plugged in)
python3 -u flowgraph.py

# Run the TSBK dumper (control channel discovery tool)
python3 -u tsbk_dump.py

# Full stack (not yet validated end-to-end):
# Terminal 1: python3 flowgraph.py
# Terminal 2: python3 zmq_bridge.py
# Terminal 3: python3 -m phase1a.run_backend
```

## Architecture

```
RTL-SDR (855.75 MHz center, 3.2 Msps)
  └── flowgraph.py
        ├── control lane (-1.1375 MHz offset) → msg_queue → MetadataPoller → tsbk.py → ZMQ :5557
        └── 8 voice lanes (retuned on grants) → ZMQ :5560-:5567 (int16 PCM)

zmq_bridge.py
  ├── metadata from :5557 → tagged PCM + ctrl → :5580/:5581

capture_backend.py
  ├── BufferManager → WAV files + TransmissionPacket JSONL
```

## Key Files

| File | Role |
|------|------|
| `flowgraph.py` | GNU Radio flowgraph — SDR, control channel, voice lanes |
| `tsbk_dump.py` | Standalone TSBK dumper for system discovery |
| `phase1a/tsbk.py` | TSBK parser (extracted from OP25, GPL v3) |
| `phase1a/settings.py` | All shared constants — frequencies, sample rates, endpoints |
| `phase1a/models.py` | Dataclasses: MetadataEvent, ActiveCall, TransmissionPacket |
| `phase1a/buffer_manager.py` | Per-talkgroup call lifecycle (open/close/timeout) |
| `phase1a/capture_backend.py` | Bridge-facing backend runtime |
| `zmq_bridge.py` | Correlates lane/TGID, repackages PCM |

## Hardware

- RTL-SDR Blog v4 (USB 2.0, max ~3.2 Msps)
- Target system: DeKalb County P25 Phase 1, control at 854.6125 MHz
- See `docs/notes/radio_info.md` for full system parameters

## Dependencies

- GNU Radio 3.10+ with gr-osmosdr
- gr-op25 / gr-op25_repeater (boatbod fork, compiled)
- OP25 `p25_demodulator.py` hierblock (referenced via sys.path from `~/clones/op25`)
- Python 3.13, pyzmq

## Docs

| Document | Description |
|----------|-------------|
| `docs/notes/phase1a_progress.md` | Session log — what was done, what's confirmed, next steps |
| `docs/notes/radio_info.md` | Target radio system parameters |
| `docs/modules/phase1a.md` | Capture pipeline spec — architecture, schemas, design decisions |
| `docs/modules/trm.md` | TRM design doc (downstream, not built here) |
| `docs/specs/albatross_base_system.md` | The Albatross pattern — five-stage pipeline design |
| `docs/specs/radio_pipeline_spec.md` | Full radio pipeline spec (capture through UI) |
