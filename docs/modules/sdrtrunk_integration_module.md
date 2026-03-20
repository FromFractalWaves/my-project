# SDRTrunk Integration Module — Architectural Design

---

## 1. Purpose

Convert SDRTrunk recording output into discrete `TransmissionPacket` objects ready for downstream ASR processing and TRM routing.

The lifecycle in one sentence: SDRTrunk writes a completed call as an MP3 file with embedded metadata → the watcher detects it → metadata is extracted and normalized → a `TransmissionPacket` is assembled and persisted.

SDRTrunk handles call segmentation internally. By the time this module sees a file, the call is already complete. The ingestion model is file-based — there is no live signal stream, no call tracking loop, and no timeout mechanism.

---

## 2. How SDRTrunk Produces Output

SDRTrunk writes one MP3 file per completed call. Each file contains:

- **ID3 tags** — embedded metadata carrying talkgroup ID, source radio ID, frequency, system/site name, and encryption status
- **Filename** — structured encoding of the same metadata as a fallback

**Filename format:**
```
20231001_173024_SystemName-SiteName__TO_41003_FROM_1612266.mp3
```

Fields:
| Field | Example | Notes |
|---|---|---|
| Timestamp | `20231001_173024` | Local time, `YYYYMMDD_HHMMSS` |
| System-Site | `SystemName-SiteName` | SDRTrunk playlist names |
| Talkgroup ID | `TO_41003` | Integer |
| Source radio ID | `FROM_1612266` | Integer, absent on some calls |

**ID3 tags are authoritative.** The filename is a fallback for any fields the tags do not cover. Parsers read tags first and fall back to filename parsing for missing fields.

**Audio format:** MP3, 8 kHz sample rate, mono.

**Encrypted calls:** SDRTrunk may still write a file for encrypted calls. If encountered, the packet is ingested with `encrypted=True` and flagged for downstream handling. Audio path is preserved.

---

## 3. Components

### 3.1 DirectoryWatcher

Responsibility:
- Monitor the SDRTrunk recordings directory for new MP3 files
- Hand completed call files to the `FileIngester`

Input:
- Filesystem path to SDRTrunk recordings directory

Output:
- New file paths passed to `FileIngester.ingest(path)`

Detection strategy:
- Poll directory at a fixed interval (default: 500ms)
- Track already-seen files to prevent double-ingestion
- Only process files not modified within the last `FILE_STABLE_SECONDS` (default: 2s) to avoid reading files mid-write

Failure handling:
- Directory not found or unreadable: logged, watcher retries on next poll
- Individual file errors do not stop the watcher

---

### 3.2 FileIngester

Responsibility:
- Coordinate ingestion of a single completed call file
- Orchestrate metadata extraction and packet assembly

Input:
- File path to a completed SDRTrunk MP3 recording

Output:
- Calls `PacketAssembler.finalize_from_file(signal)` with normalized metadata

Failure handling:
- Metadata extraction failure: logged, file moved to dead-letter directory, skipped
- Does not propagate exceptions to `DirectoryWatcher`

---

### 3.3 MetadataExtractor

Responsibility:
- Extract call metadata from a SDRTrunk MP3 file
- Produce a normalized `SdrTrunkSignal`

Input:
- File path to MP3

Output:
```json
{
  "type": "SDRTRUNK_CALL",
  "timestamp_start": float,
  "talkgroup_id": int,
  "source_ids": [int],
  "system_name": "string | null",
  "site_name": "string | null",
  "frequency": "float | null",
  "encrypted": bool,
  "audio_path": "string",
  "raw": {}
}
```

Extraction strategy:
1. Read ID3 tags from the MP3 file
2. Fall back to filename parsing for any fields not present in tags
3. If both fail for a required field, raise an extraction error

Required fields: `talkgroup_id`, `timestamp_start`, `audio_path`

Optional fields: `source_ids`, `frequency`, `system_name`, `site_name`, `encrypted`

Failure handling:
- Malformed or unreadable tags: logged, falls back to filename
- Filename unparseable: logged, raises extraction error to `FileIngester`

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
  metadata,
  payload,

  # --- TransmissionPacket-specific fields ---
  transmission_id,    # same as packet_id
  timestamp_start,
  timestamp_end,      # null — not available from SDRTrunk output
  talkgroup_id,
  source_ids,
  frequency,
  encrypted,
  audio_path          # path to existing SDRTrunk MP3 file
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
  timestamp_start:  float          # Unix timestamp parsed from ID3 or filename
  talkgroup_id:     int
  source_ids:       list[int]      # may be empty if absent from metadata
  system_name:      str | None
  site_name:        str | None
  frequency:        float | None
  encrypted:        bool
  audio_path:       str            # absolute path to the MP3 file
  raw:              dict           # all extracted ID3 tags and filename fields
}
```

---

## 5. Lifecycle Sequence

### 5.1 Signal Flow

```
SDRTrunk writes MP3
  → DirectoryWatcher detects file
  → FileIngester.ingest(path)
  → MetadataExtractor.extract(path) → SdrTrunkSignal
  → PacketAssembler.finalize_from_file(signal) → TransmissionPacket
  → Storage.insert_packet(packet)
  → EventBus.emit(PACKET_SAVED)
```

### 5.2 File Detection Sequence

1. `DirectoryWatcher` polls recordings directory at `POLL_INTERVAL_MS`
2. For each MP3 file not in the seen set:
   - Check `last_modified` — skip if modified within `FILE_STABLE_SECONDS`
   - Add to seen set
   - Call `FileIngester.ingest(path)`

### 5.3 Ingestion Sequence

1. `FileIngester` calls `MetadataExtractor.extract(path)`
2. Extractor reads ID3 tags; falls back to filename parsing for missing fields
3. Returns normalized `SdrTrunkSignal`
4. `FileIngester` calls `PacketAssembler.finalize_from_file(signal)`
5. Assembler builds `TransmissionPacket` with `audio_path` pointing to existing MP3
6. Assembler calls `Storage.insert_packet(packet)`
7. `EventBus` emits `PACKET_SAVED`

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
RECORDINGS_DIR        = "/path/to/SDRTrunk/recordings"
DEAD_LETTER_DIR       = "./failed_ingestions"
```

---

## 8. Constraints

- SDRTrunk owns the audio files — this module does not move, rename, or delete them
- `timestamp_end` is not available from SDRTrunk output — set to null
- Encrypted calls are ingested and flagged — handling is a downstream concern
- No pre-roll, post-roll, or call boundary logic — SDRTrunk handles all segmentation

---

## 9. Non-Goals

- ASR processing
- Thread/event routing (TRM)
- Audio format conversion (MP3 → WAV is a preprocessing concern)
- Real-time streaming or live signal handling
- Managing or cleaning up SDRTrunk's recordings directory

---

## 10. Result

A file-based ingestion module that watches a SDRTrunk recordings directory, extracts call metadata from completed MP3 files, and produces `TransmissionPacket` objects conforming to the base `Packet` schema — ready for downstream ASR processing and TRM routing.