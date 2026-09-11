// Scripted fake backend for Codinian, used when there's no reachable
// WebSocket server (or when the page is opened with ?mock=1). Installs
// window.CodinianMock.createSocket(), which returns an object shaped enough
// like a WebSocket (onopen/onmessage/onclose/onerror, send(), close()) for
// app.js to drive without knowing it isn't real.
//
// Runs four sessions on timers:
//   - "a1c93f02" finishes a small multi-tool edit-and-fix loop and lands on
//     awaiting_input.
//   - "7be04dd1" streams some text, then raises an approval_request for a
//     Bash command and stops there until resolve() is called, so the three
//     approval actions (Approve / Deny / Approve-with-edits) have something
//     to click. Also reachable from the cross-session inbox (ISSUE-010),
//     since it is the second approval pending at the same time as "f4e8b716".
//   - "f4e8b716" runs an Edit, a MultiEdit, and a Write, then raises an
//     approval_request for ExitPlanMode and stops there until resolve() is
//     called, exercising the plan/diff/write renderers (ISSUE-013) in both
//     the tool card and the approval card.
//   - "b2f9013c" is a resumed session (totals_cover_this_run_only from the
//     start) whose one turn is cache-heavy, exercising ISSUE-011's footer
//     and its "this run only" note.
//
// Every session's `permission_mode` can be changed from the UI at any time
// (ISSUE-012); the mock answers set_permission_mode the same way
// remote/server.py does, including the unknown_permission_mode and
// unknown_session error replies.

(function () {
  'use strict';

  function uid(prefix) {
    return prefix + Math.random().toString(36).slice(2, 8);
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // ---------------------------------------------------------------------
  // fake transport
  // ---------------------------------------------------------------------

  class FakeWebSocket {
    constructor() {
      this.onopen = null;
      this.onmessage = null;
      this.onclose = null;
      this.onerror = null;
      this.readyState = 0; // CONNECTING
      setTimeout(() => {
        this.readyState = 1; // OPEN
        if (this.onopen) this.onopen({});
      }, 40);
    }

    send(data) {
      let msg;
      try {
        msg = JSON.parse(data);
      } catch {
        return;
      }
      director.handleClientMessage(msg);
    }

    close() {
      // The mock never dies on its own; nothing for the client to do here.
    }

    _emit(obj) {
      if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) });
    }
  }

  // ---------------------------------------------------------------------
  // scripted sessions
  // ---------------------------------------------------------------------

  // A fresh SessionMeta.tokens, per docs/transcript-protocol.md — every
  // scripted session starts here so ISSUE-011's footer/sidebar code never has
  // to guard against a missing `tokens` object.
  function zeroTokens() {
    return { input: 0, output: 0, cache_read: 0, cache_creation: 0 };
  }

  const SESSION_A = {
    id: 'a1c93f02',
    name: 'Cache layer refactor',
    workdir: '/home/user/projects/cache-service',
    kind: 'sdk',
    status: 'initializing',
    created_at: new Date(Date.now() - 5 * 60000).toISOString(),
    sdk_session_id: null,
    permission_mode: 'acceptEdits',
    cost_usd: 0,
    tokens: zeroTokens(),
    totals_cover_this_run_only: false,
  };

  const SESSION_C = {
    id: 'f4e8b716',
    name: 'Plan mode walkthrough',
    workdir: '/home/user/projects/codinian-demo',
    kind: 'sdk',
    status: 'initializing',
    created_at: new Date(Date.now() - 1 * 60000).toISOString(),
    sdk_session_id: null,
    permission_mode: 'plan',
    cost_usd: 0,
    tokens: zeroTokens(),
    totals_cover_this_run_only: false,
  };

  const SESSION_B = {
    id: '7be04dd1',
    name: 'Deploy script hardening',
    workdir: '/home/user/projects/infra',
    kind: 'sdk',
    status: 'initializing',
    created_at: new Date(Date.now() - 2 * 60000).toISOString(),
    sdk_session_id: null,
    permission_mode: 'default',
    cost_usd: 0,
    tokens: zeroTokens(),
    totals_cover_this_run_only: false,
  };

  // A resumed session (ISSUE-009): seeded history carries no usage of its
  // own, so its totals cover only what this run has spent. Exercises the
  // footer's "this run only" note and, with a cache-heavy turn, the case
  // where cache_read dwarfs every other counter.
  const SESSION_D = {
    id: 'b2f9013c',
    name: 'Resumed: docs cleanup',
    workdir: '/home/user/projects/codinian-demo',
    kind: 'sdk',
    status: 'initializing',
    created_at: new Date(Date.now() - 20 * 60000).toISOString(),
    sdk_session_id: null,
    permission_mode: 'default',
    cost_usd: 0,
    tokens: zeroTokens(),
    totals_cover_this_run_only: true,
  };

  // The pre-SDK path, and the only session here the browser cannot drive: a
  // terminal session's screen is mirrored in the desktop app and its composer
  // lives there. Present so the demo shows the footer and composer in the
  // state they take for one (ISSUE-038), which is otherwise unreachable
  // without a running GTK window.
  const SESSION_E = {
    id: '9d55ac41',
    name: 'Terminal: log triage',
    workdir: '/home/user/projects/infra',
    kind: 'terminal',
    status: 'working',
    created_at: new Date(Date.now() - 35 * 60000).toISOString(),
    sdk_session_id: null,
    permission_mode: 'default',
    cost_usd: 0,
    tokens: zeroTokens(),
    totals_cover_this_run_only: false,
  };

  // ---------------------------------------------------------------------
  // director: owns session state, per-session event backlog/seq, and the
  // pending-approval gates the scripts block on.
  // ---------------------------------------------------------------------

  // What the real server accepts for set_permission_mode (remote/server.py).
  const PERMISSION_MODES = new Set(['default', 'acceptEdits', 'plan', 'bypassPermissions', 'dontAsk', 'auto']);

  class Director {
    constructor() {
      this.socket = null;
      this.sessions = new Map();
      this.backlogs = new Map();
      this.seqs = new Map();
      this.subscribed = new Set();
      this.pendingApprovals = new Map();
      // Descriptive metadata for every approval still waiting on a decision,
      // across every session — the cross-session inbox (ISSUE-010) reads this
      // on subscribe_inbox the same way the real backend's SdkRuntime does.
      this.pendingApprovalMeta = new Map();
      this.inboxSockets = new Set();
    }

    attach(socket) {
      this.socket = socket;
    }

    registerSession(meta) {
      this.sessions.set(meta.id, meta);
      this.backlogs.set(meta.id, []);
      this.seqs.set(meta.id, 0);
    }

    nextSeq(sessionId) {
      const n = this.seqs.get(sessionId);
      this.seqs.set(sessionId, n + 1);
      return n;
    }

    emitEvent(sessionId, partial) {
      // Mirrors SessionManager.add_event, which drops an event for a session
      // the manager no longer holds. A closed session's script keeps running
      // in here for a while, and its remaining events go nowhere.
      if (!this.sessions.has(sessionId)) return;
      const ev = Object.assign({ session_id: sessionId, seq: this.nextSeq(sessionId), ts: nowIso() }, partial);
      this.backlogs.get(sessionId).push(ev);

      // Mirrors session.py's SessionManager.add_event: cost is cumulative
      // for the conversation so it is assigned, tokens arrive per turn so
      // they are summed. Keeps this session's stored meta correct, since
      // setStatus below hands out a full copy of it on every status change.
      if (ev.type === 'usage') {
        const meta = this.sessions.get(sessionId);
        if (meta) {
          if (ev.total_cost_usd != null) meta.cost_usd = ev.total_cost_usd;
          this._accumulateTokens(meta, ev.tokens);
        }
      }

      if (this.subscribed.has(sessionId) && this.socket) {
        this.socket._emit({ t: 'event', event: ev });
      }

      if (ev.type === 'approval_request') {
        this.pendingApprovalMeta.set(ev.request_id, {
          session_id: sessionId,
          request_id: ev.request_id,
          tool_use_id: ev.tool_use_id,
          name: ev.name,
          input: ev.input,
        });
      } else if (ev.type === 'approval_resolved') {
        this.pendingApprovalMeta.delete(ev.request_id);
      }

      // Sent to every inbox subscriber regardless of what session they have
      // open, same as remote/server.py's ClientHub.broadcast_event.
      if ((ev.type === 'approval_request' || ev.type === 'approval_resolved') && this.inboxSockets.size) {
        const meta = this.sessions.get(sessionId);
        const inboxMessage = { t: 'inbox_event', event: ev, session: meta ? { ...meta } : null };
        for (const ws of this.inboxSockets) ws._emit(inboxMessage);
      }

      return ev;
    }

    _accumulateTokens(meta, usage) {
      if (!usage || typeof usage !== 'object') return;
      if (!meta.tokens) meta.tokens = zeroTokens();
      const map = {
        input_tokens: 'input',
        output_tokens: 'output',
        cache_read_input_tokens: 'cache_read',
        cache_creation_input_tokens: 'cache_creation',
      };
      for (const rawKey of Object.keys(map)) {
        const value = usage[rawKey];
        if (typeof value === 'number') {
          const field = map[rawKey];
          meta.tokens[field] = (meta.tokens[field] || 0) + value;
        }
      }
    }

    setStatus(sessionId, status) {
      const meta = this.sessions.get(sessionId);
      if (!meta) return;
      meta.status = status;
      this.emitEvent(sessionId, { type: 'status', status });
      if (this.socket) this.socket._emit({ t: 'session_update', session: { ...meta } });
    }

    sendSessionsList() {
      if (!this.socket) return;
      this.socket._emit({ t: 'sessions', sessions: Array.from(this.sessions.values()).map((s) => ({ ...s })) });
    }

    handleClientMessage(msg) {
      if (msg.t === 'subscribe') {
        this.subscribed.add(msg.session_id);
        const meta = this.sessions.get(msg.session_id);
        const events = this.backlogs.get(msg.session_id) || [];
        this.socket._emit({
          t: 'snapshot',
          session_id: msg.session_id,
          session: meta ? { ...meta } : null,
          status: meta ? meta.status : 'error',
          events: events.slice(),
        });
      } else if (msg.t === 'unsubscribe') {
        this.subscribed.delete(msg.session_id);
      } else if (msg.t === 'send') {
        this.handleUserSend(msg.session_id, msg.text);
      } else if (msg.t === 'resolve') {
        this.handleResolve(msg);
      } else if (msg.t === 'subscribe_inbox') {
        this.handleSubscribeInbox();
      } else if (msg.t === 'unsubscribe_inbox') {
        this.inboxSockets.delete(this.socket);
      } else if (msg.t === 'set_permission_mode') {
        this.handleSetPermissionMode(msg);
      } else if (msg.t === 'rename') {
        this.handleRename(msg);
      } else if (msg.t === 'close') {
        this.handleClose(msg);
      }
      // 'create' isn't exercised by this script — the mock only drives the
      // pre-registered sessions.
    }

    handleClose(msg) {
      if (!this.sessions.has(msg.session_id)) {
        this.socket._emit({ t: 'error', error: 'close_failed', session_id: msg.session_id });
        return;
      }
      this.sessions.delete(msg.session_id);
      this.backlogs.delete(msg.session_id);
      this.subscribed.delete(msg.session_id);
      this.sendSessionsList();
    }

    handleRename(msg) {
      const meta = this.sessions.get(msg.session_id);
      const name = (msg.name || '').trim();
      if (!meta || !name) {
        // Matches remote/server.py, which answers rather than silently
        // dropping a rename of a session it does not have.
        this.socket._emit({ t: 'error', error: 'rename_failed', session_id: msg.session_id });
        return;
      }
      meta.name = name;
      meta.name_is_custom = true;
      this.sendSessionsList();
    }

    handleResolve(msg) {
      const pending = this.pendingApprovals.get(msg.request_id);
      if (!pending) {
        // Matches remote/server.py: a resolve naming an approval that is no
        // longer pending (already answered, or never existed) gets this back
        // rather than being silently dropped.
        this.socket._emit({ t: 'error', error: 'stale_or_unknown_request', request_id: msg.request_id });
        return;
      }
      this.pendingApprovals.delete(msg.request_id);
      pending.resolve(msg);
    }

    handleSubscribeInbox() {
      this.inboxSockets.add(this.socket);
      const approvals = Array.from(this.pendingApprovalMeta.values()).map((a) => {
        const meta = this.sessions.get(a.session_id);
        return { ...a, session: meta ? { ...meta } : null };
      });
      this.socket._emit({ t: 'inbox', approvals });
    }

    handleSetPermissionMode(msg) {
      if (!PERMISSION_MODES.has(msg.mode)) {
        this.socket._emit({ t: 'error', error: 'unknown_permission_mode', mode: msg.mode });
        return;
      }
      const meta = this.sessions.get(msg.session_id);
      if (!meta) {
        this.socket._emit({ t: 'error', error: 'unknown_session', session_id: msg.session_id });
        return;
      }
      meta.permission_mode = msg.mode;
      // Matches sdk_session.py: the mode change reaches clients as a `system`
      // event, not a session broadcast, and applies from the next tool call.
      this.emitEvent(msg.session_id, {
        type: 'system',
        subtype: 'permission_mode',
        data: { permission_mode: msg.mode },
      });
    }

    waitForApproval(requestId) {
      return new Promise((resolve) => {
        this.pendingApprovals.set(requestId, { resolve });
      });
    }

    handleUserSend(sessionId, text) {
      const meta = this.sessions.get(sessionId);
      if (!meta) return;
      this.emitEvent(sessionId, { type: 'text', role: 'user', text, source: 'operator' });
      this.setStatus(sessionId, 'working');
      (async () => {
        await wait(800 + Math.random() * 500);
        this.emitEvent(sessionId, {
          type: 'text',
          role: 'assistant',
          text: "This is a scripted mock session, so I can't actually act on that — but the message round-trip works.",
        });
        this.emitEvent(sessionId, {
          type: 'usage',
          is_error: false,
          total_cost_usd: 0.0021,
          tokens: { input_tokens: 120, output_tokens: 40 },
          result_text: null,
        });
        this.setStatus(sessionId, 'awaiting_input');
      })();
    }
  }

  const director = new Director();
  // Exposed so a driver can push any event the protocol defines into a running
  // page -- a rate limit warning, a rejection -- without waiting for the real
  // conditions to occur. Mock mode is only reachable with ?mock=1, where this
  // is the whole point of the harness.
  window.__mock = { director, sessions: { A: SESSION_A, B: SESSION_B, C: SESSION_C, D: SESSION_D, E: SESSION_E } };
  director.registerSession(SESSION_A);
  director.registerSession(SESSION_B);
  director.registerSession(SESSION_C);
  director.registerSession(SESSION_D);
  director.registerSession(SESSION_E);

  // ---------------------------------------------------------------------
  // scripts
  // ---------------------------------------------------------------------

  // Realistic multi-section plan text for session C: headings at two levels,
  // a numbered list, a bullet list, inline code, bold, and a fenced code
  // block, so ISSUE-013's plan renderer gets exercised across the markdown
  // subset it supports.
  const PLAN_TEXT = [
    '# Plan: harden the deploy script',
    '',
    '## Summary',
    'Add guards around the destructive parts of `deploy.sh` before it runs',
    'unattended in CI, and cover the change with a regression test.',
    '',
    '## Steps',
    '1. Quote `$BUILD_DIR` everywhere and add a `:?` guard so an empty value',
    '   stops the script instead of deleting from `/`.',
    '2. Replace the bare `rm -rf` with a check that the directory exists and',
    '   is inside the expected workspace root.',
    '3. Add a **smoke test** that runs the script against a temp directory',
    '   and asserts it refuses to run when `BUILD_DIR` is unset.',
    '',
    '## Files touched',
    '- `deploy.sh`',
    '- `tests/test_deploy.py` (new)',
    '',
    '## Example guard',
    '',
    '```bash',
    ': "${BUILD_DIR:?BUILD_DIR must be set}"',
    'rm -rf -- "${BUILD_DIR:?}"/*',
    '```',
    '',
    'No other scripts in this repo call `deploy.sh`, so this stays a',
    'single-file change.',
  ].join('\n');

  async function runSessionA() {
    const id = SESSION_A.id;

    await wait(300);
    director.emitEvent(id, {
      type: 'system',
      subtype: 'init',
      data: {
        model: 'claude-opus-4-6',
        cwd: SESSION_A.workdir,
        sdk_session_id: uid('sdk-'),
        tools: ['Read', 'Edit', 'Write', 'Bash', 'Grep', 'Glob'],
      },
    });
    director.setStatus(id, 'working');

    await wait(300);
    // Included to prove the renderer suppresses the rate-limit banner when
    // status is "allowed" — only non-"allowed" statuses should show one.
    director.emitEvent(id, { type: 'rate_limit', status: 'allowed', rate_limit_type: null, resets_at: null, utilization: null });

    await wait(500);
    director.emitEvent(id, { type: 'text', role: 'assistant', text: "I'll look at the current cache implementation before making changes." });

    await wait(700);
    const readId = uid('tu_');
    director.emitEvent(id, { type: 'tool_use', tool_use_id: readId, name: 'Read', input: { file_path: `${SESSION_A.workdir}/cache.py` } });

    await wait(900);
    director.emitEvent(id, {
      type: 'tool_result',
      tool_use_id: readId,
      is_error: false,
      content:
        'class Cache:\n    def __init__(self):\n        self._store = {}\n\n    def get(self, key):\n        return self._store.get(key)\n\n    def set(self, key, value):\n        self._store[key] = value\n',
    });

    await wait(600);
    director.emitEvent(id, {
      type: 'text',
      role: 'user',
      source: 'injected',
      text:
        'Base directory for this skill: ~/.claude/skills/cache-review\n\n# Cache review\n\n' +
        Array.from({ length: 40 }, (_, i) => `Rule ${i + 1}: state the eviction policy before changing it.`).join('\n'),
    });

    await wait(400);
    director.emitEvent(id, {
      type: 'permission_note',
      tool_use_id: uid('tu_'),
      name: 'Read',
      mode: 'acceptEdits',
      outcome: 'allow',
    });

    await wait(600);
    director.emitEvent(id, {
      type: 'thinking',
      text: 'The dict never evicts, so a long-running process will grow it unbounded. An OrderedDict with a max size and move-to-end on access gives LRU semantics with no dependency.',
    });

    await wait(800);
    const editId = uid('tu_');
    director.emitEvent(id, {
      type: 'tool_use',
      tool_use_id: editId,
      name: 'Edit',
      input: {
        file_path: `${SESSION_A.workdir}/cache.py`,
        old_string: 'class Cache:\n    def __init__(self):\n        self._store = {}',
        new_string: 'class Cache:\n    def __init__(self, max_size=512):\n        self._store = OrderedDict()\n        self._max_size = max_size',
      },
    });

    await wait(700);
    director.emitEvent(id, { type: 'tool_result', tool_use_id: editId, is_error: false, content: 'Applied 1 edit to cache.py' });

    await wait(500);
    const testId1 = uid('tu_');
    director.emitEvent(id, { type: 'tool_use', tool_use_id: testId1, name: 'Bash', input: { command: 'pytest tests/test_cache.py -q' } });

    await wait(900);
    // Demonstrates the tool card's error state (is_error: true).
    director.emitEvent(id, {
      type: 'tool_result',
      tool_use_id: testId1,
      is_error: true,
      content: 'FAILED tests/test_cache.py::test_eviction - AssertionError: expected 512 items after overfill, got 513',
    });

    await wait(500);
    director.emitEvent(id, { type: 'text', role: 'assistant', text: "Off-by-one in the eviction check — fixing that now." });

    await wait(700);
    const fixId = uid('tu_');
    director.emitEvent(id, {
      type: 'tool_use',
      tool_use_id: fixId,
      name: 'Edit',
      input: {
        file_path: `${SESSION_A.workdir}/cache.py`,
        old_string: 'if len(self._store) > self._max_size:',
        new_string: 'if len(self._store) >= self._max_size:',
      },
    });

    await wait(600);
    director.emitEvent(id, { type: 'tool_result', tool_use_id: fixId, is_error: false, content: 'Applied 1 edit to cache.py' });

    await wait(500);
    const testId2 = uid('tu_');
    director.emitEvent(id, { type: 'tool_use', tool_use_id: testId2, name: 'Bash', input: { command: 'pytest tests/test_cache.py -q' } });

    await wait(800);
    director.emitEvent(id, { type: 'tool_result', tool_use_id: testId2, is_error: false, content: '2 passed in 0.04s' });

    await wait(500);
    director.emitEvent(id, {
      type: 'text',
      role: 'assistant',
      // Markdown on purpose: a closing summary is where the transcript has to
      // render bold, bullets and `code` rather than print the asterisks.
      text: '**Done.** Both tests pass.\n\n'
        + '- Swapped the plain `dict` for an `OrderedDict` with a 512-entry cap\n'
        + '- Fixed the off-by-one in the eviction check\n'
        + '- `move_to_end` on read, so reads count as use\n\n'
        + 'The cap is a constructor argument, so a caller that wants the old '
        + 'unbounded behaviour can still ask for it.',
    });

    await wait(300);
    director.emitEvent(id, {
      type: 'usage',
      is_error: false,
      total_cost_usd: 0.0143,
      tokens: { input_tokens: 1820, output_tokens: 340 },
      result_text: null,
    });
    director.setStatus(id, 'awaiting_input');
  }

  async function runSessionB() {
    const id = SESSION_B.id;

    await wait(600);
    director.emitEvent(id, {
      type: 'system',
      subtype: 'init',
      data: {
        model: 'claude-opus-4-6',
        cwd: SESSION_B.workdir,
        sdk_session_id: uid('sdk-'),
        tools: ['Read', 'Edit', 'Write', 'Bash', 'Grep', 'Glob'],
      },
    });
    director.setStatus(id, 'working');

    await wait(300);
    director.emitEvent(id, {
      type: 'system',
      subtype: 'note',
      data: { message: 'Resumed from a prior transcript; carrying forward file edits made earlier in this workdir.' },
    });

    await wait(500);
    director.emitEvent(id, {
      type: 'thinking',
      text: 'The user wants deploy.sh hardened. Check for missing set -e, unquoted variables, and destructive commands before touching anything.',
    });

    await wait(900);
    director.emitEvent(id, { type: 'text', role: 'assistant', text: 'Let me check the current script for obvious footguns first.' });

    await wait(700);
    const readId = uid('tu_');
    director.emitEvent(id, { type: 'tool_use', tool_use_id: readId, name: 'Read', input: { file_path: `${SESSION_B.workdir}/deploy.sh` } });

    await wait(900);
    director.emitEvent(id, {
      type: 'tool_result',
      tool_use_id: readId,
      is_error: false,
      content: '#!/bin/bash\nBUILD_DIR=$1\nrm -rf $BUILD_DIR/*\nmake build\nscp -r $BUILD_DIR user@host:/srv/app\n',
    });

    await wait(500);
    director.emitEvent(id, {
      type: 'rate_limit',
      status: 'warning',
      rate_limit_type: 'output_tokens_per_minute',
      resets_at: Math.floor(Date.now() / 1000) + 240,
      utilization: 0.86,
    });

    await wait(600);
    director.emitEvent(id, {
      type: 'text',
      role: 'assistant',
      text: 'Found it — `rm -rf $BUILD_DIR/*` runs unquoted and unchecked, so an empty $BUILD_DIR would delete "/*". I want to run shellcheck before editing anything.',
    });

    await wait(500);
    const bashId = uid('tu_');
    const requestId = uid('req_');
    director.emitEvent(id, {
      type: 'approval_request',
      request_id: requestId,
      tool_use_id: bashId,
      name: 'Bash',
      input: { command: 'shellcheck deploy.sh', description: 'Lint the deploy script for common bash pitfalls before editing it' },
    });
    director.setStatus(id, 'awaiting_approval');

    // Blocks here — a live session, and the point of this second script —
    // until the UI's Approve / Deny / Approve-with-edits sends `resolve`.
    const decision = await director.waitForApproval(requestId);

    director.emitEvent(id, {
      type: 'approval_resolved',
      request_id: requestId,
      tool_use_id: bashId,
      decision: decision.decision,
      updated_input: decision.updated_input || null,
      reason: decision.reason || null,
    });

    if (decision.decision === 'allow') {
      director.setStatus(id, 'working');
      await wait(400);
      // NOTE: the protocol doc doesn't say whether a `tool_use` event
      // accompanies an approved `approval_request`, but the tool call only
      // actually runs once approval is granted, so it gets its own
      // `tool_use` here (reusing the same tool_use_id) — otherwise the
      // `tool_result` below would have nothing to pair with.
      director.emitEvent(id, {
        type: 'tool_use',
        tool_use_id: bashId,
        name: 'Bash',
        input: decision.updated_input || { command: 'shellcheck deploy.sh', description: 'Lint the deploy script for common bash pitfalls before editing it' },
      });
      await wait(700);
      const ranCommand = (decision.updated_input && decision.updated_input.command) || 'shellcheck deploy.sh';
      director.emitEvent(id, {
        type: 'tool_result',
        tool_use_id: bashId,
        is_error: false,
        content: [
          {
            type: 'text',
            text: `$ ${ranCommand}\n\nIn deploy.sh line 3:\nrm -rf $BUILD_DIR/*\n        ^-- SC2115: Use "\${BUILD_DIR:?}" if deletion is not intended when the variable is empty.`,
          },
        ],
      });
      await wait(600);
      director.emitEvent(id, {
        type: 'text',
        role: 'assistant',
        text: "Confirmed by shellcheck (SC2115). I'll quote the variable and add a \":?\" guard before the rm, then re-run this.",
      });
      await wait(400);
      director.emitEvent(id, {
        type: 'usage',
        is_error: false,
        total_cost_usd: 0.0089,
        tokens: { input_tokens: 2210, output_tokens: 210 },
        result_text: null,
      });
      director.setStatus(id, 'awaiting_input');
    } else {
      await wait(500);
      director.emitEvent(id, { type: 'text', role: 'assistant', text: "Understood, I won't run that. Let me know how you'd like to proceed." });
      await wait(300);
      director.emitEvent(id, {
        type: 'usage',
        is_error: false,
        total_cost_usd: 0.0031,
        tokens: { input_tokens: 1400, output_tokens: 60 },
        result_text: null,
      });
      director.setStatus(id, 'awaiting_input');
    }
  }

  async function runSessionC() {
    const id = SESSION_C.id;

    await wait(400);
    director.emitEvent(id, {
      type: 'system',
      subtype: 'init',
      data: {
        model: 'claude-opus-4-6',
        cwd: SESSION_C.workdir,
        sdk_session_id: uid('sdk-'),
        tools: ['Read', 'Edit', 'MultiEdit', 'Write', 'Bash', 'Grep', 'Glob', 'ExitPlanMode'],
      },
    });
    director.setStatus(id, 'working');

    await wait(500);
    director.emitEvent(id, {
      type: 'text',
      role: 'assistant',
      text: "I'll draft the guard, add a regression test, and lay out the plan before touching deploy.sh for real.",
    });

    await wait(700);
    const editId = uid('tu_');
    director.emitEvent(id, {
      type: 'tool_use',
      tool_use_id: editId,
      name: 'Edit',
      input: {
        file_path: `${SESSION_C.workdir}/deploy.sh`,
        old_string:
          'def build_target(build_dir):\n' +
          '    if not build_dir:\n' +
          '        build_dir = "."\n' +
          '    subprocess.run(["rm", "-rf", f"{build_dir}/*"], shell=False)\n' +
          '    subprocess.run(["make", "build"], cwd=build_dir)\n' +
          '    return build_dir',
        new_string:
          'def build_target(build_dir):\n' +
          '    if not build_dir:\n' +
          '        raise ValueError("BUILD_DIR must be set")\n' +
          '    target = pathlib.Path(build_dir).resolve()\n' +
          '    if not target.is_dir():\n' +
          '        raise FileNotFoundError(f"{target} does not exist")\n' +
          '    subprocess.run(["rm", "-rf", "--", f"{target}/*"], shell=False)\n' +
          '    subprocess.run(["make", "build"], cwd=target)\n' +
          '    return target',
      },
    });

    await wait(700);
    director.emitEvent(id, { type: 'tool_result', tool_use_id: editId, is_error: false, content: 'Applied 1 edit to deploy.sh' });

    await wait(500);
    // A MultiEdit-shaped input (an `edits` array), given directly by the
    // ISSUE-013 spec rather than observed from a real backend: two hunks
    // under one file path.
    const multiEditId = uid('tu_');
    director.emitEvent(id, {
      type: 'tool_use',
      tool_use_id: multiEditId,
      name: 'MultiEdit',
      input: {
        file_path: `${SESSION_C.workdir}/deploy.sh`,
        edits: [
          { old_string: 'BUILD_DIR=$1', new_string: 'BUILD_DIR="${1:?usage: deploy.sh BUILD_DIR}"' },
          { old_string: 'scp -r $BUILD_DIR user@host:/srv/app', new_string: 'scp -r -- "$BUILD_DIR" user@host:/srv/app' },
        ],
      },
    });

    await wait(600);
    director.emitEvent(id, { type: 'tool_result', tool_use_id: multiEditId, is_error: false, content: 'Applied 2 edits to deploy.sh' });

    await wait(600);
    const writeId = uid('tu_');
    director.emitEvent(id, {
      type: 'tool_use',
      tool_use_id: writeId,
      name: 'Write',
      input: {
        file_path: `${SESSION_C.workdir}/tests/test_deploy.py`,
        content:
          '"""Regression test for deploy.sh\'s BUILD_DIR guard."""\n' +
          'import os\n' +
          'import subprocess\n' +
          'import tempfile\n' +
          '\n' +
          '\n' +
          'def test_deploy_refuses_when_build_dir_unset():\n' +
          '    result = subprocess.run(\n' +
          '        ["bash", "deploy.sh"],\n' +
          '        env={},\n' +
          '        capture_output=True,\n' +
          '        text=True,\n' +
          '    )\n' +
          '    assert result.returncode != 0\n' +
          '    assert "BUILD_DIR must be set" in result.stderr\n' +
          '\n' +
          '\n' +
          'def test_deploy_runs_against_temp_dir():\n' +
          '    with tempfile.TemporaryDirectory() as tmp:\n' +
          '        result = subprocess.run(\n' +
          '            ["bash", "deploy.sh"],\n' +
          '            env={"BUILD_DIR": tmp},\n' +
          '            capture_output=True,\n' +
          '            text=True,\n' +
          '        )\n' +
          '        assert result.returncode == 0\n' +
          '\n' +
          '\n' +
          'def test_deploy_quotes_the_build_dir():\n' +
          '    with tempfile.TemporaryDirectory() as tmp:\n' +
          '        spaced = os.path.join(tmp, "has spaces")\n' +
          '        os.makedirs(spaced)\n' +
          '        result = subprocess.run(\n' +
          '            ["bash", "deploy.sh"],\n' +
          '            env={"BUILD_DIR": spaced},\n' +
          '            capture_output=True,\n' +
          '            text=True,\n' +
          '        )\n' +
          '        assert result.returncode == 0\n',
      },
    });

    await wait(600);
    director.emitEvent(id, { type: 'tool_result', tool_use_id: writeId, is_error: false, content: 'Wrote 20 lines to tests/test_deploy.py' });

    await wait(500);
    director.emitEvent(id, { type: 'text', role: 'assistant', text: 'Draft is ready. Here is the plan before I run any of this for real.' });

    await wait(600);
    const planToolId = uid('tu_');
    const requestId = uid('req_');
    director.emitEvent(id, {
      type: 'approval_request',
      request_id: requestId,
      tool_use_id: planToolId,
      name: 'ExitPlanMode',
      input: { plan: PLAN_TEXT },
    });
    director.setStatus(id, 'awaiting_approval');

    // Blocks here, same as session B's approval, until the UI's Accept
    // plan / Reject plan sends `resolve` — this is the paused-on-approval
    // case ISSUE-013 needs to verify the plan renderer inside the approval
    // card, not just the tool card.
    const decision = await director.waitForApproval(requestId);

    director.emitEvent(id, {
      type: 'approval_resolved',
      request_id: requestId,
      tool_use_id: planToolId,
      decision: decision.decision,
      updated_input: decision.updated_input || null,
      reason: decision.reason || null,
    });

    await wait(400);
    director.emitEvent(id, {
      type: 'text',
      role: 'assistant',
      text:
        decision.decision === 'allow'
          ? "Plan accepted. I'll apply the guard for real next turn."
          : 'Understood, holding off. Let me know what to change about the plan.',
    });

    await wait(300);
    director.emitEvent(id, {
      type: 'usage',
      is_error: false,
      total_cost_usd: 0.0102,
      tokens: { input_tokens: 1650, output_tokens: 410 },
      result_text: null,
    });
    director.setStatus(id, 'awaiting_input');
  }

  // A resumed session (ISSUE-009): the transcript opens mid-conversation with
  // no usage events of its own, so ISSUE-011's totals only ever cover what
  // this run has spent. The one real turn it runs is cache-heavy, since a
  // resumed session's next turn typically re-reads a lot of prior context —
  // exercising the footer's guidance that cache_read usually dwarfs the
  // other three counters.
  async function runSessionD() {
    const id = SESSION_D.id;

    await wait(350);
    director.emitEvent(id, {
      type: 'system',
      subtype: 'note',
      data: { message: 'Resumed from a prior transcript; only this run is counted below.' },
    });
    director.setStatus(id, 'working');

    await wait(500);
    director.emitEvent(id, {
      type: 'text',
      role: 'assistant',
      text: 'Picking up where the last session left off. Checking which API pages still reference the removed v1 endpoints.',
    });

    await wait(700);
    const grepId = uid('tu_');
    director.emitEvent(id, { type: 'tool_use', tool_use_id: grepId, name: 'Grep', input: { pattern: '/v1/', path: 'docs/api' } });

    await wait(800);
    director.emitEvent(id, {
      type: 'tool_result',
      tool_use_id: grepId,
      is_error: false,
      content: 'docs/api/auth.md:12\ndocs/api/webhooks.md:44',
    });

    await wait(500);
    director.emitEvent(id, {
      type: 'text',
      role: 'assistant',
      text: 'Two pages left. Removing the v1 references from both.',
    });

    await wait(400);
    director.emitEvent(id, {
      type: 'usage',
      is_error: false,
      total_cost_usd: 0.0038,
      tokens: {
        input_tokens: 640,
        output_tokens: 180,
        cache_read_input_tokens: 51200,
        cache_creation_input_tokens: 2100,
      },
      result_text: null,
    });
    director.setStatus(id, 'awaiting_input');
  }

  function runScript() {
    runSessionA();
    runSessionB();
    runSessionC();
    runSessionD();
  }

  // ---------------------------------------------------------------------
  // public entry point
  // ---------------------------------------------------------------------

  window.CodinianMock = {
    createSocket() {
      const socket = new FakeWebSocket();
      director.attach(socket);
      setTimeout(() => {
        director.sendSessionsList();
        runScript();
      }, 60);
      return socket;
    },
  };
})();
