# Phase 1A Capture Skeleton (flowgraph + bridge aligned)

This skeleton is built around the current multi-lane GNU Radio flowgraph and the ZMQ bridge.

Runtime shape:

```text
RTL-SDR -> flowgraph.py
       -> metadata ZMQ PUSH (json bytes)
       -> per-lane PCM ZMQ PUSH (raw int16)
flowgraph -> zmq_bridge.py
         -> backend control ZMQ PUSH (json bytes)
         -> backend PCM ZMQ PUSH (multipart: header json + raw PCM)
zmq_bridge -> phase1a.run_backend
          -> WAV files + TransmissionPacket JSONL
```

## Project layout

- `flowgraph.py` — GNU Radio / gr-op25 multi-lane capture flowgraph scaffold
- `zmq_bridge.py` — correlates lane ownership and repackages PCM into multipart frames
- `phase1a/settings.py` — shared constants and endpoint helpers
- `phase1a/models.py` — metadata, call, and packet dataclasses
- `phase1a/buffer_manager.py` — per-talkgroup active call manager with explicit release + inactivity timeout
- `phase1a/wav_writer.py` — writes mono int16 WAV files
- `phase1a/packet_builder.py` — constructs `TransmissionPacket`
- `phase1a/packet_emitter.py` — simple JSONL / stdout sinks
- `phase1a/capture_backend.py` — bridge-facing backend runtime
- `phase1a/run_backend.py` — CLI entrypoint for the backend
- `tests/` — unit tests for buffer management, WAV writing, and packet building

## Important notes

- This is a skeleton, not a finished receiver.
- The GNU Radio files are syntax scaffolds; they are not executed in the test suite.
- The exact OP25 metadata field inventory and cadence still need live validation.
- The bridge now forwards **both** control metadata and lane-tagged PCM to the backend.
- `lane_id` is injected into forwarded metadata by the flowgraph poller so the bridge can correlate grants with PCM lanes.

## Suggested bring-up order

1. Run `flowgraph.py`
2. Run `zmq_bridge.py`
3. Run `python -m phase1a.run_backend`
4. Inspect stdout and `out/packets.jsonl`
5. Tune metadata field mapping against real `[meta]` traffic

## External dependencies

The backend/bridge side uses standard Python + `pyzmq`.

The flowgraph additionally requires:
- GNU Radio
- osmosdr
- OP25 / gr-op25 / gr-op25_repeater blocks available in the Python environment
