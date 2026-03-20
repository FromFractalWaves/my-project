# Radio Event Intelligence Pipeline

*Version 3 — Updated with Thread Routing Module*

*This version supersedes Version 2. The Thread Routing Module (TRM) is now the canonical intelligence layer for conversation grouping and event correlation.*

---

## 1. Objective

Design and build a system that converts real-time radio transmissions into structured, queryable, and summarized information.

The system will:

- Capture radio transmissions and metadata
- Store raw audio and associated identifiers
- Run multiple ASR passes on each transmission for accuracy
- Route each transmission into conversation threads and correlate with real-world events in real time via the Thread Routing Module
- Track conversation and event lifecycle from open to resolution or expiry
- Generate higher-level summaries and reports
- Provide a visual interface for exploration and analysis

---

## 2. Motivation

This project serves as:

- A practical exercise in building real-time data pipelines
- A bridge between RF systems and modern software tooling
- A testbed for integrating AI into structured signal processing workflows
- A way to explore how raw signals can be transformed into meaningful information

Secondary goals:

- Gain hands-on experience with modern development tools (AI-assisted coding, Next.js, backend pipelines)
- Build intuition for RF systems ahead of formal EE study

---

## 3. Pipeline Pattern

This project is one implementation of a general-purpose intelligence pipeline pattern:

**Data Stream → Packets → Preprocessing → TRM → Analysis**

| Stage | Role |
|---|---|
| **Data Stream** | Raw signal or event source — any continuous input |
| **Packets** | Discrete, structured units ingested from the stream |
| **Preprocessing** | Domain-specific enrichment before routing (e.g. transcription, parsing, normalization) |
| **TRM** | Thread routing and event correlation — domain-agnostic intelligence layer |
| **Analysis** | Downstream consumption: summaries, reports, UI, storage |

Each stage is replaceable. The TRM only cares about the message contract. The radio dispatch pipeline is one instantiation of this pattern. Others can be built by swapping the stream source, packet schema, and preprocessing step while reusing the TRM and analysis layers unchanged.

---

## 4. Core Concept

This project maps to the pattern as follows:

**SDRTRunk → TransmissionPackets → Multi-ASR → TRM → Threads + Events → Summaries**

Each stage adds structure and meaning while preserving access to the original source. No stage overwrites earlier data.

The Thread Routing Module (TRM) replaces what was previously described as a monolithic "LLM Conversation Router." The TRM is a domain-agnostic, reusable component that simultaneously handles two distinct concerns: grouping messages into conversation threads, and correlating those threads with real-world events. These are separate layers with separate outputs.

---

## 5. The Thread Routing Module

The TRM is the core intelligence layer of this pipeline. It is not specific to radio — it is a general-purpose message routing component that the radio pipeline consumes as a module.

### 5.1 What the TRM Does

For every incoming message, the TRM makes two independent routing decisions:

**Thread routing: Who is talking to whom?**
- Does this transmission extend an existing conversation thread, or open a new one?
- A thread is a communication pattern — a group of messages determined to belong to the same exchange

**Event correlation: What real-world incident is this about?**
- Does this transmission relate to an existing open event, or does it signal a new one?
- An event is a real-world occurrence that one or more threads are about

These two decisions are made independently. A single transmission can join an existing thread while simultaneously opening a new event — for example, when a unit on routine patrol is dispatched to an incident mid-conversation.

### 5.2 The Message Contract

Every message passed to the TRM conforms to a single base schema:

```json
{
  "id": "uuid",
  "timestamp": "ISO8601",
  "text": "transcribed message content",
  "metadata": {}
}
```

The metadata field carries radio-specific context: talkgroup ID, source unit ID, department. The TRM is configured — via its configuration/prompting layer — to understand what these fields mean and how to weight them in routing decisions.

### 5.3 Thread and Event Concepts

**Threads:**
- A unique ID and open/closed status
- A running summary maintained by the routing/summarization layer
- Constraints: max duration, max silence gap, max message count
- Zero or more associated events

**Events:**
- A unique ID and open/closed status
- A label and summary maintained by the routing/summarization layer
- One or more associated threads

The relationship between threads and events is many-to-many:
- One event can have multiple threads (e.g. different talkgroups coordinating on the same incident)
- One thread can reference multiple events (e.g. a unit clears one call and is dispatched to another)
- A thread can exist with no event (routine chatter, administrative talk)
- An event can be inferred across threads even when no single thread contains the full picture

```
Message ──belongs to──▶ Thread ──relates to──▶ Event
                        Thread ◀──has many──    Event
```

### 5.4 The Two-Layer Routing Decision

For every incoming message, the TRM produces routing decisions at both layers:

**Thread layer:**

| Decision | Meaning |
|---|---|
| `existing` | Belongs to an already-open thread (includes thread ID) |
| `new` | Opens a new thread |
| `unknown` | Cannot be confidently classified |

**Event layer:**

| Decision | Meaning |
|---|---|
| `existing` | Thread relates to an already-open event (includes event ID) |
| `new` | Opens a new event |
| `none` | Thread has no associated real-world event |
| `unknown` | Cannot be confidently classified |

### 5.5 Why Metadata is Load-Bearing

Talkgroup ID, source radio ID, and timing are not supplementary — they are critical routing inputs:

- A TGID mismatch is nearly disqualifying for thread grouping (~90% of cases)
- However, TGID mismatch does not preclude shared event correlation — two departments on separate channels can be responding to the same incident
- Two transmissions can be semantically identical but correctly kept in separate threads because the participants don't match
- The TRM is configured to understand the difference: TGID is a strong thread signal, but a weaker event signal

This is why the earlier version's approach of timestamp/proximity heuristics was rejected. The TRM reasons about who is talking to whom and what real-world thing they are talking about — not just proximity in time.

---

## 6. System Architecture

### Layer 1 — Signal Processing (OP25)

Receive and decode radio transmissions.

Output:
- Talkgroup ID
- Source radio ID
- Audio stream
- Timing information

### Layer 2 — Ingestion Service

Detect transmission start/stop, generate a Transmission UUID, and save metadata, audio recording, and timestamps. Produces a `TransmissionPacket` conforming to the base `Packet` schema. See `modules/op25_integration.md`.

### Layer 3 — Multi-Pass ASR

Radio audio is often compressed, noisy, and uses domain-specific jargon. The approach:

- Run multiple ASR models (e.g. Whisper variants, specialized models) on each audio file independently
- Store all transcripts with model provenance
- Use confidence scores where available to flag uncertain transcripts
- Optionally reconcile outputs before passing downstream

Running multiple models on the same audio allows comparison and gives the TRM better signal quality to work with.

### Layer 4 — Packet Structuring

Store each `TransmissionPacket` as a discrete record. Maintain linkage between metadata, audio file(s), and all transcript versions. Prepare the message payload conforming to the TRM message contract.

### Layer 5 — Thread Routing Module (TRM)

The TRM receives each new transmission (text + metadata) alongside the current state of all open threads and events. It produces:

- A thread decision (new / existing / unknown) with thread ID if existing
- An event decision (new / existing / none / unknown) with event ID if existing
- Updated thread and event summaries

This happens in real time on each transmission. The TRM maintains a running picture of what is active and routes accordingly. It is not a batch post-processing step.

The TRM is superior to heuristic grouping because:
- It reasons about who is talking to whom, not just temporal proximity
- It understands conversational structure, call-and-response patterns, and radio protocol conventions
- It can handle incidents that go quiet and resume, or multiple parallel incidents on the same talkgroup
- It separates thread identity (communication pattern) from event identity (real-world incident) — allowing one conversation to span multiple incidents, and one incident to span multiple conversations

### Layer 6 — Thread and Event Lifecycle Management

Both threads and events have a lifecycle:

**`open → completed | expired`**

- Open: active, receiving new transmissions or thread associations
- Completed: explicitly closed by a resolution call or sign-off in the content
- Expired: no activity within a configurable timeout window (default: 2 hours)

Thread expiry and event expiry are managed independently. A thread may expire while its associated event remains open (if other threads are still active on that event).

### Layer 7 — Summarization

Use an LLM to generate incident summaries, key events, and participant roles. Operates on grouped thread and event data, not the raw stream. Triggers on thread or event completion/expiry.

### Layer 8 — Storage

Initial: SQLite or Postgres

Core entities: TransmissionPackets, ASR Results, Threads, Events, Summaries

See Section 6 for the conceptual data model.

### Layer 9 — Visualization (Next.js)

- Live transmission feed
- Timeline view
- Thread grouping with lifecycle status
- Event grouping showing which threads are associated
- Daily report summaries
- Filtering by talkgroup, radio ID, time, status

---

## 7. Data Model (Conceptual)

These are logical entities, not a final DB schema. Array fields (`participant_ids`, `transmission_ids`, `event_ids`, `thread_ids`) may become join tables in a Postgres implementation.

### TransmissionPacket

| Field | Type / Notes |
|---|---|
| transmission_id | UUID |
| timestamp_start / end | Datetime |
| talkgroup_id | Integer |
| source_radio_id | String |
| audio_path | String |
| thread_id | UUID, nullable — assigned by TRM |
| status | `captured` \| `transcribed` \| `routed` \| `summarized` \| `error` |

### ASR Result

| Field | Type / Notes |
|---|---|
| asr_result_id | UUID |
| transmission_id | FK → TransmissionPacket |
| model_name | String |
| transcript_text | Text |
| confidence | Float, nullable |
| created_at | Datetime |

### Thread

| Field | Type / Notes |
|---|---|
| thread_id | UUID |
| start_time / end_time | Datetime |
| talkgroup_id | Integer |
| participant_ids | Array |
| transmission_ids | Array |
| summary | Text, maintained by routing/summarization layer |
| status | `open` \| `completed` \| `expired` |
| event_ids | Array — events this thread is associated with |

### Event

| Field | Type / Notes |
|---|---|
| event_id | UUID |
| start_time / end_time | Datetime |
| label | String, maintained by routing/summarization layer |
| summary | Text, maintained by routing/summarization layer |
| status | `open` \| `completed` \| `expired` |
| thread_ids | Array — threads associated with this event |

---

## 8. Key Design Principles

**1. Source of Truth = Audio**
All higher-level data (transcripts, summaries, thread groupings, event correlations) must be traceable back to original audio. Nothing overwrites earlier stages.

**2. Multi-Pass ASR**
Audio quality on radio is unpredictable. Running multiple models and preserving all outputs gives the system the best chance of producing usable transcripts and flags low-confidence results for inspection.

**3. The TRM as Real-Time Dispatcher**
Thread grouping and event correlation are not batch post-processing steps — they happen on each transmission as it arrives. The TRM maintains a running picture of what's active and routes accordingly.

**4. Two Layers, Two Concerns**
Threads and events are distinct concepts managed independently. A continuous thread can span multiple events. Multiple threads can share an event. This distinction is what makes the TRM more expressive than a simple conversation grouper.

**5. Metadata is Load-Bearing**
Talkgroup ID, source radio ID, and timing are not supplementary — they are critical inputs to routing decisions. The TRM is configured to understand that TGID is a strong thread signal but a weaker event signal.

**6. Asynchronous Pipeline**
Capture happens in real time. Processing (ASR, TRM routing, summarization) happens asynchronously. The capture layer must never be blocked by downstream processing.

**7. Observability**
Each transmission has a status field tracking its progress through the pipeline. The system must allow listening to original audio, comparing transcripts across ASR models, and inspecting TRM routing decisions for both thread and event assignments.

**8. The TRM is Domain-Agnostic**
The TRM is not a radio module. It is a reusable component that the radio pipeline consumes. Other pipelines can be built on top of it by defining their own metadata schema and configuring the TRM's prompting/configuration layer accordingly. The radio pipeline is one use case.

---

## 9. Development Plan

### V1 — Capture
- Run OP25
- Detect transmissions
- Save metadata + audio files

### V2 — Multi-Pass ASR
- Integrate Whisper and at least one alternative model
- Store all transcripts with model provenance
- Evaluate quality on real radio audio

### V3 — Basic UI
- Next.js page
- Display transmissions: time, IDs, transcripts

### V4 — TRM Integration
- Build the TRM's radio-domain configuration and message contract
- Implement thread routing and event correlation in real time
- Store TRM output: thread assignments, event assignments, routing decisions
- Implement lifecycle management for both threads and events
- Build the golden dataset (Tier 1–4) for benchmarking TRM output

### V5 — Summarization
- Generate summaries from completed threads and events

### V6 — Refinement
- Tune TRM configuration based on real data and golden dataset scores
- Improve ASR reconciliation logic
- Add filters, graphs, daily reports
- Optimize pipeline performance

---

## 10. Open Questions

- How much does ASR quality vary across models on real radio audio?
- What is the optimal configuration structure for the TRM's radio-domain setup?
- What is the right expiry window for threads vs. events? They may differ significantly.
- How to handle overlapping transmissions on the same talkgroup?
- Can participant roles be inferred reliably from transcript content alone?
- At what tier of the golden dataset does TRM performance degrade? This determines where to focus configuration tuning.

> **Resolved: TRM output format.** Structured JSON is the contract (thread_id, event_id, decisions, confidence). Freeform reasoning may optionally be retained as a debug artifact but is not part of the contract. Required for storage, eval, observability, and retries.

---

## 11. Success Criteria

- System reliably captures and stores transmissions
- At least one ASR model produces usable transcripts on real radio audio
- TRM correctly separates parallel conversation threads
- TRM correctly correlates threads with real-world events, including cross-talkgroup events
- Thread and event lifecycle (open / completed / expired) work correctly and independently
- TRM scores acceptably against the golden dataset across all four tiers
- Summaries provide useful high-level understanding of completed incidents
- UI allows intuitive exploration of both thread and event views

---

## 12. Pipeline Overview

```
OP25 capture → ASR transcription → [Thread Routing Module] → Thread/Event store → Web UI
```

The TRM is one module in this larger pipeline. It sits between transcription output and the thread/event store. It receives transcribed messages with radio metadata and produces threaded, event-correlated output. Everything upstream and downstream is separate — the TRM only cares about the message contract.

---

## 13. Philosophy

This is not just a coding project.

It is an experiment in:

- Turning raw signals into structured knowledge
- Building layered systems where each stage adds meaning without destroying source data
- Understanding where AI fits in real pipelines — not as a black box, but as a reasoning layer with defined inputs and outputs
- Maintaining control and debuggability while leveraging automation
- Designing reusable components: the TRM is not a radio tool. It is a general intelligence module that happens to be deployed here first.

The most interesting decisions in this project are not the code. They are the architectural choices: separating threads from events, treating metadata as load-bearing routing context, designing for multi-pass ASR rather than assuming one model is enough, and building a golden dataset before running anything in production. These are the skills that matter.