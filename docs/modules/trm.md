# Thread Routing Module — Mock Data Strategy & Plan

## What Is the Thread Routing Module?

The Thread Routing Module (TRM) is a domain-agnostic, reusable intelligence component that ingests a stream of discrete messages — each carrying text and metadata — and does two things simultaneously:

1. **Thread Routing** — classifies each message into a conversation thread (who is talking to whom, in sequence)
2. **Event Correlation** — associates threads with real-world events (the thing being talked about)

These are two distinct layers. A thread is a communication pattern. An event is a real-world occurrence. The TRM maintains both.

It is **not** a radio module. It is not tied to any specific use case. The radio dispatch pipeline is one consumer of it. Others can be built by following the same pattern.

---

## Core Concepts

### The Message Contract

Every message passed to the TRM conforms to a single base schema:

```json
{
  "id": "uuid",
  "timestamp": "ISO8601",
  "text": "transcribed or raw message content",
  "metadata": {}
}
```

The `metadata` field is flexible. Each use case populates it with whatever fields are relevant to routing decisions for that domain. The TRM is told — via configuration or system prompt — what those fields mean and how much weight to give them.

---

### The Thread

A thread is a group of messages the TRM has determined belong to the same conversation. Threads have:

- A unique ID
- An open/closed status
- A summary (maintained by the LLM)
- Constraints (max duration, max silence gap, max message count, etc.)
- Zero or more associated events

---

### The Event

An event is a real-world occurrence that one or more threads are about. Events have:

- A unique ID
- An open/closed status
- A label/summary (maintained by the LLM)
- One or more associated threads

The relationship between threads and events is **many-to-many:**

- One event can have multiple threads (e.g. different talk groups coordinating on the same incident)
- One thread can reference multiple events (e.g. a unit clears one call and gets dispatched to another in the same conversation)
- A thread can exist with no event (routine chatter, administrative talk)
- An event can be inferred across threads even when no single thread contains the full picture

```
Message  ──belongs to──▶  Thread  ──relates to──▶  Event
                          Thread  ◀──has many──     Event
```

---

### The Two-Layer Routing Decision

For every incoming message, the TRM produces decisions at both layers:

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

These decisions are made independently. A message can join an existing thread while that thread is simultaneously being linked to a new event — for example, when a unit that was on routine patrol is suddenly dispatched to an incident mid-conversation.

---

## Why a Golden Dataset?

Before any model is run in production, we need a baseline — a fixed set of inputs where the correct outputs are already known. This allows us to:

- **Benchmark models** against each other objectively
- **Measure the impact of prompt changes** without guessing
- **Catch regressions** when logic is modified
- **Compare modes of operation** (e.g. aggressive vs. conservative threading)
- **Onboard new use cases** with a clear pattern to follow

The dataset is a permanent fixture of the repository, not a throwaway dev tool.

---

## Dataset Structure

```
/datasets/
  README.md                         # How to use the dataset, how to add scenarios
  /tier1_plain_conversation/
    /scenario_01_simple_two_party/
      transmissions.json            # Input messages
      expected_output.json          # Ground truth threads, events, and decisions
      README.md                     # Human description of what's happening
    /scenario_02_three_way_split/
      ...
  /tier2_radio_domain/
    /scenario_01_traffic_stop/
      ...
  /tier3_metadata_dominant/
    /scenario_01_tgid_separation/
      ...
  /tier4_adversarial/
    /scenario_01_mutual_aid_cross_tgid/
      ...
```

---

## The Four Tiers

### Tier 1 — Plain Conversation (Domain Agnostic)

**Purpose:** Establish a baseline for the core threading and event correlation logic with zero domain complexity.

Messages are plain natural language between named speakers. No codes, no jargon. Anyone can read the scenario and immediately know what the correct output should be. Metadata is minimal — just speaker identifiers and timestamps.

Events at this tier are simple and obvious — a topic being discussed, a problem being solved, a plan being made. The distinction between "thread" (the conversation) and "event" (the thing being discussed) should be immediately legible to a human reader.

If the TRM cannot thread and correlate these correctly, nothing else matters.

**Example scenarios:**
- Two people discussing a problem, interleaved with two other people having a completely unrelated conversation — two threads, two events, zero cross-contamination
- A conversation that starts as small talk (thread with no event) and then pivots to coordinating a response to something (thread now linked to an event)
- A three-way conversation where two people are discussing one thing and a third party introduces a separate topic — one thread forks or a new thread opens, producing two distinct events

---

### Tier 2 — Radio Domain (Semantics Matter)

**Purpose:** Test whether the TRM handles domain-specific language and richer metadata correctly.

Messages use realistic public safety communication patterns — unit designators, 10-codes, dispatch language. Talk group structure is introduced. Metadata carries TGID, unit IDs, department identifiers, timestamps.

Events at this tier are real-world incidents. The distinction between thread (the radio exchange) and event (the incident being responded to) is central to this tier.

Tests the interplay between text meaning and metadata signals.

**Example scenarios:**
- A traffic stop dispatched and acknowledged — one thread, one event (the stop)
- A multi-unit response to a single incident across two talk groups — two threads, one shared event
- A unit that clears a call and immediately gets dispatched to a new one — one continuous thread, two sequential events
- Routine chatter with no incident — thread present, no event

---

### Tier 3 — Metadata Dominant

**Purpose:** Validate that the TRM can route correctly even when text is ambiguous or semantically sparse, relying primarily on metadata.

Text content may be identical or near-identical across messages from different threads and events. The metadata — specifically talk group ID, department, unit type — is what determines correct routing.

**Example scenarios:**
- Two units on different TGIDs saying nearly identical things about completely unrelated incidents — same text, different threads, different events
- A transmission whose text is unclear but whose TGID places it unambiguously in an existing thread and event
- A message that metadata alone can assign to an event even before the text is parsed

**Key insight this tier validates:** A TGID mismatch is nearly disqualifying for thread grouping ~90% of the time. However, shared event correlation can sometimes survive a TGID mismatch — e.g. two departments talking on separate channels about the same incident. The TRM must understand the difference between these two signals: TGID is a strong thread signal, but a weaker event signal.

---

### Tier 4 — Adversarial / Edge Cases

**Purpose:** Stress-test failure modes and corner cases that real-world data will eventually produce.

**Example scenarios:**
- **Mutual aid:** Two units from different departments/TGIDs legitimately coordinating — separate threads correctly linked to the same event
- **Cold re-entry:** A unit goes silent for a long time then transmits again — does the thread re-open, does the event re-open, or do both start fresh?
- **Semantic trap:** Near-identical language used in two completely unrelated incidents — must not be merged into the same event
- **Thread-event decoupling:** A thread that transitions from one event to another mid-stream — the thread is continuous but the event changes
- **Ambiguous unit ID:** Same unit ID appearing in two different departments — metadata appears to match but context does not

---

## Scenario File Format

### `transmissions.json`
```json
[
  {
    "id": "msg_001",
    "timestamp": "2024-01-15T14:23:01Z",
    "text": "Unit 4, start for the 10-50 at Main and 5th.",
    "metadata": {
      "tgid": 1001,
      "source_unit": "DISPATCH",
      "department": "police"
    }
  },
  {
    "id": "msg_002",
    "timestamp": "2024-01-15T14:23:18Z",
    "text": "Copy, Unit 4 en route.",
    "metadata": {
      "tgid": 1001,
      "source_unit": "UNIT_4",
      "department": "police"
    }
  },
  {
    "id": "msg_003",
    "timestamp": "2024-01-15T14:31:02Z",
    "text": "Unit 4, also be advised we have a 10-62 at Oak and 3rd, can you swing by after?",
    "metadata": {
      "tgid": 1001,
      "source_unit": "DISPATCH",
      "department": "police"
    }
  }
]
```

### `expected_output.json`
```json
{
  "threads": [
    {
      "thread_id": "thread_A",
      "label": "Unit 4 dispatch and response",
      "status": "open",
      "message_ids": ["msg_001", "msg_002", "msg_003"]
    }
  ],
  "events": [
    {
      "event_id": "event_A",
      "label": "Traffic accident at Main and 5th",
      "status": "open",
      "thread_ids": ["thread_A"]
    },
    {
      "event_id": "event_B",
      "label": "Follow-up call at Oak and 3rd",
      "status": "open",
      "thread_ids": ["thread_A"]
    }
  ],
  "routing": [
    {
      "message_id": "msg_001",
      "thread_decision": "new",
      "thread_id": "thread_A",
      "event_decision": "new",
      "event_id": "event_A"
    },
    {
      "message_id": "msg_002",
      "thread_decision": "existing",
      "thread_id": "thread_A",
      "event_decision": "existing",
      "event_id": "event_A"
    },
    {
      "message_id": "msg_003",
      "thread_decision": "existing",
      "thread_id": "thread_A",
      "event_decision": "new",
      "event_id": "event_B"
    }
  ]
}
```

Note how `msg_003` joins an existing thread but opens a new event — the conversation continues unbroken but a second real-world incident enters the picture. This illustrates the independence of the two routing layers and is a pattern that will appear frequently in real radio data.

---

## Scoring Metrics

When a model's output is compared against `expected_output.json`, the following are computed:

**Thread-level metrics:**

| Metric | Description |
|---|---|
| **Thread accuracy** | Were the right messages grouped into the right threads? |
| **Thread boundary detection** | Did threads open and close at the right messages? |
| **False thread grouping rate** | Unrelated messages incorrectly merged into one thread |
| **Thread miss rate** | Related messages incorrectly split across different threads |
| **Thread classification accuracy** | Correct `new` / `existing` / `unknown` decisions |

**Event-level metrics:**

| Metric | Description |
|---|---|
| **Event accuracy** | Were the right threads linked to the right events? |
| **Event boundary detection** | Did events open and close at the right messages? |
| **False event grouping rate** | Unrelated threads incorrectly merged into one event |
| **Event miss rate** | Related threads incorrectly split across different events |
| **Event classification accuracy** | Correct `new` / `existing` / `none` / `unknown` decisions |
| **Thread-event decoupling accuracy** | Correct handling of messages where thread and event decisions diverge |

**Cross-cutting metrics:**

| Metric | Description |
|---|---|
| **Metadata sensitivity** | Score delta when metadata is stripped vs. included (measures reliance on metadata signals) |
| **Overall composite score** | Weighted combination of thread and event metrics |

---

## Adding a New Use Case

To adapt the TRM to a new domain, follow this pattern:

1. Define the metadata fields relevant to your routing decisions
2. Define what constitutes a "thread" vs. an "event" in your domain
3. Write Tier 1 scenarios using the base schema (minimal metadata, plain language)
4. Write Tier 2–4 scenarios with your domain's metadata populated
5. Document what each metadata field means and its expected influence on thread and event routing
6. Run against the TRM and score against your ground truth
7. Tune the system prompt / config to reflect your domain's signal weights

The Tier 1 scenarios from the original dataset remain valid baselines across all use cases.

---

## Relationship to the Broader Project

The TRM is one module in a larger pipeline. In the radio dispatch use case:

```
OP25 capture → ASR transcription → [Thread Routing Module] → Event store → Web UI
```

The TRM sits between the transcription output and the event store. It receives transcribed messages with radio metadata and produces threaded, event-correlated output. Everything upstream and downstream is separate — the TRM only cares about the message contract.