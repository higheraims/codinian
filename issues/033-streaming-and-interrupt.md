---
id: ISSUE-033
title: Stream partial responses, and wire up interrupt
status: done
type: feature
area: gui
created: 2026-08-20
updated: 2026-08-20
related: [ISSUE-032, ISSUE-003]
---

## Summary

A turn renders nothing between the last tool result and the finished text block.
`ClaudeAgentOptions.include_partial_messages` would emit the deltas that fill
that gap, and `SdkSession.interrupt()` already exists to act on what they show.

Both are held over from M1, where `sdk_session.py` says partial streaming is "a
later enhancement". This is that.

## Acceptance / done-when

- Assistant text appears as it is generated, not only when the block completes.
- Deltas are never stored and never replayed, so a reconnecting client gets
  whole blocks rather than a token-by-token history.
- A running turn can be stopped from the transcript, on the pane and in a browser.

## Notes & worklog

- The two are one piece of work: watching an answer form is worth much more when
  you can stop it, and a stop button is hard to use when there is nothing to
  react to.

## The case for

Dead air is worst on a reasoning-heavy turn with few tools. A tool-heavy turn
already shows constant motion as cards appear; a turn that thinks and then
writes several paragraphs currently shows a status dot and nothing else.

ISSUE-032 makes this worse before it makes it better: turning on Claude Code's
system prompt produces more prose, so more of each turn is spent in exactly the
silence streaming would fill. The two changes compound.

The second reason is steering. Seeing an answer go wrong early is only useful if
it can be stopped, and `SdkSession.interrupt()` is currently wired to nothing:
no route, no button.

Streaming costs no extra tokens. It is the same request with finer-grained
output events.

## The cost, which is architectural

`SessionManager.add_event` appends every event to `self._events[session_id]` and
keeps it for the life of the session; `get_events` returns that whole list on
every `subscribe`. Per-token deltas would mean thousands of events per turn, all
retained in memory, and all re-sent whenever a client reconnects, to rebuild text
it could have received as a single block.

So this is not a flag flip. It needs a second tier on the bus: transient events
that broadcast but are never stored, with the existing block event staying the
durable record. That changes the contract in `docs/transcript-protocol.md`, and
it adds a second render path, since replayed history from JSONL never contains
deltas. Given that ISSUE-025 through ISSUE-028 were all renderer bugs, that path
is worth building deliberately.

## Constraints found while scoping

`include_partial_messages` is a session-creation option, so the backend either
emits deltas for everyone watching a session or for nobody. It cannot be a
per-viewer preference without emitting always and filtering client-side.

## Measured: what this costs a phone

Measured against the ISSUE-024 session, whose events were replayed through
`claude_history` and serialized exactly as the WebSocket hub sends them.

That session's whole event stream is **275 KiB**, and its shape is the
surprising part: 8 assistant text blocks totalling 3,295 characters, against
**91,497 output tokens**. Over 99% of what the model generated was thinking and
tool-call argument JSON, not prose anyone reads.

One delta on the wire is 146 bytes of JSON for a 4-character token, because the
event envelope (`type`, `session_id`, `seq`, `ts`) is 142 bytes of it. Add the
WebSocket, TLS, TCP/IP and WireGuard framing on the tailnet path and a delta
costs **270 bytes to carry 4** — 68x overhead.

| | deltas | added | vs today |
|---|---|---|---|
| today, no streaming | - | 0.27 MiB | 1.0x |
| stream assistant text only | 823 | 0.21 MiB | 1.8x |
| stream everything the SDK emits | 91,497 | 23.6 MiB | 89x |

**Streaming assistant text is cheap and the earlier worry about it was wrong.**
0.21 MiB for a half-hour session is nothing, on any link. What is expensive is
forwarding every `StreamEvent` without filtering, which is the naive
implementation and lands at 89x the session's entire current traffic.

Note the interaction with ISSUE-032: setting thinking display to `summarized`
turns thinking into real text, and thinking is the bulk of those 91,497 tokens.
Summarized thinking plus unfiltered streaming *is* the expensive column.

### Batching, and why battery is not a bandwidth question

The envelope is 36x the payload for a single token, so flushing on a timer
rather than per token collapses most of it. At 50 tokens/second:

| flush every | tokens/frame | packets/s | total (all output) | total (text only) |
|---|---|---|---|---|
| immediately | 1 | 50 | 23.6 MiB | 217 KiB |
| 100 ms | 5 | 10 | 5.0 MiB | 46 KiB |
| 250 ms | 12 | 4 | 2.3 MiB | 21 KiB |

The battery cost is packet cadence, not bytes: a packet every 20 ms holds a
cellular radio in its connected state for the length of a turn, where bursts
every 250 ms let it idle between them. 100 ms is under the threshold where
text stops looking live, and buys 5x on both counts.

### Reconnect

A client that reconnects re-subscribes and receives the whole event list: 275
KiB today. If deltas were stored alongside everything else, that becomes ~24 MiB
per reconnect, for text the client could have had as a handful of blocks. This
is the number behind the transient-tier requirement above, not a theoretical
tidiness argument.

## Plan

1. A transient event tier: broadcast without storing, and excluded from the
   `subscribe` snapshot.
2. `include_partial_messages` behind a switch on the Settings Claude tab.
3. Map `StreamEvent` (whose `event` field is the raw Anthropic stream event) to
   a delta event. **Forward text deltas only by default.** Thinking and
   tool-argument deltas are 99% of the volume and neither is legible as it
   arrives; the tool card already appears the moment the call is made.
4. Coalesce deltas on a ~100 ms timer rather than sending one frame per token.
   The envelope is 36x a single token's payload, so this is most of the cost,
   and it takes the packet rate from 50/s to 10/s.
5. Render by mutating the open block and reconciling when the finished block
   arrives.
6. An interrupt route and a stop button, shown while a turn is running.
7. Update `docs/transcript-protocol.md` for the two-tier model.

Steps 3 and 4 are what make this affordable on a phone; without them the naive
version is 89x the current traffic. With them it is roughly 46 KiB per session
on top of 275 KiB, which needs no per-client switch at all.

## Resolution

Built as planned, and the two mitigations from the measurements are what made it
affordable rather than optional extras.

**The transient tier.** `SessionManager.broadcast_transient` sends to whoever is
watching without storing. A transient event carries `"transient": true` and
**no `seq`**, because it has no place in the ordering a reconnecting client
catches up on, and the finished block that follows is the durable record of the
same text. `add_event` is untouched, so the seq counter never sees a delta and a
`subscribe` snapshot never replays one.

**Text only.** `_handle_stream_event` forwards `content_block_delta` with a
`text_delta` and drops everything else: thinking deltas, tool-argument deltas,
and any delta carrying a `parent_tool_use_id`. That last one is a subagent,
whose card is collapsed by default, so the text would stream into something
nobody is looking at.

**Coalescing.** Deltas accumulate per content-block index and flush every
100 ms. The envelope is 142 bytes around a 4-byte token, so per-token frames
spend 270 bytes to carry 4; batching takes the traffic and the packet rate to a
fifth each, and 100 ms is under the threshold where text stops looking live.

**Rendering.** A preview block accumulates per index, marked `is-streaming` with
a blinking cursor so it reads as provisional. Any finished `text`, `thinking` or
`tool_use` event clears every preview before rendering, which is what stops the
tail appearing twice. An empty bubble left behind by a cleared preview is
removed with it.

**Interrupt.** `SdkSession.interrupt()` had existed since M1 with nothing wired
to it. `SdkRuntime.interrupt` and a `{"t": "interrupt"}` message now reach it,
and a Stop button appears in the composer only while a turn is running. It
reports "Stopping…" rather than claiming success, because the turn ends at a
safe point rather than instantly.

### What was checked

The transient tier without a browser: a transient reaches subscribers, is marked,
carries no seq, is absent from `get_events`, leaves the seq counter untouched,
and is dropped for an unknown session. Coalescing: four deltas become one frame
carrying the whole run, two block indexes stay two frames, thinking and
tool-argument and subagent deltas produce nothing, and a finished block discards
what was still buffered.

On the real page, in WebKitGTK: sampled mid-stream at 6 s, a preview block held
36 characters of partial text; at 7 s the preview was gone and the durable block
whole, with no duplicated tail and no empty bubble. The Stop button is present
and visible while busy, and clicking it puts
`{"t":"interrupt","session_id":"test0001"}` on the wire, captured off
`WebSocket.prototype.send`.

### Two harness traps worth recording

Both cost time and neither was a product bug.

The demo first fired from a `threading.Timer`. The hub schedules sends with
`asyncio.ensure_future`, which needs a running loop in the calling thread, so
every event was dropped with `RuntimeError: There is no current event loop`.
Production flushes from an asyncio task and is unaffected, but anything driving
the bus from a test must do so on the loop.

Then the browser verification kept reporting nothing while the DOM plainly had
partial text in it. Headless Chromium throttles background tabs, so neither
`setInterval` nor a `MutationObserver` fired often enough to sample a five second
stream. The same throttling stalls `mock.js`. WebKitGTK, which is the real
renderer anyway, has no such problem: evaluate at a fixed delay and read the DOM.

### Not done

A single global 100 ms flush interval, not adaptive. Fine at observed generation
speeds; worth revisiting only if a model gets fast enough that 100 ms buffers
more than a line.
