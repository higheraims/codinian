---
id: ISSUE-078
title: A picture in a tool result ends the session
status: done
type: bug
area: sdk
created: 2026-09-24
updated: 2026-09-24
related: [ISSUE-035, ISSUE-036]
---

## Summary

Reported from a live session: "Failed to decode JSON: JSON message exceeded
maximum buffer size of 1048576 bytes...". The user had asked for a mockup, which
is how a picture got into the conversation.

The CLI writes NDJSON, one message to a line, and a tool result carrying an
image puts the whole image on that line as base64. It puts it there twice: once
in `message.content[].source.data`, and again in `toolUseResult.file.base64`. The
SDK frames those lines against a default ceiling of 1,048,576 bytes and raises
on anything longer.

A `Read` of a 319 KB PNG is enough to pass it. The line measured 1,290,865
bytes.

What that costs is the session rather than the message. The check lives in the
SDK's own stdout reader task and is enforced by a raise inside it, so the task
dies and the message stream closes for good. `_read` then has nothing left to
read and calls `_fail`, which is the ISSUE-036 path: ERROR, and `send` refuses
from then on. Nothing is wrong with the `claude` process, and it carries on
without noticing. In the session that reported this, its transcript kept growing
for thirteen seconds after Codinian had already given up on it.

## Steps to reproduce

The mockup from the reported session was still on disk, so the failure and the
fix were both measured against a real CLI. One throwaway client, one `Read`, the
only difference being `max_buffer_size`:

```
default (unset -> 1 MiB)     RAISED after 6 messages: CLIJSONDecodeError: ...
64 MiB                       ok: 10 messages, result='OK'
```

The offending line, from `~/.claude/projects`:

```
tool: Read /tmp/pin-mockup.png
image/png, 1200x2600, resized by the CLI to 923x2000
319,120 bytes on disk -> 645,008 of base64, carried twice -> 1,290,865 byte line
```

## Acceptance / done-when

- A turn that reads an image runs to its end.
- The image still reaches a client as a reference rather than as base64, which
  is ISSUE-035 and is a separate concern from this one.
- The bound is still a bound.

## Notes & worklog

- Not specific to images in principle, only in practice. Anything the CLI can
  put on one line reaches the same ceiling; images are what get near it, because
  base64 is the one payload that arrives at its full size and then gets repeated.

- The duplication is the CLI's, not ours, and it halves the headroom. Worth
  knowing when picking the number: the size that matters is twice the image.

## Resolution

`MAX_BUFFER_BYTES` in `sdk_session.py`, passed as `max_buffer_size`. 64 MiB,
because the largest honest line is a `Read` of a PDF, which returns up to twenty
pages as images in one tool result: at the 645 KB one page of that mockup came
to, doubled, twenty pages is about 26 MB. It is still a limit on a subprocess we
launched rather than on a network peer, so it is there to stop a runaway eating
memory, not to rule on how big a real message may be.

Two tests in `tests/test_sdk_transport.py`, both driving a stub `claude` as a
real subprocess. The limit is applied while lines are framed off the pipe, before
there is a message to hand to `_handle_message`, so a fake client returning
objects cannot reach it. One test holds the fix: a 2 MiB image result finishes
the turn and arrives as a reference. The other pins `MAX_BUFFER_BYTES` back to
1 MiB and asserts the session dies, so the reason the setting exists is written
down in a form that runs. Removing the option from the options literal fails the
first with the original error text.

Full suite: 778 passed.
