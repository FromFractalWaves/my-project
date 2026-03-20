# OP25 Integration Module — Architectural Design

---

## 1. Purpose

Convert OP25 output (UDP JSON + audio stream) into discrete `TransmissionPacket` objects with associated audio files and metadata.

The lifecycle in one sentence: OP25 emits signals → the tracker maintains active calls → completed calls become packets → packets are handed downstream for ASR and TRM routing.

---

## 2. Components

### 2.1 JSONListener

Responsibility:
- Receive UDP JSON from OP25 and normalize it into an `Op25Signal`
- Pass the signal directly to `CallTracker`

Input:
- UDP packets (JSON)

Output:

```json
{
  "type": "OP25_SIGNAL",
  "timestamp": float,
  "talkgroup_id": int,
  "source_id": int,
  "frequency": float,
  "encrypted": bool,
  "raw": {}
}
```

Failure handling:
- Malformed or unparseable JSON is logged and dropped
- Does not propagate exceptions to the audio path

---

### 2.2 AudioBuffer

Responsibility:
- Maintain rolling buffer of recent audio samples

AudioBuffer is the single source of audio. CallTracker references or slices from it but does not own raw audio ingestion.

Input:
- Continuous audio stream (PCM chunks)

Output:
- `get_last(seconds)` → audio segment
- `append(chunk)` → store new audio

Constraints:
- Fixed duration (e.g. 10 seconds)
- FIFO eviction

Failure handling:
- If the buffer is empty when `get_last()` is called, returns an empty segment — call starts with no pre-roll rather than failing

---

### 2.3 CallTracker

Responsibility:
- Track active transmissions
- Manage lifecycle (start → active → end)

Input:
- `Op25Signal` (from JSONListener)
- Audio chunks (from AudioBuffer)

Output (via EventBus):
- `CALL_STARTED`
- `CALL_UPDATED`
- `CALL_ENDED`

On `CALL_ENDED`: invokes `PacketAssembler.finalize_call()` directly. This is an internal handoff, not a bus notification.

**`active_calls` structure:** `dict[talkgroup_id → list[CallState]]`

V1 always has at most one item per list. Call resolution goes through `resolve_call(signal)` — in V1 this returns the first item or `None`. This isolates the selection logic so V2 can handle overlapping calls on the same talkgroup without restructuring the data or refactoring call sites.

Failure handling:
- If `PacketAssembler.finalize_call()` throws, the call is removed from `active_calls` and the error is logged — ingestion continues

---

### 2.4 PacketAssembler

Responsibility:
- Convert a completed `CallState` into a persisted `TransmissionPacket`
- Write audio file to disk
- Insert record into database

Input:

```json
{
  "transmission_id": "UUID",
  "talkgroup_id": int,
  "source_ids": [],
  "start_time": float,
  "end_time": float,
  "audio": "binary",
  "metadata": {}
}
```

Output:

`TransmissionPacket` extends the base `Packet` class defined in `decisions/modular_data_ingestion.md`. All packets stored to the database must conform to that base schema. `TransmissionPacket` adds radio-specific fields on top of it.

```
TransmissionPacket(Packet) {
  # --- base Packet fields ---
  packet_id,          # maps to transmission_id
  packet_type,        # "transmission"
  timestamp,          # maps to timestamp_start
  source,             # maps to talkgroup_id + source_ids
  metadata,

  # --- TransmissionPacket-specific fields ---
  transmission_id,
  timestamp_start,
  timestamp_end,
  talkgroup_id,
  source_ids,
  frequency,
  encrypted,
  audio_path
}
```

Failure handling:
- Audio write failure: logged, `audio_path` set to null, DB insert proceeds with flag
- DB insert failure: logged, packet is not lost — retry or dead-letter queue to be defined in V2

---

### 2.5 EventBus

Responsibility:
- Dispatch internal events to registered listeners in-process

Model:
- Synchronous, in-process pub/sub
- No threading or async behavior in V1

Structure:

```python
subscribers = {
  "CALL_STARTED":  [fn, fn],
  "CALL_UPDATED":  [fn],
  "CALL_ENDED":    [fn],
  "PACKET_SAVED":  [fn]
}
```

Interface:

```python
subscribe(event_type: str, handler: Callable) -> None
emit(event_type: str, data: dict) -> None
```

Execution semantics:
- `emit()` calls all handlers for the event type sequentially
- Handlers execute in the same thread as the caller
- No guarantees of isolation — handlers must be non-blocking

Constraints:
- EventBus is not a queue and not durable
- It must not be used as a source of truth

---

## 3. Interfaces

### 3.1 JSONListener → CallTracker

```python
handle_signal(signal: Op25Signal) -> None
```

### 3.2 AudioBuffer → CallTracker

```python
get_last(seconds: float) -> AudioSegment
append(chunk: AudioChunk) -> None
```

### 3.3 CallTracker → PacketAssembler

```python
finalize_call(call_state: CallState) -> None
```

### 3.4 PacketAssembler → Storage

```python
save_audio(audio: AudioSegment, path: str) -> None
insert_packet(packet: TransmissionPacket) -> None
```

### 3.5 CallTracker — Internal

```python
resolve_call(signal: Op25Signal) -> CallState | None
```

Returns the active `CallState` for the signal's talkgroup, or `None` if no active call exists. In V1, always returns `active_calls[tgid][0]` or `None`. In future versions, this method encapsulates selection logic for overlapping calls. Selection may consider `talkgroup_id`, `source_id`, `frequency`, and recency.

### 3.6 CallTracker — Lifecycle

```python
end_call(call: CallState) -> None
```

Centralizes call termination logic. Called by both the timeout poller and any explicit end signal. Responsible for post-roll append, removal from `active_calls`, emitting `CALL_ENDED`, and handing off to `PacketAssembler`.

### 4.1 CallState

```
CallState {
  transmission_id:    UUID
  talkgroup_id:       int
  start_time:         float
  last_activity_time: float
  source_ids:         set[int]
  frequency:          float
  encrypted:          bool
  audio_segments:     list
}
```

### 4.2 active_calls

```python
active_calls: dict[int, list[CallState]]
# key: talkgroup_id
# value: list of active CallState objects for that talkgroup
# V1: list always has 0 or 1 items
```

---

## 5. Lifecycle Sequence

### 5.1 Signal Flow

```
UDP JSON → JSONListener → CallTracker
Audio    → AudioBuffer  → CallTracker

CallTracker → EventBus  (CALL_STARTED / CALL_UPDATED / CALL_ENDED)
CallTracker → PacketAssembler
PacketAssembler → EventBus  (PACKET_SAVED)
```

### 5.2 Call Start Sequence

1. JSONListener receives UDP packet, parses it into an `Op25Signal`, calls `CallTracker.handle_signal(signal)` directly
2. CallTracker calls `resolve_call(signal)` — returns `None` (no active call)
3. Create `CallState`; initialize `last_activity_time = now`
4. Append to `active_calls[tgid]`
5. Pull pre-roll:
```python
audio = AudioBuffer.get_last(2-3 seconds)
```
6. Emit `CALL_STARTED`

---

### 5.3 Call Active Sequence

1. CallTracker calls `resolve_call(signal)` — returns existing `CallState`
2. Append audio chunks continuously
3. Update:
```python
last_activity_time = now
source_ids.add(source_id)
```
4. Emit `CALL_UPDATED`

---

### 5.4 Call End Sequence

Decision point — timeout poller calls `resolve_call` or iterates `active_calls` directly:
```python
if now - call.last_activity_time > CALL_END_TIMEOUT:
```

Actions:
1. Append post-roll audio
2. Remove `CallState` from `active_calls[tgid]`
3. Emit `CALL_ENDED`
4. Pass `CallState` to `PacketAssembler`

---

### 5.5 Packet Finalization

1. Write audio file:
```
/recordings/YYYY/MM/DD/{uuid}.wav
```
2. Create `TransmissionPacket` (conforming to base `Packet`)
3. Insert into database
4. Emit `PACKET_SAVED`

---

### 5.6 Timeout Mechanism

Responsibility:
- Detect inactive calls and trigger call termination

Implementation:
- Background polling loop running at fixed interval

```python
POLL_INTERVAL_MS = 100
```

Loop behavior:

```python
while True:
    now = current_time()

    for call_list in active_calls.values():
        for call in call_list:
            if now - call.last_activity_time > CALL_END_TIMEOUT:
                end_call(call)

    sleep(POLL_INTERVAL_MS)
```

Constraints:
- Runs in same process as CallTracker
- Must be lightweight and non-blocking
- Does not spawn per-call timers

Rationale:
- Avoids complexity of per-call scheduling
- Ensures deterministic and centralized timeout handling

---

## 6. Decision Points

### 6.1 Call Start Detection

```python
call = resolve_call(signal)
if call is None:
    start_call(signal)
```

### 6.2 Call Continuation

```python
call = resolve_call(signal)
if call is not None:
    update_call(call, signal)
```

### 6.3 Call End Detection

```python
if now - call.last_activity_time > CALL_END_TIMEOUT:
    end_call(call)
```

### 6.4 Encrypted Handling

```python
if encrypted:
    mark_call_encrypted()
```

Audio may still be recorded but flagged.

---

## 7. Configuration

```
CALL_END_TIMEOUT_MS  = 800–1500
AUDIO_BUFFER_SECONDS = 10
PRE_ROLL_SECONDS     = 2–3
POST_ROLL_SECONDS    = 1
POLL_INTERVAL_MS     = 100
```

---

## 8. Constraints

- Audio is authoritative
- JSON drives segmentation only
- System must be non-blocking
- Failures must not stop ingestion

---

## 9. Non-Goals

- ASR processing
- Thread/event routing (TRM)
- UI formatting
- Cross-transmission aggregation

---

## 10. Result

A deterministic ingestion module that converts OP25 output into structured `TransmissionPacket` objects (conforming to the base `Packet` schema) with audio, ready for downstream ASR processing and TRM routing.