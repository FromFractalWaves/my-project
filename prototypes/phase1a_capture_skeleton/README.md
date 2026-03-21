# Phase 1A Capture Skeleton

Minimal Python skeleton for the Phase 1A capture backend.

Scope:
- receive PCM and metadata on separate ZMQ lanes
- manage per-talkgroup buffers
- detect boundaries via metadata and inactivity timeout
- write WAV files
- emit `TransmissionPacket`

This is intentionally a scaffold, not a full GNU Radio or gr-op25 integration.
Real wire formats, exact metadata fields, and call-boundary policy still need to be confirmed against live data.
