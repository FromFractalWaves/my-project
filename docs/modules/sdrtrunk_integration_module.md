# SDRTrunk Integration Module — Architectural Design

---

## 1. Purpose

Convert SDRTrunk recording output into discrete `TransmissionPacket` objects ready for downstream ASR processing and TRM routing.

The lifecycle in one sentence: SDRTrunk writes a completed call as an MP3 file with embedded metadata → the watcher detects it → metadata is extracted and normalized → a `TransmissionPacket` is assembled and persisted.

**Key difference from the OP25 module:** SDRTrunk handles call segmentation internally. There is no live signal stream, no `CallTracker`, no `AudioBuffer`, and no timeout loop. By the time this module sees a file, the call is already complete. The ingestion model is file-based, not stream-based.

---

## 2. How SDRTrunk Produces Output

SDRTrunk writes one MP3 file per completed call. Each file contains:

- **Filename metadata** — structured filename encoding timestamp, system/site name, talkgroup ID, and source radio ID
- **ID3 tags** — embedded MP3 tags carrying the same metadata more reliably

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

**ID3 tags are authoritative.** The filename is a fallback for fields the tags do not cover (e.g. system/site name). Parsers should read tags first and fall back to filename parsing for missing fields.

**Encrypted calls:** SDRTrunk does not produce audio files for encrypted calls by default. If an encrypted call file is encountered (flagged via ID3 tag or filename convention), it should be ingested as a packet with `encrypted=True` and `audio_path` set to the file path, but flagged for downstream handling.

**Audio format:** MP3, 8 kHz sample rate, mono, 16-bit source samples.

---

## 3. Components

### 3.1 DirectoryWatcher

Responsibility:
- Monitor the SDRTrunk recordings directory for new MP3 files
- Detect completed call files and hand them to the `FileIngester`

Input:
- Filesystem path to SDRTrunk recordings directory

Output:
- New file paths passed to `FileIngester.ingest(path)`

Detection strategy:
- Poll the directory at a fixed interval (default: 500ms)
- Track already-seen files to avoid double-ingestion
- Only process files that have not been modified in the last N seconds (default: 2s) to avoid reading files mid-write

Failure handling:
- Directory not found or unreadable: logged, watcher retries on next poll
- Individual file errors do not stop the watcher

---

### 3.2 FileIngester

Responsibility:
- Coordinate the ingestion of a single completed call file
- Orchestrate metadata extraction and packet assembly

Input:
- File path to a completed SDRTrunk MP3 recording

Output:
- Calls `PacketAssembler.finalize_from_file(path, metadata)` with normalized metadata

Failure handling:
- Metadata extraction failure: logged, file is moved to a dead-letter directory and skipped
- Does not propagate exceptions to the `DirectoryWatcher`

---

### 3.3 MetadataExtractor

Responsibility:
- Extract call metadata from a SDRTrunk MP3 file
- Produce a normalized `SdrTrunkSignal` object

Input:
- File path to MP3

Output:
```json
{
  "type": "SDRTRUNK_CALL",
  "timestamp_start": float,
  "talkgroup_id": int,
  "source_ids": [int],
  "system_name": "string",
  "site_name": "string",
  "frequency": float | null,
  "encrypted": bool,
  "audio_path": "string",
  "raw": {}
}
```

Extraction strategy:
1. Attempt to read ID3 tags from the MP3 file
2. Fall back to filename parsing for any fields not present in tags
3. If both fail for a required field (`talkgroup_id`, `timestamp_start`), raise an extraction error

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

This component is **shared with the OP25 integration module**. The `finalize_from_file` path is an additional entry point that accepts pre-extracted metadata and an existing audio file path rather than raw audio bytes.

Input:
- `SdrTrunkSignal` (normalized metadata)
- Audio file path (already written by SDRTrunk — no audio write step needed)

Output:
- `TransmissionPacket` conforming to base `Packet` schema

```
TransmissionPacket(Packet) {
  # --- base Packet fields ---
  packet_id,          # maps to transmission_id (generated UUID)
  packet_type,        # "transmission"
  timestamp,          # maps to timestamp_start
  source,             # maps to talkgroup_id + source_ids
  metadata,

  # --- TransmissionPacket-specific fields ---
  transmission_id,
  timestamp_start,
  timestamp_end,      # null — SDRTrunk filenames encode start time only
  talkgroup_id,
  source_ids,
  frequency,
  encrypted,
  audio_path          # path to the existing SDRTrunk MP3 file
}
```

Failure handling:
- DB insert failure: logged, packet written to dead-letter JSON file, exception raised

---

### 3.5 EventBus

Identical to the OP25 module. Synchronous in-process pub/sub. Emits `PACKET_SAVED` on successful ingestion.

---

## 4. Interfaces

### 4.1 DirectoryWatcher → FileIngester

```python
ingest(path: Path) -> None
```

### 4.2 FileIngester → MetadataExtractor

```python
extract(path: Path) -> SdrTrunkSignal
```

### 4.3 FileIngester → PacketAssembler

```python
finalize_from_file(signal: SdrTrunkSignal) -> TransmissionPacket
```

### 4.4 PacketAssembler → Storage

```python
insert_packet(packet: TransmissionPacket) -> None
```

---

## 5. Data Structures

### 5.1 SdrTrunkSignal

```
SdrTrunkSignal {
  type:             "SDRTRUNK_CALL"
  timestamp_start:  float          # Unix timestamp parsed from filename or ID3
  talkgroup_id:     int
  source_ids:       list[int]      # may be empty if not present in metadata
  system_name:      str | None
  site_name:        str | None
  frequency:        float | None
  encrypted:        bool
  audio_path:       str            # absolute path to the MP3 file
  raw:              dict           # all extracted ID3 tags and filename fields
}
```

---

## 6. Lifecycle Sequence

### 6.1 Signal Flow

```
SDRTrunk writes MP3 → DirectoryWatcher detects file
                    → FileIngester.ingest(path)
                    → MetadataExtractor.extract(path) → SdrTrunkSignal
                    → PacketAssembler.finalize_from_file(signal)
                    → Storage.insert_packet(packet)
                    → EventBus.emit(PACKET_SAVED)
```

### 6.2 File Detection Sequence

1. `DirectoryWatcher` polls recordings directory at `POLL_INTERVAL_MS`
2. For each MP3 file not in the seen set:
   - Check `last_modified` — skip if modified within `FILE_STABLE_SECONDS`
   - Add to seen set
   - Call `FileIngester.ingest(path)`

### 6.3 Ingestion Sequence

1. `FileIngester` calls `MetadataExtractor.extract(path)`
2. Extractor reads ID3 tags; falls back to filename parsing for missing fields
3. Returns normalized `SdrTrunkSignal`
4. `FileIngester` calls `PacketAssembler.finalize_from_file(signal)`
5. Assembler builds `TransmissionPacket` with `audio_path` pointing to existing MP3
6. Assembler calls `Storage.insert_packet(packet)`
7. EventBus emits `PACKET_SAVED`

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
- `timestamp_end` is not available from SDRTrunk filename or standard ID3 tags — set to null
- Encrypted call handling deferred to downstream — ingested as packet, flagged
- No pre-roll or post-roll — call boundaries are determined by SDRTrunk

---

## 9. Non-Goals

- ASR processing
- Thread/event routing (TRM)
- Audio format conversion (MP3 → WAV for ASR is a preprocessing concern)
- Real-time streaming or live signal handling
- Managing or cleaning up SDRTrunk's recordings directory

---

## 10. Comparison to OP25 Module

| Concern | OP25 Module | SDRTrunk Module |
|---|---|---|
| Call segmentation | `CallTracker` + timeout loop | SDRTrunk (external) |
| Audio buffering | `AudioBuffer` (rolling PCM) | Not needed |
| Signal ingestion | UDP JSON stream → `JSONListener` | Completed file → `DirectoryWatcher` |
| Metadata source | Live UDP JSON events | ID3 tags + filename |
| Audio format | WAV (written by module) | MP3 (written by SDRTrunk) |
| `timestamp_end` | Available | Not available |
| Packet assembly | `PacketAssembler.finalize_call()` | `PacketAssembler.finalize_from_file()` |

The `PacketAssembler`, `StorageBackend`, `TransmissionPacket`, and `EventBus` are shared between both modules. Only the ingestion side differs.

---

## 11. Result

A file-based ingestion module that watches a SDRTrunk recordings directory, extracts call metadata from MP3 files, and produces `TransmissionPacket` objects conforming to the base `Packet` schema — ready for downstream ASR processing and TRM routing.