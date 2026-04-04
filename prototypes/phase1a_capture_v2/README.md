# Phase 1A Capture — P25 Trunked Radio

Captures P25 Phase 1 trunked radio transmissions and emits structured TransmissionPackets (WAV + metadata).

## Status

The flowgraph has been validated against live RF. Control channel decoding, TSBK parsing, and voice lane assignment are working. The bridge and backend have not yet been tested against live data.

## Runtime shape

```text
RTL-SDR -> flowgraph.py
       -> raw TSBK bytes (msg_queue, decoded by tsbk.py)
       -> per-lane PCM ZMQ PUSH (raw int16)
flowgraph -> zmq_bridge.py
         -> backend control ZMQ PUSH (json bytes)
         -> backend PCM ZMQ PUSH (multipart: header json + raw PCM)
zmq_bridge -> phase1a.run_backend
          -> WAV files + TransmissionPacket JSONL
```

## Project layout

- `flowgraph.py` — GNU Radio multi-lane capture flowgraph (validated against live RF)
- `zmq_bridge.py` — correlates lane ownership and repackages PCM into multipart frames
- `tsbk_dump.py` — standalone TSBK dumper for system discovery
- `phase1a/settings.py` — shared constants and endpoint helpers
- `phase1a/models.py` — metadata, call, and packet dataclasses
- `phase1a/tsbk.py` — standalone TSBK parser extracted from OP25 (GPL v3)
- `phase1a/buffer_manager.py` — per-talkgroup active call manager with explicit release + inactivity timeout
- `phase1a/wav_writer.py` — writes mono int16 WAV files
- `phase1a/packet_builder.py` — constructs `TransmissionPacket`
- `phase1a/packet_emitter.py` — simple JSONL / stdout sinks
- `phase1a/capture_backend.py` — bridge-facing backend runtime
- `phase1a/run_backend.py` — CLI entrypoint for the backend
- `tests/` — unit tests for buffer management, WAV writing, and packet building

## Bring-up order

```bash
export PYTHONPATH=/usr/local/lib/python3.13/dist-packages:$PYTHONPATH

# Terminal 1
python flowgraph.py

# Terminal 2
python zmq_bridge.py

# Terminal 3
python -m phase1a.run_backend
```

Inspect stdout and `out/packets.jsonl`. WAV files land in `out/wav/`.

## External dependencies

The backend/bridge side uses standard Python + `pyzmq`.

The flowgraph additionally requires:
- GNU Radio 3.10+
- gr-osmosdr
- gr-op25 / gr-op25_repeater blocks (boatbod fork, compiled and installed)
- OP25 `p25_demodulator.py` hierblock (referenced via sys.path from the OP25 clone)
