# SDRTrunk Integration Module — Architectural Design

---

## 1. Purpose

Convert SDRTrunk recording output into discrete `TransmissionPacket` objects ready for downstream ASR processing and TRM routing.

The lifecycle in one sentence: SDRTrunk writes a completed call as a recording with embedded metadata → the watcher detects it → metadata is extracted and normalized → a `TransmissionPacket` is assembled and persisted.

SDRTrunk handles call segmentation internally. By the time this module sees a file, the call is already complete. The ingestion model is file-based — there is no live signal stream, no call tracking loop, and no timeout mechanism.

---

## 2. How SDRTrunk Produces Output

SDRTrunk writes one completed audio recording per call segment, typically as MP3 by default or WAV if configured, with rich embedded metadata and a structured filename that can be used as a fallback when tags are incomplete.

Each file contains:

- **ID3 tags** — embedded metadata carrying talkgroup ID, source radio ID, frequency, system/site name, encryption status, and recording time metadata (`timestamp_start` required, `timestamp_end` when available)
- **Filename** — structured encoding of key metadata fields as a best-effort fallback only

**Filename patterns (observed examples — not a stable schema):**
```
20250108_192900CO_DTRS_Thorodin_T-Thorodin_CC__TO_9100.mp3
20231001_173024_SystemName-SiteName__TO_41003_FROM_1612266.mp3
```

Filenames include a timestamp prefix, system/site/channel identifiers, participant fields (TO/FROM), optional aliases, and sanitized special characters. The exact pattern varies across SDRTrunk versions and configurations. Filename parsing is best-effort only — it fills in fields the tags do not cover. If filename parsing fails for a required field, ingestion fails for that file; it does not silently produce incomplete packets.

**ID3 tags are authoritative.** Always read tags first. Fall back to filename only for fields absent from tags.

**Recording format:**
- V1 targets MP3 recordings. WAV support is out of scope for the first implementation.
- MP3 is the SDRTrunk default. If a WAV file is encountered, log it and skip — do not attempt ingestion.
- A `recording_format` field is carried on the packet for downstream consumers (ASR, preprocessing) that need to know the audio format.

**Encrypted calls:** SDRTrunk may still write a recording for encrypted calls. If encountered, the packet is ingested with `encrypted=True` and flagged for downstream handling. The `audio_path` is preserved.

---

## 3. Components

### 3.1 DirectoryWatcher

Responsibility:
- Monitor the SDRTrunk recordings directory for new recordings
- Hand completed recordings to the `FileIngester`

Input:
- Filesystem path to SDRTrunk recordings directory

Output:
- New recording paths passed to `FileIngester.ingest(path)`

Detection strategy:
- Poll directory at a fixed interval (default: 500ms)
- Only process files not modified within the last `FILE_STABLE_SECONDS` (default: 2s) to avoid reading files mid-write
- Track file state explicitly:
  - `pending` — detected, not yet ingested
  - `ingested` — successfully processed
  - `failed` — ingestion failed; eligible for retry up to `MAX_RETRIES`
- A file is only marked `ingested` after successful packet assembly and storage. Transient failures leave the file in `failed` state and do not permanently skip it.

Failure handling:
- Directory not found or unreadable: logged, watcher retries on next poll
- Individual file errors do not stop the watcher

---

### 3.2 FileIngester

Responsibility:
- Coordinate ingestion of a single completed recording
- Orchestrate metadata extraction and packet assembly

Input:
- File path to a completed SDRTrunk recording

Output:
- Returns success or failure to `DirectoryWatcher`
- On success: calls `PacketAssembler.finalize_from_file(signal)` and emits `PACKET_SAVED`

Failure handling:
- Unsupported format (e.g. WAV in V1): logged, recording marked `failed` in watcher state
- Metadata extraction failure: logged, failure metadata written to dead-letter directory as JSON, recording left in place, marked `failed`
- Does not propagate exceptions to `DirectoryWatcher`

---

### 3.3 MetadataExtractor

Responsibility:
- Extract call metadata from a SDRTrunk recording
- Produce a normalized `SdrTrunkSignal`

Input:
- File path to a SDRTrunk recording

Output:
```json
{
  "type": "SDRTRUNK_CALL",
  "timestamp_start": float,
  "timestamp_end": "float | null",
  "talkgroup_id": int,
  "source_ids": [int],
  "system_name": "string | null",
  "site_name": "string | null",
  "frequency": "float | null",
  "encrypted": bool,
  "recording_format": "string",
  "audio_path": "string",
  "raw": {}
}
```

Extraction strategy:
1. Read ID3 tags from the file
2. Fall back to filename parsing for any fields not present in tags
3. If both fail for a required field, raise an extraction error

Required fields: `talkgroup_id`, `timestamp_start`, `audio_path`

Optional fields: `timestamp_end`, `source_ids`, `frequency`, `system_name`, `site_name`, `encrypted`

`timestamp_end` note: V1 does not rely on end time from SDRTrunk output. Extract it if available in tags; otherwise store null. Do not derive it from audio duration in this module — that is a preprocessing concern.

Failure handling:
- Malformed or unreadable tags: logged, falls back to filename
- Filename unparseable for a required field: logged, raises extraction error to `FileIngester`

---

### 3.4 PacketAssembler

Responsibility:
- Convert a normalized `SdrTrunkSignal` into a persisted `TransmissionPacket`
- Insert record into database

SDRTrunk owns the audio file. This module does not write, move, rename, or delete it. The `audio_path` in the packet points to the existing file.

Input:
- `SdrTrunkSignal` (normalized metadata + audio file path)

Output:

`TransmissionPacket` extends the base `Packet` class. All packets stored to the database conform to that base schema. `TransmissionPacket` adds radio-specific fields on top of it.

```
TransmissionPacket(Packet) {
  # --- base Packet fields ---
  packet_id,          # generated UUID
  packet_type,        # "transmission"
  timestamp,          # maps to timestamp_start
  source,             # maps to talkgroup_id + source_ids
  metadata,           # includes recording_format, system_name, site_name, raw tags
  payload,

  # --- TransmissionPacket-specific fields ---
  transmission_id,    # same as packet_id
  timestamp_start,
  timestamp_end,      # null if not available from SDRTrunk output
  talkgroup_id,
  source_ids,
  frequency,
  encrypted,
  recording_format,   # "mp3" | "wav" — for downstream ASR/preprocessing
  audio_path          # path to existing SDRTrunk recording
}
```

Failure handling:
- DB insert failure: logged, packet written to dead-letter JSON file, exception raised

---

### 3.5 EventBus

Responsibility:
- Dispatch internal events to registered listeners in-process

Model:
- Synchronous, in-process pub/sub
- No threading or async behavior in V1

Structure:

```python
subscribers = {
  "PACKET_SAVED": [fn]
}
```

Interface:

```python
subscribe(event_type: str, handler: Callable) -> None
emit(event_type: str, data: dict) -> None
```

Execution semantics:
- `emit()` calls all handlers sequentially in the caller's thread
- Handlers must be non-blocking

Constraints:
- EventBus is not a queue and not durable
- It must not be used as a source of truth

---

## 4. Data Structures

### 4.1 SdrTrunkSignal

```
SdrTrunkSignal {
  type:             "SDRTRUNK_CALL"
  timestamp_start:  float          # Unix timestamp from ID3 tags or filename
  timestamp_end:    float | None   # from ID3 tags if available; otherwise null
  talkgroup_id:     int
  source_ids:       list[int]      # may be empty if absent from metadata
  system_name:      str | None
  site_name:        str | None
  frequency:        float | None
  encrypted:        bool
  recording_format: str            # "mp3" in V1
  audio_path:       str            # absolute path to the recording
  raw:              dict           # all extracted ID3 tags and filename fields
}
```

### 4.2 FileState

```
FileState {
  path:          Path
  status:        "pending" | "ingested" | "failed"
  attempt_count: int
  last_attempt:  float | None
}
```

---

## 5. Lifecycle Sequence

### 5.1 Signal Flow

```
SDRTrunk writes recording
  → DirectoryWatcher detects recording
  → DirectoryWatcher marks recording pending, calls FileIngester.ingest(path)
  → MetadataExtractor.extract(path) → SdrTrunkSignal
  → PacketAssembler.finalize_from_file(signal) → TransmissionPacket
  → Storage.insert_packet(packet)
  → FileIngester returns success to DirectoryWatcher
  → DirectoryWatcher marks recording ingested
  → FileIngester emits PACKET_SAVED via EventBus
```

### 5.2 File Detection Sequence

1. `DirectoryWatcher` polls recordings directory at `POLL_INTERVAL_MS`
2. For each MP3 recording not in `ingested` state:
   - Skip if modified within `FILE_STABLE_SECONDS`
   - Skip if in `failed` state and `attempt_count >= MAX_RETRIES`
   - Mark as `pending`, call `FileIngester.ingest(path)`
   - On success: mark `ingested`
   - On failure: mark `failed`, increment `attempt_count`

### 5.3 Ingestion Sequence

1. `FileIngester` checks format — skip non-MP3 recordings in V1
2. Calls `MetadataExtractor.extract(path)`
3. Extractor reads ID3 tags; falls back to filename parsing for missing fields
4. Returns normalized `SdrTrunkSignal`
5. `FileIngester` calls `PacketAssembler.finalize_from_file(signal)`
6. Assembler builds `TransmissionPacket` with `audio_path` pointing to existing recording
7. Assembler calls `Storage.insert_packet(packet)`
8. `FileIngester` returns success to `DirectoryWatcher`
9. `DirectoryWatcher` marks recording `ingested`
10. `FileIngester` emits `PACKET_SAVED` via `EventBus`

---

## 6. Interfaces

### 6.1 DirectoryWatcher → FileIngester

```python
ingest(path: Path) -> None
```

### 6.2 FileIngester → MetadataExtractor

```python
extract(path: Path) -> SdrTrunkSignal
```

### 6.3 FileIngester → PacketAssembler

```python
finalize_from_file(signal: SdrTrunkSignal) -> TransmissionPacket
```

### 6.4 PacketAssembler → Storage

```python
insert_packet(packet: TransmissionPacket) -> None
```

---

## 7. Configuration

```
POLL_INTERVAL_MS      = 500
FILE_STABLE_SECONDS   = 2
MAX_RETRIES           = 3
RECORDINGS_DIR        = "/path/to/SDRTrunk/recordings"
DEAD_LETTER_DIR       = "./failed_ingestions"
```

---

## 8. Constraints

- SDRTrunk owns the audio files — this module does not move, rename, or delete them
- V1 supports MP3 only — WAV files are logged and skipped
- `timestamp_end` is stored if available in tags; null otherwise — not derived in this module
- Encrypted calls are ingested and flagged — handling is a downstream concern
- No pre-roll, post-roll, or call boundary logic — SDRTrunk handles all segmentation
- Filename parsing is best-effort only — never the primary metadata source

---

## 9. Non-Goals

- ASR processing
- Thread/event routing (TRM)
- Audio format conversion (MP3 → WAV is a preprocessing concern)
- Real-time streaming or live signal handling
- Managing or cleaning up SDRTrunk's recordings directory
- WAV ingestion (V1)

---

## 10. Result

A file-based ingestion module that watches a SDRTrunk recordings directory, extracts call metadata from completed recordings, and produces `TransmissionPacket` objects conforming to the base `Packet` schema — ready for downstream ASR processing and TRM routing.