# OP25 Integration Skeleton

A minimal Python skeleton matching the OP25 Integration Module architectural design.

## Package layout

```text
op25_integration_skeleton/
  pyproject.toml
  README.md
  src/op25_integration/
    __init__.py
    config.py
    models.py
    audio_buffer.py
    event_bus.py
    storage.py
    packet_assembler.py
    call_tracker.py
    json_listener.py
    main.py
```

## Notes

- This skeleton keeps the storage model as `dict[talkgroup_id, list[CallState]]`.
- `resolve_call(signal)` is the abstraction point for future overlapping-call logic.
- Timeouts are configured in milliseconds and compared in seconds internally.
- `AudioBuffer` is the authoritative rolling store; `CallTracker` only attaches chunk references to active calls.
- The default audio routing here is intentionally simple. If your upstream can attribute audio to a specific call or channel, replace `handle_audio_chunk()` with targeted routing.

## Quick start

```bash
cd op25_integration_skeleton
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m op25_integration.main
```

The demo in `main.py` is synthetic and just shows object wiring plus a tiny end-to-end flow.
