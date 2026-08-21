# Verification recipes

Ways to make an agent (or yourself) prove that something works, rather than
report that it should.

## The rule

A test that constructs the client's message itself cannot catch a bug in how the
client constructs that message. A mock more forgiving than the server hides the
difference between them. Whatever is under test has to be the thing that runs.

This is not hypothetical. A broken Approve button once passed both a mock-driven
frontend check and a headless protocol test: the mock matched approvals on one
field, and the protocol test hand-wrote the payload with a field the real page
omitted. Neither exercised what the page actually sent. One human click found it
immediately.

## Browser UI: drive the real page

Use `scripts/cdp_drive.py` in this skill. It launches headless Chromium, attaches
over the DevTools Protocol, evaluates JavaScript in the page, and captures
screenshots. No dependency beyond `aiohttp` and a Chromium binary.

To capture what a page actually sends over a WebSocket, wrap the method before
clicking:

```js
(() => {
  const sent = [];
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { sent.push(d); return orig.call(this, d); };
  document.querySelector('.approval-card .btn-approve').click();
  WebSocket.prototype.send = orig;
  return sent.join(' | ');
})()
```

`scripts/cdp_drive.py --capture-ws --click <selector>` does exactly that.

One caveat found while testing it: this only sees frames sent through a real
`WebSocket`. A mock backend that hands the page a fake socket object never
touches `WebSocket.prototype`, so the capture comes back empty even though the
mock UI works. Capture against the real server.

Give each agent its own debugging port and its own `--user-data-dir`, or two
sessions will fight over one browser profile. Steps run in the order written, so
an `--eval` that selects something can precede the `--click` that needs it.

Look at the screenshots. Colour, contrast and layout problems do not show up in a
DOM query, and neither does a diff rendered with the wrong lines marked.

## Desktop GTK: drive the app through its own actions

A GTK application registered on the session bus exposes its actions, so a test
can select a session or open a view without a human clicking:

```
gdbus call --session --dest <app.id> --object-path /<app/id/path> \
  --method org.gtk.Actions.Activate "<action-name>" "[<'argument'>]" "{}"
```

Then screenshot the result (`spectacle -b -n -f -o out.png` on KDE). This also
exercises the action a notification click would fire, which is otherwise awkward
to test.

## Check claims against stored data

Before accepting "the input shape is X", look for real examples. Transcripts,
logs and fixtures on disk answer questions that reasoning only guesses at.
Counting occurrences is often the whole answer: a shape that appears zero times
in 1800 real calls is not a shape to claim coverage of.

## Measure semantics, do not assume them

When a field's meaning decides the implementation (cumulative or per-turn,
inclusive or exclusive), run the two-sample experiment. One session, two turns,
print the field both times. Ten minutes of measurement replaced an entire wrong
implementation of token accounting, twice.

## Watch the system, not the code, for side effects

Desktop notifications, D-Bus signals and outbound requests can be observed
directly:

```
dbus-monitor --session "interface='org.freedesktop.Notifications',member='Notify'"
```

That shows the title, body, urgency and replacement id actually delivered, which
is the only way to know a notification replaced its predecessor rather than
stacking.
