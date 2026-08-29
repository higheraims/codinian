// Codinian transcript client.
//
// Renders the TranscriptEvent stream described in docs/transcript-protocol.md
// against a WebSocket at /api/ws. Falls back to the scripted fake backend in
// mock.js when there's no reachable server, or when the page is opened with
// ?mock=1. This file makes no assumption about which one is behind `state.ws`
// past construction time — both speak the same {onopen, onmessage, onclose,
// send} shape.

(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // DOM refs
  // ---------------------------------------------------------------------

  const appEl = document.getElementById('app');
  const alertsEl = document.getElementById('alerts');
  const agentsRunningEl = document.getElementById('agents-running');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  const sidebarBackdrop = document.getElementById('sidebar-backdrop');
  const sessionListEl = document.getElementById('session-list');
  const connStatusEl = document.getElementById('conn-status');
  const paneTitle = document.getElementById('pane-title');
  const paneStatus = document.getElementById('pane-status');
  const transcriptEl = document.getElementById('transcript');
  const composerForm = document.getElementById('composer');
  const composerInput = document.getElementById('composer-input');
  const composerSend = document.getElementById('composer-send');
  const composerStop = document.getElementById('composer-stop');
  const commandPalette = document.getElementById('command-palette');
  const commandList = document.getElementById('command-list');
  const commandPaletteTitle = document.getElementById('command-palette-title');
  const unauthorizedEl = document.getElementById('unauthorized-screen');
  const inboxToggle = document.getElementById('inbox-toggle');
  const inboxCountEl = document.getElementById('inbox-count');
  const inboxPanel = document.getElementById('inbox-panel');
  const inboxList = document.getElementById('inbox-list');
  const inboxPanelClose = document.getElementById('inbox-panel-close');
  const permissionModeControl = document.getElementById('permission-mode-control');
  const permissionModeSelect = document.getElementById('permission-mode-select');
  const permissionModeError = document.getElementById('permission-mode-error');
  const sessionTotalsEl = document.getElementById('session-totals');
  const sessionFooterEl = document.getElementById('session-footer');
  const planUsageEl = document.getElementById('plan-usage');

  // What the footer is allowed to show, from the desktop app's settings. A
  // failed fetch falls back to the same defaults the backend uses rather than
  // showing nothing: a page that cannot reach /api/prefs can still reach the
  // WebSocket, and the footer is not worth blanking over.
  let displayPrefs = { show_plan_usage: true, show_cost: false };

  async function loadDisplayPrefs() {
    try {
      const res = await apiFetch('/api/prefs');
      if (res.ok) {
        const body = await res.json();
        if (body && typeof body === 'object') displayPrefs = { ...displayPrefs, ...body };
      }
    } catch { /* keep the defaults */ }
    renderSessionTotals();
    renderPlanUsage();
  }
  const approvalJumpEl = document.getElementById('approval-jump');

  // ---------------------------------------------------------------------
  // small DOM builder — keeps event text/JSON going through textContent
  // rather than innerHTML, since tool input/output is arbitrary data.
  // ---------------------------------------------------------------------

  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null) continue;
        if (k === 'class') el.className = v;
        else el.setAttribute(k, v);
      }
    }
    if (children != null) {
      const list = Array.isArray(children) ? children : [children];
      for (const c of list) {
        if (c == null) continue;
        el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
      }
    }
    return el;
  }

  function countLines(text) {
    return String(text == null ? '' : text).split('\n').length;
  }

  // The first line with something on it, trimmed to fit one row.
  function firstMeaningfulLine(text) {
    const line = String(text == null ? '' : text).split('\n').find((l) => l.trim() !== '');
    if (!line) return null;
    const trimmed = line.trim();
    return trimmed.length > 160 ? trimmed.slice(0, 160) + '…' : trimmed;
  }

  function prettyJson(obj) {
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj);
    }
  }

  // `tool_result.content` is a string or an array of {type, text} blocks per
  // the spec. Render either as plain text.
  function resultText(content) {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content
        .filter((block) => !isImageBlock(block))
        .map((block) => (block && typeof block === 'object' && 'text' in block ? block.text : JSON.stringify(block)))
        .join('\n');
    }
    return prettyJson(content);
  }

  function isImageBlock(block) {
    return !!block && typeof block === 'object' && block.type === 'image';
  }

  // A tool result names the image, so the URL comes from whatever the tool
  // read, and `img src` fetches it the moment it is in the DOM. `javascript:`
  // does not run from `src`, but every other scheme is still an outbound
  // request the transcript made on the user's behalf, so only `https:` and
  // `data:` are rendered -- the same instinct as the markdown link renderer,
  // which matches `https?://` and nothing else (ISSUE-041).
  function isSafeImageUrl(url) {
    if (typeof url !== 'string') return false;
    return /^https:/i.test(url) || /^data:image\//i.test(url);
  }

  // A tool that returns a picture -- Read on a PNG, a screenshot driver --
  // sends an image block, and `JSON.stringify` on one produces 60,000
  // characters of base64 where the picture should be. Pull them out and hand
  // back sources the renderer can put in an `img`.
  function resultImages(content) {
    if (!Array.isArray(content)) return [];
    const out = [];
    for (const block of content) {
      if (!isImageBlock(block)) continue;
      const source = block.source || {};
      if (source.type === 'codinian_ref') {
        const src = imageRefSrc(source.path);
        if (src) out.push({ src, bytes: source.bytes || 0 });
      } else if (source.type === 'base64' && source.data && source.media_type) {
        // Still handled, because the demo data carries one and an old event
        // might. Nothing on the live or replay path sends base64 any more.
        out.push({ src: `data:${source.media_type};base64,${source.data}`, bytes: source.data.length });
      } else if (source.type === 'url' && isSafeImageUrl(source.url)) {
        out.push({ src: source.url, bytes: 0 });
      }
    }
    return out;
  }

  // Turn an image reference's path into something `img src` can fetch.
  //
  // The token goes in the query string here, which `apiFetch` deliberately
  // avoids. There is no alternative: `img` cannot carry an Authorization
  // header, which is the same reason the WebSocket URL takes the token this
  // way (see wsUrl). The path is required to be one of ours, so a reference
  // from a tool result cannot point the fetch -- token attached -- at
  // somewhere else.
  function imageRefSrc(path) {
    if (typeof path !== 'string' || !path.startsWith('/api/')) return null;
    if (!authToken) return path;
    return `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(authToken)}`;
  }

  // ---------------------------------------------------------------------
  // state
  // ---------------------------------------------------------------------

  const state = {
    ws: null,
    usingMock: false,
    everConnected: false,
    connAttempts: 0,
    unauthorized: false,
    sessionsMeta: new Map(), // id -> SessionMeta
    order: [], // sidebar order
    currentId: null,
    currentEvents: new Map(), // seq -> TranscriptEvent, for the subscribed session only
    // The last of each for the open session. Both arrive as ordinary events, so
    // a replayed snapshot fills these in exactly as a live stream does.
    rateLimit: null,   // the CLI's push: window, status, reset, overage
    planUsage: null,   // percentages, from a /usage the user ran
    inbox: new Map(), // request_id -> {session_id, request_id, tool_use_id, name, input, session}
    inboxOpen: false,
    renamingId: null, // the session whose sidebar row is being renamed in place
    confirmCloseId: null, // the session whose row is asking to be closed
    closedByMe: new Set(), // sessions this client closed, to explain the empty pane
  };

  // DOM node for each currently rendered inbox row, keyed by request_id, so a
  // server error reply for a `resolve` sent from the inbox can be routed back
  // to the right row without a full re-render (ISSUE-010).
  let inboxRowControls = new Map();

  // Tracks the permission-mode control's own in-flight change (ISSUE-012), so
  // a rejection from the server can be shown next to the control and the
  // dropdown put back to the mode the session is actually in.
  let pendingPermissionMode = null; // { sessionId, previousMode }

  // Embedded single-session mode: the desktop app's WebKitGTK pane loads this
  // same bundle with ?embed=1&session=<id> and wants sidebar-free chrome
  // pinned to one session, subscribed immediately on connect rather than
  // waiting for a sidebar click (there is no sidebar to click).
  const queryParams = new URLSearchParams(location.search);
  const embedMode = queryParams.get('embed') === '1';
  const embedSessionId = queryParams.get('session');
  if (embedMode) appEl.classList.add('embed-mode');

  // Reading a stored conversation rather than driving a live one (ISSUE-015).
  // The id is the CLI's own session uuid: the point is to open something that
  // has no live session and may never get one, so there is no socket, no
  // subscribe and nothing to send. Everything below the transcript renderer is
  // shared with a live session, which is what "the same renderer" means.
  const historyId = queryParams.get('history');
  if (historyId) appEl.classList.add('history-mode');

  // Access token (ISSUE-007): the desktop app hands it out as ?token=... on
  // links it opens (the embed pane, the "open in browser" link). A token
  // arriving that way is saved to localStorage and stripped from the visible
  // URL so it does not sit in the address bar or in screenshots; every other
  // query parameter is left as-is. Absent that, a previously saved token is
  // used. `?mock=1` needs neither and never reaches the code that checks it.
  const TOKEN_STORAGE_KEY = 'codinian_token';
  let authToken = '';
  const tokenFromUrl = queryParams.get('token');
  if (tokenFromUrl) {
    authToken = tokenFromUrl;
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, authToken);
    } catch {
      // Storage unavailable (private browsing, storage full). The token
      // still works for this page load; it just will not survive a reload.
    }
    queryParams.delete('token');
    const rest = queryParams.toString();
    history.replaceState(null, '', location.pathname + (rest ? `?${rest}` : '') + location.hash);
  } else {
    try {
      authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || '';
    } catch {
      authToken = '';
    }
  }

  // Render bookkeeping for the currently displayed transcript: which DOM
  // node represents which tool_use_id / request_id, so a later tool_result
  // or approval_resolved can be spliced into the right card instead of
  // forcing a full re-render (which would collapse open <details> etc).
  let renderState = null;

  // ---------------------------------------------------------------------
  // connection
  // ---------------------------------------------------------------------

  function wsUrl() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const q = authToken ? `?token=${encodeURIComponent(authToken)}` : '';
    return `${proto}//${location.host}/api/ws${q}`;
  }

  function startConnection() {
    const params = new URLSearchParams(location.search);
    if (params.get('mock') === '1') {
      startMock();
      return;
    }
    attemptRealConnection();
  }

  // GET /api/auth needs a valid token to return 200; it exists specifically
  // so the client can tell "wrong or missing token" apart from "no server
  // running here at all", which get very different treatment below.
  async function checkAuthToken() {
    const res = await fetch('/api/auth', {
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    });
    return res.status === 401 ? 'unauthorized' : 'ok';
  }

  // Any authenticated GET against our own API. The token rides in a header
  // rather than the query string so it stays out of logs and referrers.
  function apiFetch(path) {
    return fetch(path, {
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    });
  }

  async function attemptRealConnection() {
    setConnStatus('connecting');

    let authStatus;
    try {
      authStatus = await checkAuthToken();
    } catch {
      // Nothing answered at all, the genuinely-absent-backend case, handled
      // the same as before: demo mode on first load, keep retrying after
      // that.
      onRealConnectFailed();
      return;
    }

    if (authStatus === 'unauthorized') {
      onUnauthorized();
      return;
    }

    openRealSocket();
  }

  function openRealSocket() {
    let settled = false;
    let ws;
    try {
      ws = new WebSocket(wsUrl());
    } catch {
      onRealConnectFailed();
      return;
    }
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try {
        ws.close();
      } catch {
        // already gone
      }
      onRealConnectFailed();
    }, 3000);

    ws.addEventListener('open', () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      state.everConnected = true;
      state.connAttempts = 0;
      state.usingMock = false;
      setConnStatus('open');
      bindSocket(ws);
      onConnectionOpen();
    });

    ws.addEventListener('error', () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      onRealConnectFailed();
    });
  }

  function onRealConnectFailed() {
    if (!state.everConnected) {
      // Never had a backend at all in this page load — demo mode.
      startMock();
    } else {
      setConnStatus('error');
      scheduleReconnect();
    }
  }

  // A wrong or expired token is a dead end, not something to retry: show the
  // explanation once and stop. If this fires after the socket had been open
  // (the token was rotated mid-session), attemptRealConnection got here
  // through scheduleReconnect below, and returning without calling it again
  // is what ends that loop.
  function onUnauthorized() {
    state.unauthorized = true;
    appEl.hidden = true;
    unauthorizedEl.hidden = false;
  }

  function scheduleReconnect() {
    state.connAttempts += 1;
    const delay = Math.min(15000, 500 * 2 ** state.connAttempts) + Math.random() * 300;
    setTimeout(attemptRealConnection, delay);
  }

  function startMock() {
    state.usingMock = true;
    setConnStatus('mock');
    const ws = window.CodinianMock.createSocket();
    bindSocket(ws);
    ws.onopen = () => onConnectionOpen();
  }

  // Auto-subscribe for embed mode, on every successful (re)connection —
  // sent as soon as the socket opens, without waiting for the `sessions`
  // list or a sidebar click, since embed mode has no sidebar.
  function onConnectionOpen() {
    // The inbox is a cross-session view; the embed pane is already pinned to
    // one session inside an app that raises its own native notifications for
    // approvals, so there is nothing for it to show.
    if (!embedMode) send({ t: 'subscribe_inbox' });

    // A subscription lives on the connection, so a reconnect starts the backend
    // with an empty set and the open session stops receiving events until it is
    // re-sent. The pinned id is `embedSessionId` in the pane and `currentId` in
    // the full UI; either way, resubscribe to whatever this client was watching.
    const watching = embedMode ? embedSessionId : state.currentId;
    if (!watching) return;
    if (state.currentId === watching) {
      // Already "selected" locally from before the drop, so selectSession()
      // would no-op on the unchanged id. Reset the view and resubscribe by hand.
      state.currentEvents = new Map();
      state.rateLimit = null;
      state.planUsage = null;
      resetTranscript();
      transcriptEl.appendChild(h('div', { class: 'empty-state' }, 'Loading…'));
      send({ t: 'subscribe', session_id: watching });
    } else {
      selectSession(watching);
    }
  }

  loadDisplayPrefs();

  function bindSocket(ws) {
    state.ws = ws;
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      handleServerMessage(msg);
    };
    ws.onclose = () => {
      if (state.usingMock) return; // the fake socket never closes itself
      setConnStatus('error');
      scheduleReconnect();
    };
    ws.onerror = () => {
      // onclose follows for real sockets; nothing extra to do here.
    };
  }

  function send(obj) {
    if (!state.ws) return;
    try {
      state.ws.send(JSON.stringify(obj));
    } catch (e) {
      console.error('codinian: send failed', e);
    }
  }

  function setConnStatus(kind) {
    connStatusEl.classList.remove('is-open', 'is-error', 'is-mock');
    const labels = {
      connecting: 'connecting…',
      open: 'connected',
      error: 'reconnecting…',
      mock: 'mock mode',
      history: 'stored transcript',
    };
    connStatusEl.textContent = labels[kind] || kind;
    if (kind === 'open') connStatusEl.classList.add('is-open');
    if (kind === 'error') connStatusEl.classList.add('is-error');
    if (kind === 'mock') connStatusEl.classList.add('is-mock');
  }

  // ---------------------------------------------------------------------
  // server -> client messages
  // ---------------------------------------------------------------------

  function handleServerMessage(msg) {
    switch (msg.t) {
      case 'sessions':
        applySessionsList(msg.sessions || []);
        break;
      case 'snapshot':
        applySnapshot(msg);
        break;
      case 'event':
        applyLiveEvent(msg.event);
        break;
      case 'session_update':
        applySessionUpdate(msg.session);
        break;
      case 'inbox':
        applyInboxSnapshot(msg.approvals || []);
        break;
      case 'inbox_event':
        applyInboxEvent(msg.event, msg.session);
        break;
      case 'error':
        applyServerError(msg);
        break;
      default:
        console.warn('codinian: unknown server message', msg);
    }
  }

  // Errors with no card to attach to go here rather than to the console: a
  // session that failed to start used to show an error dot in the sidebar and
  // nothing else, which is indistinguishable from a session that died later.
  function showAlert(text, detail) {
    if (!alertsEl) return;
    const body = h('span', { class: 'alert-text' }, text);
    if (detail) body.appendChild(h('span', { class: 'alert-detail' }, detail));
    const close = h('button', { class: 'alert-dismiss', type: 'button',
                                'aria-label': 'Dismiss' }, '\u00d7');
    const row = h('div', { class: 'alert' }, [body, close]);
    close.addEventListener('click', () => row.remove());
    alertsEl.appendChild(row);
    return row;
  }

  // Errors the server sends in reply to something we asked for, phrased for
  // the person looking at the card rather than echoing the wire code.
  const SERVER_ERROR_TEXT = {
    stale_or_unknown_request:
      'That approval is no longer pending. It may have been answered from another client, or the session may have restarted.',
    unknown_permission_mode: 'The server did not recognize that permission mode.',
    unknown_session: 'That session is no longer available.',
    rename_failed: 'That session could not be renamed. It may have ended.',
    close_failed: 'That session was already gone. Nothing was closed.',
    session_not_running:
      'That session is not taking messages. It has stopped, or it is a terminal session, which is driven from the desktop app.',
  };

  function applyServerError(msg) {
    const text = SERVER_ERROR_TEXT[msg.error] || `The server rejected that request (${msg.error}).`;

    if (msg.error === 'unknown_permission_mode' || msg.error === 'unknown_session') {
      if (pendingPermissionMode) {
        permissionModeSelect.value = pendingPermissionMode.previousMode;
        pendingPermissionMode = null;
      }
      permissionModeError.textContent = text;
      permissionModeError.hidden = false;
      return;
    }

    const card = msg.request_id ? renderState.approvalCards.get(msg.request_id) : null;
    if (card) {
      // A stale request is gone for good, so the card is retired rather than
      // handed back for a second refusal. Every other error is worth another
      // try (ISSUE-052).
      if (msg.error === 'stale_or_unknown_request' && card.expired) {
        renderState.pendingApprovals.delete(msg.request_id);
        card.expired(text);
        updateApprovalJump();
        return;
      }
      if (card.rejected) {
        card.rejected(text);
        return;
      }
    }
    const row = msg.request_id ? inboxRowControls.get(msg.request_id) : null;
    if (row && row.rejected) {
      row.rejected(text);
      return;
    }

    if (msg.error === 'close_failed') {
      // The session was already gone, so nothing here closed it and the note
      // kept for explaining the empty pane would be a false claim.
      if (msg.session_id) state.closedByMe.delete(msg.session_id);
      showAlert(text);
      return;
    }
    if (msg.error === 'session_start_failed') {
      const meta = msg.session_id ? state.sessionsMeta.get(msg.session_id) : null;
      const name = (meta && (meta.name || meta.id)) || msg.session_id || 'The session';
      showAlert(`${name} could not be started.`, msg.detail);
      return;
    }
    if (msg.error === 'request_failed') {
      showAlert(
        msg.request
          ? `The server could not handle that ${msg.request} request.`
          : 'The server could not handle that request.',
        'It logged the failure; the action did not happen.');
      return;
    }
    showAlert(text);
    console.warn('codinian: server error', msg);
  }

  function applySessionsList(list) {
    state.sessionsMeta.clear();
    state.order = [];
    for (const s of list) {
      state.sessionsMeta.set(s.id, s);
      state.order.push(s.id);
    }
    // A closed session takes its pending approvals with it: the futures they
    // were waiting on die with the session, so an inbox row for one is a
    // button that can no longer do anything. Only redrawn when something was
    // actually dropped, because this list arrives on every status and cost
    // change and rebuilding an open panel under a click is its own bug.
    let pruned = false;
    for (const [requestId, entry] of state.inbox) {
      if (state.sessionsMeta.has(entry.session_id)) continue;
      state.inbox.delete(requestId);
      pruned = true;
    }
    if (pruned) {
      renderInboxToggle();
      renderInboxList();
    }
    if (state.confirmCloseId && !state.sessionsMeta.has(state.confirmCloseId)) {
      state.confirmCloseId = null;
    }
    renderSidebar();
    if (state.currentId && !state.sessionsMeta.has(state.currentId)) {
      // We were watching this session and it is gone from the list. This is
      // not the user picking a different one, and not the first load
      // (currentId is only set once something has been selected). Either
      // somebody closed it, here or in another client, or Codinian restarted
      // and dropped every SDK session it had.
      const closedHere = state.closedByMe.delete(state.currentId);
      state.currentId = null;
      resetTranscript(closedHere
        ? 'This session is closed. Its conversation is still on disk and can be ' +
          'resumed from History or the project it ran in.'
        : 'This session ended, either closed from another client or dropped when ' +
          'Codinian restarted. Any approval that was waiting on a decision was ' +
          'cancelled. It can be resumed from History.'
      );
      renderPaneHeader();
      updateComposerState();
    }
  }

  function applySnapshot(msg) {
    if (msg.session) {
      state.sessionsMeta.set(msg.session.id, msg.session);
      if (!state.order.includes(msg.session.id)) state.order.push(msg.session.id);
    }
    // A snapshot answers one specific subscribe call. If the user has since
    // selected a different session, drop it — a fresh subscribe for the
    // current selection is already in flight (see selectSession).
    if (msg.session_id !== state.currentId) return;
    state.currentEvents = new Map();
    state.rateLimit = null;
    state.planUsage = null;
    for (const ev of msg.events || []) {
      state.currentEvents.set(ev.seq, ev);
      // The last of each wins, and a snapshot is in order, so this ends on the
      // same state a client that watched it live would hold.
      noteUsageEvent(ev);
    }
    renderFullTranscript();
    renderPaneHeader();
    renderPermissionModeControl();
    renderSessionTotals();
    renderPlanUsage();
    renderSidebar();
    updateComposerState();
  }

  function applyLiveEvent(ev) {
    if (!ev || ev.session_id !== state.currentId) return; // only the subscribed session streams here

    // Transient events are never stored, carry no seq, and are not part of the
    // ordering a reconnecting client catches up on (ISSUE-033). They are a
    // preview of a block that will arrive properly in a moment, so they skip
    // the de-dupe and the event map entirely.
    if (ev.transient) {
      if (ev.type === 'text_delta') applyTextDelta(ev);
      return;
    }

    if (state.currentEvents.has(ev.seq)) return; // already seen — de-dupe on seq per spec
    state.currentEvents.set(ev.seq, ev);

    // Any finished block supersedes whatever was being previewed: the durable
    // text is about to render, and leaving the preview would show it twice.
    if (ev.type === 'text' || ev.type === 'thinking' || ev.type === 'tool_use') {
      clearStreamingText();
    }

    noteUsageEvent(ev);

    if (ev.type === 'status') {
      const meta = state.sessionsMeta.get(ev.session_id);
      if (meta) meta.status = ev.status;
    }

    // The server does broadcast a fresh SessionMeta when a status or usage
    // event lands, but reading the numbers straight off the event updates the
    // open session without waiting for that round trip, the same way `status`
    // above is handled.
    if (ev.type === 'usage') {
      const meta = state.sessionsMeta.get(ev.session_id);
      if (meta) {
        if (ev.total_cost_usd != null) meta.cost_usd = ev.total_cost_usd;
        setSessionTokens(meta, ev);
      }
    }
    if (ev.type === 'system' && ev.subtype === 'permission_mode') {
      const meta = state.sessionsMeta.get(ev.session_id);
      if (meta && ev.data) meta.permission_mode = ev.data.permission_mode;
    }

    if (ev.type === 'approval_request') notifyApprovalRequest(ev);

    // A snapshot taken before the session had produced anything left a
    // "No activity yet." placeholder behind; the first real event replaces
    // it rather than trailing after it.
    clearEmptyState();

    const wasBottom = isAtBottom();
    appendEventToDom(ev);
    if (wasBottom) scrollToBottom();
    updateApprovalJump();

    renderSidebar();
    renderPaneHeader();
    renderPermissionModeControl();
    renderSessionTotals();
    updateComposerState();
  }

  // Folds one turn's usage into a session's running totals. `usageTokens`
  // uses the SDK's own field names (input_tokens, output_tokens, ...), same
  // as the per-turn `usage` event; SessionMeta.tokens uses the shorter names
  // documented in docs/transcript-protocol.md, so the two are mapped here.
  const USAGE_TOKEN_KEY_MAP = {
    input_tokens: 'input',
    output_tokens: 'output',
    cache_read_input_tokens: 'cache_read',
    cache_creation_input_tokens: 'cache_creation',
  };

  // A usage event carries two different things. `tokens_total` is cumulative
  // for the conversation, summed by the backend across every model the session
  // used, and is the number to show. `tokens` describes only the last API call
  // and comes back all zeros often enough that adding it up understates the
  // total; it is the fallback for a backend that sent no total.
  function setSessionTokens(meta, ev) {
    if (!meta.tokens) meta.tokens = { input: 0, output: 0, cache_read: 0, cache_creation: 0 };
    if (ev.tokens_total && typeof ev.tokens_total === 'object') {
      Object.assign(meta.tokens, ev.tokens_total);
      return;
    }
    if (!ev.tokens || typeof ev.tokens !== 'object') return;
    for (const rawKey of Object.keys(USAGE_TOKEN_KEY_MAP)) {
      const value = ev.tokens[rawKey];
      if (typeof value === 'number') {
        const field = USAGE_TOKEN_KEY_MAP[rawKey];
        meta.tokens[field] = (meta.tokens[field] || 0) + value;
      }
    }
  }

  // ---------------------------------------------------------------------
  // browser notifications (ISSUE-008): the away-from-desk case. A phone
  // with the tab backgrounded should still surface a pending approval.
  //
  // Notification only exists in a secure context (https, or localhost).
  // Over plain http on a LAN address, which is exactly the phone-on-the-wifi
  // setup this feature targets, `window.Notification` is undefined. This
  // degrades to doing nothing rather than throwing.
  // ---------------------------------------------------------------------

  let notificationPermissionAsked = false;

  // Call from a real user gesture (selecting a session, sending a message).
  // Asking at page load gets ignored or auto-denied by browsers and burns
  // the one prompt a site gets; asking again after a denial just nags.
  function maybeRequestNotificationPermission() {
    // The desktop pane is inside an app that already raises native
    // notifications for the same approvals, and its session is selected
    // automatically on connect rather than by a click, so there is no gesture
    // to ask on and nothing to gain by asking.
    if (embedMode) return;
    if (!('Notification' in window)) return;
    if (notificationPermissionAsked) return;
    if (Notification.permission !== 'default') return;
    notificationPermissionAsked = true;
    Notification.requestPermission();
  }

  function notifyApprovalRequest(ev) {
    // Skipped in the pane: a hidden GTK stack page reports document.hidden,
    // so without this an approval would notify twice, once natively and once
    // from inside the pane.
    if (embedMode) return;
    if (!document.hidden) return;
    if (!('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;
    const meta = state.sessionsMeta.get(ev.session_id);
    const sessionName = (meta && (meta.name || meta.id)) || ev.session_id;
    const notif = new Notification('Approval needed', {
      body: `${sessionName} wants to run ${ev.name}`,
      tag: `codinian-approval-${ev.request_id}`,
    });
    notif.addEventListener('click', () => {
      window.focus();
      selectSession(ev.session_id);
      notif.close();
    });
  }

  function applySessionUpdate(meta) {
    if (!meta) return;
    state.sessionsMeta.set(meta.id, meta);
    if (!state.order.includes(meta.id)) state.order.push(meta.id);
    renderSidebar();
    // A session that raised an inbox entry may have changed name in the
    // meantime; the inbox stores its own copy of `session` per entry
    // (ISSUE-010's wire format), so keep those in step too.
    for (const entry of state.inbox.values()) {
      if (entry.session_id === meta.id) entry.session = meta;
    }
    if (!inboxPanel.hidden) renderInboxList();
    if (meta.id === state.currentId) {
      renderPaneHeader();
      renderPermissionModeControl();
      renderSessionTotals();
      updateComposerState();
    }
  }

  // -----------------------------------------------------------------------
  // cross-session approval inbox (ISSUE-010)
  // -----------------------------------------------------------------------

  function applyInboxSnapshot(approvals) {
    state.inbox = new Map();
    for (const entry of approvals) {
      if (entry && entry.request_id) state.inbox.set(entry.request_id, entry);
    }
    renderInboxToggle();
    renderInboxList();
  }

  // `inbox_event` carries a full approval_request/approval_resolved
  // TranscriptEvent plus the session meta, sent to every inbox subscriber
  // regardless of what session they have open (docs/transcript-protocol.md).
  // A client also watching this session gets the same approval as an
  // ordinary `event` too; the two are independent updates to independent
  // views (transcript vs. inbox), so nothing here should skip on that.
  function applyInboxEvent(ev, session) {
    if (!ev) return;
    if (ev.type === 'approval_request') {
      state.inbox.set(ev.request_id, {
        session_id: ev.session_id,
        request_id: ev.request_id,
        tool_use_id: ev.tool_use_id,
        name: ev.name,
        input: ev.input,
        session,
      });
    } else if (ev.type === 'question_request') {
      // A question parks the turn exactly as an approval does, so it belongs
      // here or it is invisible from any other session (ISSUE-050). The row
      // only offers a way in: the picker lives in the transcript, and a second
      // copy of it in a panel this narrow would be worse than a short walk.
      state.inbox.set(ev.request_id, {
        session_id: ev.session_id,
        request_id: ev.request_id,
        tool_use_id: ev.tool_use_id,
        name: 'AskUserQuestion',
        questions: ev.questions || [],
        isQuestion: true,
        session,
      });
    } else if (ev.type === 'approval_resolved' || ev.type === 'question_resolved'
               || ev.type === 'approval_expired') {
      // An expired request leaves the inbox for the same reason an answered one
      // does: the row's only job is to lead somewhere worth going, and this one
      // no longer does (ISSUE-052).
      state.inbox.delete(ev.request_id);
    } else {
      return;
    }
    renderInboxToggle();
    renderInboxList();
  }

  function openInboxPanel() {
    if (state.inbox.size === 0) return;
    state.inboxOpen = true;
    inboxPanel.hidden = false;
    inboxToggle.setAttribute('aria-expanded', 'true');
    document.addEventListener('click', onDocumentClickForInbox, true);
  }

  function closeInboxPanel() {
    state.inboxOpen = false;
    inboxPanel.hidden = true;
    inboxToggle.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', onDocumentClickForInbox, true);
  }

  function onDocumentClickForInbox(e) {
    if (inboxPanel.contains(e.target) || inboxToggle.contains(e.target)) return;
    closeInboxPanel();
  }

  inboxToggle.addEventListener('click', () => {
    if (state.inboxOpen) closeInboxPanel();
    else openInboxPanel();
  });
  inboxPanelClose.addEventListener('click', closeInboxPanel);

  // Count is the only thing that has to be right always-on; the list itself
  // only needs to exist while the panel is open.
  function renderInboxToggle() {
    const n = state.inbox.size;
    inboxCountEl.textContent = n > 0 ? String(n) : '';
    inboxToggle.disabled = n === 0;
    inboxToggle.classList.toggle('has-items', n > 0);
    inboxToggle.setAttribute(
      'aria-label',
      n > 0 ? `Approval inbox, ${n} waiting` : 'Approval inbox, nothing waiting'
    );
    // Never leave the panel open on an empty inbox — there is nothing left
    // to show, and an open box with nothing in it is worse than no box.
    if (n === 0 && state.inboxOpen) closeInboxPanel();
  }

  function inboxSessionLabel(entry) {
    const s = entry.session;
    return (s && (s.name || s.id)) || entry.session_id;
  }

  function buildInboxRow(entry) {
    const el = h('div', { class: 'inbox-row' });

    const sessionBtn = h('button', { type: 'button', class: 'inbox-row-session' }, inboxSessionLabel(entry));
    sessionBtn.addEventListener('click', () => {
      closeInboxPanel();
      selectSession(entry.session_id);
    });
    el.appendChild(h('div', { class: 'inbox-row-head' }, [sessionBtn, h('span', { class: 'inbox-row-tool' }, entry.name)]));

    if (entry.isQuestion) {
      const qs = entry.questions || [];
      const body = h('div', { class: 'inbox-row-body' });
      for (const q of qs) {
        body.appendChild(h('div', { class: 'question-text' }, q.question || ''));
      }
      el.appendChild(body);
      const open = h('button', { class: 'btn-approve', type: 'button' }, 'Open session to answer');
      open.addEventListener('click', () => {
        closeInboxPanel();
        selectSession(entry.session_id);
      });
      el.appendChild(h('div', { class: 'approval-actions' }, [open]));
      // No controls to hand back an error to: this row cannot answer, so it
      // cannot be told its answer was stale.
      return { el, controls: null };
    }

    const body = h('div', { class: 'inbox-row-body' });
    const special = renderSpecialToolInput(entry.name, entry.input);
    if (special) {
      body.appendChild(buildCollapsibleSection(special.el, special.lineCount, special.collapseLabels));
    } else {
      body.appendChild(h('pre', { class: 'code-block' }, prettyJson(entry.input)));
    }
    el.appendChild(body);

    const btnApprove = h('button', { class: 'btn-approve', type: 'button' }, 'Approve');
    const btnDeny = h('button', { class: 'btn-deny', type: 'button' }, 'Deny');
    const errorEl = h('div', { class: 'approval-edit-error' });
    errorEl.style.display = 'none';
    el.appendChild(h('div', { class: 'approval-actions' }, [btnApprove, btnDeny]));
    el.appendChild(errorEl);

    let settled = false;
    function setDisabled(disabled) {
      btnApprove.disabled = disabled;
      btnDeny.disabled = disabled;
    }

    function resolveWith(decision) {
      if (settled) return;
      settled = true;
      errorEl.style.display = 'none';
      setDisabled(true);
      send({
        t: 'resolve',
        session_id: entry.session_id,
        request_id: entry.request_id,
        decision,
        updated_input: null,
        reason: null,
      });
    }

    btnApprove.addEventListener('click', () => resolveWith('allow'));
    btnDeny.addEventListener('click', () => resolveWith('deny'));

    return {
      el,
      controls: {
        rejected(message) {
          settled = false;
          setDisabled(false);
          errorEl.textContent = message;
          errorEl.style.display = 'block';
        },
      },
    };
  }

  function renderInboxList() {
    inboxList.innerHTML = '';
    inboxRowControls = new Map();
    for (const entry of state.inbox.values()) {
      const row = buildInboxRow(entry);
      inboxRowControls.set(entry.request_id, row.controls);
      inboxList.appendChild(row.el);
    }
  }

  // ---------------------------------------------------------------------
  // client -> server actions
  // ---------------------------------------------------------------------

  function selectSession(id) {
    maybeRequestNotificationPermission();
    if (id === state.currentId) return;
    if (state.currentId) {
      send({ t: 'unsubscribe', session_id: state.currentId });
    }
    state.currentId = id;
    state.currentEvents = new Map();
    // The footer's usage readings belong to the session leaving the pane, not
    // the one arriving. Clearing them here, rather than waiting for the new
    // session's snapshot, stops the previous session's "limit reached" from
    // sitting under the incoming transcript -- and stops the 60s repaint timer
    // from redrawing it against the new session in the meantime.
    state.rateLimit = null;
    state.planUsage = null;
    resetTranscript();
    if (id) transcriptEl.appendChild(h('div', { class: 'empty-state' }, 'Loading…'));
    renderSidebar();
    renderPaneHeader();
    renderPermissionModeControl();
    renderSessionTotals();
    renderPlanUsage();
    updateComposerState();
    appEl.classList.remove('sidebar-open');
    sidebarToggle.setAttribute('aria-expanded', 'false');
    if (id) send({ t: 'subscribe', session_id: id });
  }

  // The composer is a textarea, so it starts one line tall and grows with the
  // message up to the ceiling the stylesheet sets, past which it scrolls.
  // Height goes to `auto` first because scrollHeight only ever reports the
  // content's height when the box is not already holding it open.
  function growComposer() {
    composerInput.style.height = 'auto';
    // scrollHeight measures the content box, and the element is sized
    // border-box, so the border has to be added back. Without it the box loses
    // a pixel a side on the first keystroke and never gets it back.
    const border = composerInput.offsetHeight - composerInput.clientHeight;
    composerInput.style.height = `${composerInput.scrollHeight + border}px`;
  }

  composerForm.addEventListener('submit', (e) => {
    e.preventDefault();
    maybeRequestNotificationPermission();
    const text = composerInput.value.trim();
    if (!text || !state.currentId) return;
    closePalette();
    send({ t: 'send', session_id: state.currentId, text });
    composerInput.value = '';
    growComposer();
  });

  if (composerStop) {
    composerStop.addEventListener('click', () => {
      if (!state.currentId) return;
      send({ t: 'interrupt', session_id: state.currentId });
      // The turn stops at a safe point rather than instantly, so the button
      // says what it is waiting for instead of pretending it is already done.
      composerStop.disabled = true;
      composerStop.textContent = 'Stopping…';
      setTimeout(() => {
        composerStop.disabled = false;
        composerStop.textContent = 'Stop';
      }, 4000);
    });
  }

  sidebarToggle.addEventListener('click', () => {
    const open = appEl.classList.toggle('sidebar-open');
    sidebarToggle.setAttribute('aria-expanded', String(open));
  });
  sidebarBackdrop.addEventListener('click', () => {
    appEl.classList.remove('sidebar-open');
    sidebarToggle.setAttribute('aria-expanded', 'false');
  });

  // ---------------------------------------------------------------------
  // commands and skills (ISSUE-016)
  //
  // In the CLI this is typing `/` and reading the list. A GUI has to build it,
  // or the features are invisible: you have to already know a skill exists to
  // invoke it. The backend hands back one list because the CLI does -- a skill
  // sits beside /compact and /model and is invoked the same way.
  // ---------------------------------------------------------------------

  // Cached per session: the list does not change for a session's lifetime, and
  // re-fetching it on every keystroke would be absurd.
  const commandCache = new Map();
  let paletteIndex = 0;

  async function loadCommands(sessionId) {
    if (commandCache.has(sessionId)) return commandCache.get(sessionId);
    let commands = [];
    try {
      const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/commands`);
      if (res.ok) {
        const body = await res.json();
        if (Array.isArray(body.commands)) commands = body.commands;
      }
    } catch { /* offline or no runtime; an empty list is a real answer */ }
    // The CLI answers in its own order -- skills first, then commands, neither
    // sorted -- which reads as no order at all in a list of 48. Sorted by name
    // here rather than at the route, so the ordering is the one thing the list
    // is rendered by and every client gets it. `localeCompare` with `base`
    // sensitivity keeps `/Foo` beside `/foo` instead of in a separate block of
    // capitals.
    commands = commands.slice().sort((a, b) =>
      String(a.name || '').localeCompare(String(b.name || ''), undefined,
                                         { sensitivity: 'base', numeric: true }));
    commandCache.set(sessionId, commands);
    return commands;
  }

  function paletteOpen() {
    return !commandPalette.hidden;
  }

  function closePalette() {
    commandPalette.hidden = true;
    paletteIndex = 0;
  }

  function paletteRows() {
    return Array.from(commandList.querySelectorAll('.command-row'));
  }

  function highlightPalette() {
    const rows = paletteRows();
    rows.forEach((row, i) => row.classList.toggle('is-active', i === paletteIndex));
    const active = rows[paletteIndex];
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  function applyCommand(name) {
    // Inserted rather than sent: most commands take arguments, and sending on
    // click would fire `/model` with no model. The user completes the line.
    composerInput.value = `/${name} `;
    closePalette();
    composerInput.focus();
    growComposer();
  }

  // Incremented per call, so a slower earlier lookup cannot render its stale
  // list over a newer one's. The first call for a session awaits a fetch while
  // later ones hit the cache, which is exactly the ordering that goes wrong.
  let paletteRequest = 0;

  async function refreshPalette() {
    const mine = ++paletteRequest;
    const text = composerInput.value;
    if (!state.currentId || !text.startsWith('/') || text.includes(' ')) {
      closePalette();
      return;
    }
    const needle = text.slice(1).toLowerCase();
    const commands = await loadCommands(state.currentId);
    if (mine !== paletteRequest) return;
    const matches = commands.filter((c) => {
      if (!needle) return true;
      if (String(c.name || '').toLowerCase().includes(needle)) return true;
      return (c.aliases || []).some((a) => String(a).toLowerCase().includes(needle));
    });

    commandList.innerHTML = '';
    if (!commands.length) {
      commandList.appendChild(h('div', { class: 'command-empty' },
        'This session reports no commands.'));
    } else if (!matches.length) {
      commandList.appendChild(h('div', { class: 'command-empty' },
        `Nothing matches "${needle}".`));
    } else {
      for (const c of matches.slice(0, 60)) {
        const row = h('div', { class: 'command-row', role: 'option' }, [
          h('span', { class: 'command-name' }, `/${c.name}`),
          c.argumentHint ? h('span', { class: 'command-args' }, c.argumentHint) : null,
          h('span', { class: 'command-desc', title: c.description || '' }, c.description || ''),
        ]);
        row.addEventListener('click', () => applyCommand(c.name));
        commandList.appendChild(row);
      }
    }
    commandPaletteTitle.textContent =
      `${matches.length} of ${commands.length} commands and skills`;
    commandPalette.hidden = false;
    paletteIndex = 0;
    highlightPalette();
  }

  composerInput.addEventListener('input', refreshPalette);
  composerInput.addEventListener('keydown', (e) => {
    if (!paletteOpen()) return;
    const rows = paletteRows();
    if (e.key === 'Escape') {
      e.preventDefault();
      closePalette();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      paletteIndex = Math.min(paletteIndex + 1, rows.length - 1);
      highlightPalette();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      paletteIndex = Math.max(paletteIndex - 1, 0);
      highlightPalette();
    } else if (e.key === 'Tab' || e.key === 'Enter') {
      const active = rows[paletteIndex];
      if (!active) return;
      // Enter would otherwise submit a half-typed command name.
      e.preventDefault();
      active.click();
    }
  });
  composerInput.addEventListener('blur', () => setTimeout(closePalette, 150));
  composerInput.addEventListener('input', growComposer);

  // Enter sends; Shift+Enter breaks the line. A textarea does the opposite by
  // default, and a prompt long enough to need paragraphs is exactly the one
  // worth laying out before sending it. The palette has a prior claim on Enter
  // -- it completes the command being typed -- and its handler above runs
  // first, so this one stands aside for it. Only when the palette has
  // something to complete, though: open on "no matches" it takes no keys, and
  // Enter there means send the line as typed.
  composerInput.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    if (paletteOpen() && paletteRows().length) return;
    e.preventDefault();
    composerForm.requestSubmit();
  });

  // ---------------------------------------------------------------------
  // sidebar
  // ---------------------------------------------------------------------

  const STATUS_LABEL = {
    initializing: 'initializing',
    working: 'working',
    awaiting_approval: 'needs approval',
    awaiting_input: 'awaiting input',
    done: 'done',
    error: 'error',
  };

  function statusLabel(status) {
    return STATUS_LABEL[status] || status;
  }

  // The tail of a session's working directory, for the row's second line.
  // The tail rather than the whole path: the sidebar is narrow enough that a
  // full path is ellipsised, and the end is the half that says which checkout
  // this is. The row's tooltip carries the path in full.
  function shortWorkdir(path) {
    if (!path) return '';
    const parts = String(path).split('/').filter(Boolean);
    if (parts.length <= 2) return path;
    return `\u2026/${parts.slice(-2).join('/')}`;
  }

  // Renaming happens in the row itself: a dialog for one short string is more
  // ceremony than the change deserves. The name typed here sticks -- the
  // backend stops adopting the CLI's generated title for that session.
  function beginRename(id, nameEl) {
    const current = nameEl.textContent;
    const input = h('input', { class: 'session-rename-input', type: 'text', value: current });
    let settled = false;
    const finish = (commit) => {
      if (settled) return;
      settled = true;
      const next = input.value.trim();
      state.renamingId = null;
      // The field goes straight back to being a label rather than through a
      // redraw: redrawing replaces the row under a click that is still in
      // flight, which is how clicking a different session to end a rename lost
      // the click. The name itself changes when the server sends the list
      // back, so a rename the server refuses leaves the old name on screen.
      input.replaceWith(nameEl);
      if (commit && next && next !== current) {
        send({ t: 'rename', session_id: id, name: next });
      }
    };
    // The row underneath is a click target for selecting the session, and
    // every part of the edit is a click on the row.
    input.addEventListener('click', (e) => e.stopPropagation());
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') {
        e.preventDefault();
        finish(true);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        finish(false);
      }
    });
    input.addEventListener('blur', () => finish(true));
    state.renamingId = id;
    nameEl.replaceWith(input);
    input.focus();
    input.select();
  }

  // What a session is doing, for the confirmation that closing it will stop
  // that. A status missing from here has already finished, and closing it
  // interrupts nothing, so those close on the click.
  const CLOSE_WARNING = {
    initializing: 'It is still starting up.',
    working: 'Claude is working on a turn.',
    awaiting_approval: 'It is waiting on a tool approval.',
    awaiting_input: 'It is connected, waiting for a message.',
  };

  function closeSession(id) {
    // Remembered so the pane can say who ended the session. Without it, a
    // session that vanishes is reported as a restart, which is the only other
    // way that happens.
    state.closedByMe.add(id);
    state.confirmCloseId = null;
    send({ t: 'close', session_id: id });
  }

  // The confirmation lives in the row rather than in a dialog: the sidebar is
  // where the decision is made, and the row is the only place that can say
  // what this particular session is in the middle of.
  function buildCloseConfirm(s) {
    const strip = h('div', { class: 'session-close-confirm' });
    // The row underneath selects the session on click, which is not what
    // answering a question about it should do.
    strip.addEventListener('click', (e) => e.stopPropagation());
    strip.appendChild(h('div', { class: 'session-close-warning' },
      `${CLOSE_WARNING[s.status]} Close it? The conversation stays on disk.`));
    const yes = h('button', { class: 'btn-close-confirm', type: 'button' }, 'Close session');
    yes.addEventListener('click', () => {
      closeSession(s.id);
      renderSidebar();
    });
    const no = h('button', { class: 'btn-close-cancel', type: 'button' }, 'Cancel');
    no.addEventListener('click', () => {
      state.confirmCloseId = null;
      renderSidebar();
    });
    strip.appendChild(h('div', { class: 'session-close-actions' }, [yes, no]));
    return strip;
  }

  function renderSidebar() {
    // A rename is a half-typed name that lives only in the DOM, and the list
    // redraws on every status and cost change -- several a turn. Leaving the
    // sidebar as it is for those few seconds costs a stale status dot; not
    // leaving it costs the name being typed.
    if (state.renamingId) return;
    sessionListEl.innerHTML = '';
    if (state.order.length === 0) {
      sessionListEl.appendChild(h('div', { class: 'empty-note' }, 'No sessions yet.'));
      return;
    }
    for (const id of state.order) {
      const s = state.sessionsMeta.get(id);
      if (!s) continue;
      const row = h('div', { class: 'session-row' + (id === state.currentId ? ' active' : '') });
      const nameEl = h('span', { class: 'session-name' }, s.name || s.id);
      const renameBtn = h('button', {
        class: 'session-rename',
        type: 'button',
        title: 'Rename this session',
        'aria-label': `Rename ${s.name || s.id}`,
      }, '\u270e');
      renameBtn.addEventListener('click', (e) => {
        // Without this the click also lands on the row and selects the
        // session, which scrolls a different transcript into view under the
        // field just opened.
        e.stopPropagation();
        beginRename(id, nameEl);
      });
      const closeBtn = h('button', {
        class: 'session-close',
        type: 'button',
        title: 'Close this session',
        'aria-label': `Close ${s.name || s.id}`,
      }, '\u00d7');
      closeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (CLOSE_WARNING[s.status]) {
          state.confirmCloseId = id;
          renderSidebar();
        } else {
          // Nothing left running to interrupt.
          closeSession(id);
          renderSidebar();
        }
      });
      row.appendChild(
        h('div', { class: 'session-row-top' }, [
          h('span', { class: `dot dot-${s.status}` }),
          nameEl,
          renameBtn,
          closeBtn,
        ])
      );
      row.appendChild(h('div', { class: 'session-where', title: s.workdir || '' },
                        shortWorkdir(s.workdir)));

      const totalTokens = sessionTotalTokens(s.tokens);
      if (s.kind === 'sdk' && (typeof s.cost_usd === 'number' || totalTokens > 0)) {
        const bits = [];
        if (typeof s.cost_usd === 'number') bits.push(formatCost(s.cost_usd));
        if (totalTokens > 0) bits.push(`${formatTokenCount(totalTokens)} tok`);
        if (s.totals_cover_this_run_only) bits.push('this run only');
        row.appendChild(h('div', { class: 'session-totals-compact' }, bits.join(' · ')));
      }

      if (state.confirmCloseId === id) row.appendChild(buildCloseConfirm(s));

      row.addEventListener('click', () => {
        // Opening a session is also how you walk away from the question this
        // row was asking.
        state.confirmCloseId = null;
        selectSession(id);
      });
      sessionListEl.appendChild(row);
    }
  }

  function renderPaneHeader() {
    const meta = state.currentId ? state.sessionsMeta.get(state.currentId) : null;
    paneTitle.textContent = meta ? meta.name || meta.id : 'Select a session';
    paneStatus.innerHTML = '';
    if (meta) {
      paneStatus.appendChild(h('span', { class: `dot dot-${meta.status}` }));
      paneStatus.appendChild(document.createTextNode(statusLabel(meta.status)));
    }
  }

  // A message sent mid-turn is not dropped: SdkSession queues it and the CLI
  // queues it again on its own side, so it lands as soon as the turn ends.
  // Disabling the box while Claude worked meant there was no way to steer a
  // running turn, which is what ISSUE-028 hit trying to type a /btw. Only a
  // session that isn't there at all closes the composer now.
  function updateComposerState() {
    const meta = state.currentId ? state.sessionsMeta.get(state.currentId) : null;
    // `send` and `interrupt` are looked up among SDK sessions only, so both are
    // a no-op for a terminal session, and the browser shows no terminal output
    // to type against in the first place. The composer joins the mode control
    // and the totals row in gating on the kind, rather than offering a box that
    // silently swallows what is typed into it (ISSUE-038).
    // `error` is now only set when the turn loop has stopped for good, so the
    // session cannot take a message and the composer should not pretend it can
    // (ISSUE-036). A failure rendering one block reports itself and leaves the
    // session running, and does not reach here.
    const stopped = !!meta && meta.status === 'error';
    const writable = !!meta && meta.kind === 'sdk' && !stopped;
    const busy = writable && (meta.status === 'working' || meta.status === 'awaiting_approval');
    composerInput.disabled = !writable;
    composerSend.disabled = !writable;
    // The idle placeholder is the only thing that says the box does anything
    // besides send a message: typing "/" is a CLI convention, and a GUI that
    // does not mention it has the feature without the affordance (ISSUE-016).
    // A session mid-turn has a more urgent thing to say in this space, and one
    // with nothing selected has nothing to list.
    composerInput.placeholder = !meta
      ? 'Select a session to send a message…'
      : stopped
        ? 'This session stopped — resume it from History to carry on'
        : !writable
          ? 'This is a terminal session — drive it from the desktop app'
          : busy
            ? 'Message… (queued until this turn ends)'
            : 'Message, or / for commands and skills…';
    // Only while there is something to stop. An always-present Stop that does
    // nothing most of the time is worse than no Stop (ISSUE-033).
    if (composerStop) composerStop.hidden = !busy || !!historyId;
  }

  // ---------------------------------------------------------------------
  // permission mode control (ISSUE-012)
  // ---------------------------------------------------------------------

  const PERMISSION_MODE_LABELS = {
    default: 'Default (ask each time)',
    acceptEdits: 'Accept edits',
    plan: 'Plan',
    bypassPermissions: 'Bypass permissions',
    dontAsk: "Don't ask",
    auto: 'Auto',
  };

  for (const mode of Object.keys(PERMISSION_MODE_LABELS)) {
    permissionModeSelect.appendChild(h('option', { value: mode }, PERMISSION_MODE_LABELS[mode]));
  }

  // Only `sdk` sessions carry a switchable mode; the control stays hidden for
  // anything else, and for no session selected at all.
  function renderPermissionModeControl() {
    const meta = state.currentId ? state.sessionsMeta.get(state.currentId) : null;
    if (!meta || meta.kind !== 'sdk' || !meta.permission_mode) {
      permissionModeControl.hidden = true;
      updateSessionFooter();
      return;
    }
    permissionModeControl.hidden = false;
    permissionModeError.hidden = true;
    updateSessionFooter();
    if (!pendingPermissionMode || pendingPermissionMode.sessionId !== meta.id) {
      permissionModeSelect.value = meta.permission_mode;
    }
  }

  permissionModeSelect.addEventListener('change', () => {
    const meta = state.currentId ? state.sessionsMeta.get(state.currentId) : null;
    if (!meta) return;
    const previousMode = meta.permission_mode;
    const nextMode = permissionModeSelect.value;

    if (nextMode === 'bypassPermissions') {
      const ok = window.confirm(
        'Switch to bypassPermissions? Claude will run tools in this session without asking for approval, starting with its next tool call.'
      );
      if (!ok) {
        permissionModeSelect.value = previousMode;
        return;
      }
    }

    permissionModeError.hidden = true;
    pendingPermissionMode = { sessionId: meta.id, previousMode };
    send({ t: 'set_permission_mode', session_id: meta.id, mode: nextMode });
  });

  // ---------------------------------------------------------------------
  // cost and token totals (ISSUE-011)
  // ---------------------------------------------------------------------

  function formatCost(usd) {
    return `$${usd.toFixed(4)}`;
  }

  // Token counts, rounded to what anyone actually reads off them. A running
  // total is a sense of scale, not an audit: nobody acts on the difference
  // between 204,317 and 204,000, and the digits were costing the footer width
  // the mode selector then had to share.
  //
  // Under a thousand, the number itself. From there to ten thousand, the
  // nearest 500, because that range is where the second digit still says
  // something. Then the nearest thousand, and past a million the nearest
  // hundred thousand. Each step is entered by promotion rather than by a
  // threshold of its own, so the seams are continuous: 9,750 and 10,000 both
  // read 10k, and 999,500 reads 1M rather than 1000k.
  //
  // Used by the footer and the sidebar alike. The exact figure is on the
  // footer value's title for anyone who wants it.
  function formatTokenCount(n) {
    const v = n || 0;
    if (v < 1000) return String(v);
    // 2k, not 2.0k; 1.5k keeps its half. Same for M below.
    const unit = (x, u) => `${Number.isInteger(x) ? x : x.toFixed(1)}${u}`;
    if (v < 10000) return unit(Math.round(v / 500) * 500 / 1000, 'k');
    const k = Math.round(v / 1000);
    if (k < 1000) return `${k}k`;
    return unit(Math.round(v / 100000) / 10, 'M');
  }

  function sessionTotalTokens(tokens) {
    const t = tokens || {};
    return (t.input || 0) + (t.output || 0) + (t.cache_read || 0) + (t.cache_creation || 0);
  }

  // The transcript already shows a per-turn usage line (buildUsageLine); this
  // is the running total for the whole session, in the footer between the
  // transcript and the composer, so it survives scrolling past old turns.
  // Both usage events are ordinary transcript events, so this is called from
  // the live path and from the snapshot replay alike.
  function noteUsageEvent(ev) {
    if (!ev) return;
    if (ev.type === 'rate_limit') {
      state.rateLimit = ev;
      renderPlanUsage();
    } else if (ev.type === 'plan_usage') {
      state.planUsage = ev;
      renderPlanUsage();
    }
  }

  // The footer holds two independent things -- the running totals and the mode
  // control -- and either can be absent: a session that has spent nothing has
  // no totals, and a terminal session has no mode. An empty bar with a border
  // on it is not worth the pixels, so the strip follows its contents.
  function updateSessionFooter() {
    sessionFooterEl.hidden = sessionTotalsEl.hidden
      && (!planUsageEl || planUsageEl.hidden)
      && permissionModeControl.hidden;
  }

  // Windows the CLI names, in the order a reader wants them: the one that
  // bites first, then the week, then the per-model weeks.
  const RATE_LIMIT_WINDOW_LABELS = {
    five_hour: 'Session',
    seven_day: 'Week',
    seven_day_opus: 'Week (Opus)',
    seven_day_sonnet: 'Week (Sonnet)',
    overage: 'Overage',
  };

  // How long ago a reading was taken. Relative rather than absolute because the
  // question being answered is "is this stale?", and "4m ago" answers it where
  // "10:39" makes the reader do the subtraction. Falls back to a date once a
  // reading is old enough that the exact gap has stopped mattering.
  // The three states the CLI reports, in words. `allowed_warning` is the one
  // that matters and the one the raw string reads worst as: it is the warning
  // shot before a window closes, and everything stops when it does.
  const RATE_LIMIT_STATUS_LABELS = {
    allowed: 'within limit',
    allowed_warning: 'nearly used up',
    rejected: 'limit reached',
  };

  function fmtAge(epochSeconds) {
    if (!epochSeconds) return '';
    const seconds = Math.max(0, Date.now() / 1000 - epochSeconds);
    if (seconds < 45) return 'just now';
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return new Date(epochSeconds * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function fmtResetTime(epochSeconds) {
    if (!epochSeconds) return '';
    const when = new Date(epochSeconds * 1000);
    const sameDay = when.toDateString() === new Date().toDateString();
    return sameDay
      ? when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
      : when.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  // Plan limits, from whichever of the two sources has something to say.
  //
  // The percentages come from a `/usage` the user ran themselves -- nothing
  // here asks, because asking costs a turn against the limit it reports. The
  // push event fills the gap in the meantime: it always names the window in
  // force and when it resets, and carries a percentage only sometimes.
  function renderPlanUsage() {
    if (!planUsageEl) return;
    const meta = state.currentId ? state.sessionsMeta.get(state.currentId) : null;
    planUsageEl.innerHTML = '';
    if (!displayPrefs.show_plan_usage || !meta || meta.kind !== 'sdk') {
      planUsageEl.hidden = true;
      updateSessionFooter();
      return;
    }

    const parts = [];
    let takenAt = 0;  // epoch seconds of whichever reading is on show
    const measured = (state.planUsage && state.planUsage.windows) || [];
    if (measured.length) takenAt = state.planUsage.captured_at || 0;
    for (const w of measured) {
      parts.push(h('div', { class: 'plan-window' }, [
        h('span', { class: 'plan-window-label' }, w.label),
        h('span', { class: 'plan-window-value' }, `${w.percent}%`),
        w.resets ? h('span', { class: 'plan-window-reset' }, `resets ${w.resets}`) : null,
      ]));
    }

    const limit = state.rateLimit;
    const warning = !!limit && limit.status && limit.status !== 'allowed';
    // The push reading is shown when there are no measured percentages -- and
    // also when it is a warning or a rejection, however fresh the percentages
    // are. A window about to close is the actionable thing on the row; a
    // reading from twenty minutes ago is not.
    if (limit && (!measured.length || warning)) {
      // The push event has no captured_at of its own; its transcript timestamp
      // is when the CLI sent it, which is the same thing.
      const sentAt = limit.ts ? Date.parse(limit.ts) / 1000 : 0;
      if (!measured.length) takenAt = sentAt;
      const label = RATE_LIMIT_WINDOW_LABELS[limit.rate_limit_type] || limit.rate_limit_type || 'Plan';
      // A percentage when the CLI sends one -- it tends to appear exactly when
      // it starts to matter -- and the status in words otherwise.
      const status = RATE_LIMIT_STATUS_LABELS[limit.status] || limit.status;
      const value = limit.utilization != null
        ? `${Math.round(limit.utilization * 100)}% · ${status}`
        : status;
      const reset = fmtResetTime(limit.resets_at);
      // What happens at the end of a rejected window is the question the user
      // will have, so the reset is stated as the thing to wait for.
      const resetLabel = limit.status === 'rejected' ? 'resumes' : 'resets';
      parts.push(h('div', { class: `plan-window${warning ? ' is-limit' : ''}` }, [
        h('span', { class: 'plan-window-label' }, measured.length ? `${label} limit` : label),
        h('span', { class: 'plan-window-value' }, value),
        reset ? h('span', { class: 'plan-window-reset' }, `${resetLabel} ${reset}`) : null,
      ]));
    }

    if (!parts.length) {
      planUsageEl.hidden = true;
      updateSessionFooter();
      return;
    }

    // A warning or a rejection outranks the numbers: it is the thing to act on.
    planUsageEl.classList.toggle('is-warning', warning);
    planUsageEl.classList.toggle('is-rejected', !!limit && limit.status === 'rejected');

    // Every one of these is a reading taken at a moment, not a live gauge: the
    // percentages come from the last `/usage` the user ran, and the fallback
    // from the last turn the CLI announced. Saying when it was taken is what
    // makes an hour-old number read as an hour old rather than as the truth.
    if (takenAt) {
      parts.push(h('div', { class: 'plan-window is-age' }, [
        h('span', { class: 'plan-window-label' }, 'Checked'),
        h('span', { class: 'plan-window-age' }, fmtAge(takenAt)),
        measured.length ? h('span', { class: 'plan-window-reset' }, 'run /usage to refresh') : null,
      ]));
    }

    const row = h('div', { class: 'plan-usage-row' }, parts);
    planUsageEl.appendChild(row);
    if (measured.length && state.planUsage.note) {
      // The CLI's own caveat: its figures cover this machine only.
      row.title = state.planUsage.note;
    }
    planUsageEl.hidden = false;
    updateSessionFooter();
  }

  // "just now" stops being true a minute after it is drawn, and a transcript
  // can sit open for hours without another event to trigger a re-render. Cheap
  // enough to run unconditionally: it returns immediately when the panel is
  // hidden, which is every case where nothing is showing an age.
  setInterval(() => {
    if (planUsageEl && !planUsageEl.hidden) renderPlanUsage();
  }, 60000);

  function renderSessionTotals() {
    const meta = state.currentId ? state.sessionsMeta.get(state.currentId) : null;
    sessionTotalsEl.innerHTML = '';
    if (!meta || meta.kind !== 'sdk') {
      sessionTotalsEl.hidden = true;
      updateSessionFooter();
      return;
    }
    const tokens = meta.tokens || {};
    const hasCost = typeof meta.cost_usd === 'number' && displayPrefs.show_cost;
    // What is left after the preferences have had their say: with cache counts
    // hidden, a session whose only traffic was cached has nothing to put in the
    // row, and an empty bordered strip is worse than no strip.
    const hasTokens = displayPrefs.show_cache_tokens
      ? sessionTotalTokens(tokens) > 0
      : (tokens.input || 0) + (tokens.output || 0) > 0;
    if (!hasCost && !hasTokens) {
      sessionTotalsEl.hidden = true;
      updateSessionFooter();
      return;
    }
    sessionTotalsEl.hidden = false;
    updateSessionFooter();

    function stat(label, value, exact) {
      return h('div', { class: 'total-stat' }, [
        h('span', { class: 'total-stat-label' }, label),
        h('span', exact == null
          ? { class: 'total-stat-value' }
          : { class: 'total-stat-value', title: `${exact.toLocaleString()} tokens` }, value),
      ]);
    }

    function tokenStat(label, count) {
      return stat(label, formatTokenCount(count), count || 0);
    }

    const row = h('div', { class: 'session-totals-row' });
    if (hasCost) row.appendChild(stat('Cost', formatCost(meta.cost_usd)));
    row.appendChild(tokenStat('Input', tokens.input));
    row.appendChild(tokenStat('Output', tokens.output));
    if (displayPrefs.show_cache_tokens) {
      row.appendChild(tokenStat('Cache create', tokens.cache_creation));
      // Cache reads run far larger than every other counter and would drown
      // them out at the front of the row, so it goes last.
      row.appendChild(tokenStat('Cache read', tokens.cache_read));
    }
    if (meta.totals_cover_this_run_only) {
      // Three words on the row rather than a sentence under it. The caveat
      // matters -- these numbers are not the whole conversation -- but it was
      // costing a second line of the footer every time, and the sidebar row for
      // the same session already says the same three words. The sentence it
      // used to be is the tooltip.
      row.appendChild(h('div', { class: 'total-stat is-caveat' }, [
        h('span', { class: 'total-stat-label' }, 'Covers'),
        h('span', {
          class: 'total-stat-value',
          title: 'These totals cover only this run, not the resumed conversation before it.',
        }, 'this run'),
      ]));
    }
    sessionTotalsEl.appendChild(row);
  }

  // ---------------------------------------------------------------------
  // transcript rendering
  // ---------------------------------------------------------------------

  // Approval buttons sitting below the fold were the other half of ISSUE-025:
  // with the transcript scrolled up, or with a long turn's worth of cards
  // between here and there, an approval could wait unnoticed. The bar appears
  // only while one is genuinely off screen, so a visible approval card is not
  // announced twice.
  // Only from two upward: one running subagent already says so on its own
  // card, and a bar repeating it would be noise on every Agent call.
  function updateAgentsRunning() {
    if (!agentsRunningEl) return;
    const running = renderState ? renderState.runningAgents.size : 0;
    agentsRunningEl.hidden = running < 2;
    if (running >= 2) agentsRunningEl.textContent = `${running} subagents running`;
  }

  if (agentsRunningEl) {
    agentsRunningEl.addEventListener('click', () => {
      if (!renderState || !renderState.runningAgents.size) return;
      // The topmost one still running, so repeated clicks walk down the
      // transcript in the order they were started rather than jumping about.
      let first = null;
      for (const el of renderState.runningAgents) {
        if (!el.isConnected) continue;
        if (!first || el.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING) {
          first = el;
        }
      }
      if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  }

  function updateApprovalJump() {
    if (!approvalJumpEl) return;
    const target = firstOffscreenApproval();
    approvalJumpEl.hidden = target == null;
  }

  function firstOffscreenApproval() {
    if (!renderState || renderState.pendingApprovals.size === 0) return null;
    const view = transcriptEl.getBoundingClientRect();
    for (const el of renderState.pendingApprovals.values()) {
      if (!el.isConnected) continue;
      const box = el.getBoundingClientRect();
      // Its buttons live at the bottom of the card, so a card whose foot is
      // past the viewport is one the user cannot act on.
      if (box.bottom > view.bottom || box.top < view.top) return el;
    }
    return null;
  }

  if (approvalJumpEl) {
    approvalJumpEl.addEventListener('click', () => {
      const target = firstOffscreenApproval();
      if (target) target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    transcriptEl.addEventListener('scroll', updateApprovalJump, { passive: true });
    window.addEventListener('resize', updateApprovalJump);
  }

  // Assistant text as it is written. Accumulated per content-block index into a
  // bubble of its own, then thrown away when the finished block arrives: the
  // durable event is the record, this is only a preview of it. A dropped or
  // late delta therefore costs nothing but the animation.
  function applyTextDelta(ev) {
    if (!renderState) return;
    const wasBottom = isAtBottom();
    const index = ev.index || 0;
    let el = renderState.streaming.get(index);
    if (!el) {
      const group = ensureBubble('assistant', { container: transcriptEl, bubbles: renderState });
      el = h('div', { class: 'text-block is-streaming' });
      group.appendChild(el);
      renderState.streaming.set(index, el);
    }
    el.textContent += ev.text;
    if (wasBottom) scrollToBottom();
  }

  function clearStreamingText() {
    if (!renderState || renderState.streaming.size === 0) return;
    for (const el of renderState.streaming.values()) {
      const group = el.parentElement;
      el.remove();
      // The bubble was created for the preview and may now be empty.
      if (group && group.classList.contains('bubble-group') && !group.firstChild) {
        if (renderState.lastBubbleEl === group) breakBubble();
        group.remove();
      }
    }
    renderState.streaming.clear();
  }

  function isAtBottom() {
    return transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 40;
  }

  // Whether the reader is riding the bottom, which is the state the pane is
  // written around: new output arrives without them chasing it, and somebody
  // who has scrolled up to re-read a tool result is left where they are.
  //
  // Held as a flag rather than measured when it is needed, because what breaks
  // the position is a layout change, and by the time one has happened
  // `isAtBottom` already reads false. A check made after the fact cannot tell
  // "they scrolled up" from "the box shrank under them" (ISSUE-053).
  let stickToBottom = true;

  function scrollToBottom() {
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
    stickToBottom = true;
  }

  transcriptEl.addEventListener('scroll', () => {
    stickToBottom = isAtBottom();
  }, { passive: true });

  // #transcript is `flex: 1` above the approval bar, the totals footer and the
  // composer, so anything down there that grows takes its height out of the
  // scroll viewport while the scroll position stays put. A transcript at the
  // bottom then loses exactly as many pixels off the end as the other pane
  // gained, and the tail of the last message sits under the composer until
  // something gives the height back.
  //
  // The composer is the one that bites, because it grows on every keystroke:
  // measured on an 820x700 pane, typing an eight-line prompt took it from 69px
  // to 237px and hid 168px of the answer, which came back only when the box was
  // cleared on send. That is why the missing text looked like it arrived with
  // the next prompt (ISSUE-053).
  //
  // One observer on the box itself, rather than a re-scroll after each of the
  // four things that currently resize it, so the fifth does not have to
  // remember.
  new ResizeObserver(() => {
    if (stickToBottom) scrollToBottom();
  }).observe(transcriptEl);

  function clearEmptyState() {
    const placeholder = transcriptEl.querySelector(':scope > .empty-state');
    if (placeholder) placeholder.remove();
  }

  function resetTranscript(message) {
    transcriptEl.innerHTML = '';
    stickToBottom = true;
    renderState = {
      toolCards: new Map(),
      approvalCards: new Map(),
      pendingApprovals: new Map(),
      // Streaming preview elements, keyed by content-block index (ISSUE-033).
      streaming: new Map(),
      // Agent cards whose subagent has not reported back yet, so several at
      // once can be counted rather than hunted for.
      runningAgents: new Set(),
      lastBubbleRole: null,
      lastBubbleEl: null,
    };
    updateApprovalJump();
    updateAgentsRunning();
    if (!state.currentId) {
      // The mark goes on the greeting only, not on a caller's message: the
      // other empty states here are errors and Loading, and a logo above
      // "That transcript could not be read." would be a strange thing to see.
      const cls = message ? 'empty-state' : 'empty-state is-welcome';
      transcriptEl.appendChild(
        h('div', { class: cls }, message || 'Select a session from the sidebar to view its transcript.')
      );
    }
  }

  function renderFullTranscript() {
    resetTranscript();
    if (state.currentEvents.size === 0) {
      transcriptEl.appendChild(h('div', { class: 'empty-state' }, 'No activity yet.'));
      return;
    }
    const sorted = Array.from(state.currentEvents.values()).sort((a, b) => a.seq - b.seq);
    for (const ev of sorted) appendEventToDom(ev);
    scrollToBottom();
    updateApprovalJump();
  }

  // Where an event renders. Blocks a subagent produced arrive tagged with the
  // id of the Agent call that spawned them, and belong inside that card rather
  // than beside it (ISSUE-017). Everything else goes to the transcript root.
  //
  // Bubble-grouping state travels with the destination: consecutive text from a
  // subagent groups within its own card instead of joining whatever the parent
  // rendered last.
  function targetFor(ev) {
    const parent = ev && ev.parent_tool_use_id;
    if (parent) {
      const card = renderState.toolCards.get(parent);
      if (card && card.nested) {
        card.nested.noteEvent();
        return { container: card.nested.body, bubbles: card.nested.state };
      }
      // The Agent card should always precede its subagent's blocks. If it
      // somehow does not, render at the root rather than dropping the event.
    }
    return { container: transcriptEl, bubbles: renderState };
  }

  function ensureBubble(role, target) {
    const { container, bubbles } = target;
    if (bubbles.lastBubbleRole === role && bubbles.lastBubbleEl) {
      return bubbles.lastBubbleEl;
    }
    const group = h('div', { class: `bubble-group role-${role}` });
    container.appendChild(group);
    bubbles.lastBubbleRole = role;
    bubbles.lastBubbleEl = group;
    return group;
  }

  function breakBubble(target) {
    const bubbles = (target || { bubbles: renderState }).bubbles;
    bubbles.lastBubbleRole = null;
    bubbles.lastBubbleEl = null;
  }

  // ---------------------------------------------------------------------
  // special tool renderers (ISSUE-013): ExitPlanMode, Edit/MultiEdit, Write.
  // Used from both buildToolCard and buildApprovalCard, since approving an
  // edit means reading the diff, not a wall of quoted JSON.
  // ---------------------------------------------------------------------

  // The inline syntax Claude actually writes, in the order it has to be
  // recognised: code spans first so nothing inside a backtick is reinterpreted,
  // then links, then bold before italic so `**x**` is not read as two italics.
  // `_underscore_` is deliberately absent: identifiers like `some_var_name`
  // appear in prose far more often than underscore italics do. Italic also
  // refuses to span whitespace at either end, so `2 * 3 * 4` stays arithmetic.
  const INLINE_RULES = [
    { re: /`([^`]+)`/g, make: (m) => h('code', { class: 'md-inline-code' }, m[1]) },
    {
      // The URL may itself contain one level of balanced parentheses, so a
      // Wikipedia link like .../Foo_(bar) keeps its closing paren instead of
      // ending the match at the first one and leaving a stray ) behind.
      re: /\[([^\]\n]+)\]\((https?:\/\/(?:[^\s()]|\([^\s()]*\))+)\)/g,
      make: (m) => h('a', {
        class: 'md-link', href: m[2], target: '_blank', rel: 'noopener noreferrer',
      }, m[1]),
    },
    { re: /\*\*([^*\n]+)\*\*/g, make: (m) => h('strong', {}, m[1]) },
    { re: /\*(\S|\S[^*\n]*\S)\*/g, make: (m) => h('em', {}, m[1]) },
  ];

  // Splits the string parts of `nodes` on one rule, leaving already-matched
  // elements alone. Running the rules in sequence this way is what keeps a
  // match from being re-examined by a later rule.
  function applyInlineRule(nodes, rule) {
    const out = [];
    for (const node of nodes) {
      if (typeof node !== 'string') {
        out.push(node);
        continue;
      }
      rule.re.lastIndex = 0;
      let last = 0;
      let m;
      while ((m = rule.re.exec(node)) !== null) {
        if (m.index > last) out.push(node.slice(last, m.index));
        out.push(rule.make(m));
        last = m.index + m[0].length;
      }
      if (last < node.length) out.push(node.slice(last));
    }
    return out;
  }

  // Anything that matches no rule passes through as plain text, so unsupported
  // syntax shows its literal characters instead of being stripped or
  // mis-rendered.
  function parseInlineMarkdown(text) {
    let nodes = [String(text == null ? '' : text)];
    for (const rule of INLINE_RULES) nodes = applyInlineRule(nodes, rule);
    return nodes.map((n) => (typeof n === 'string' ? document.createTextNode(n) : n));
  }

  // A pipe table's second line, which both confirms the block is a table and
  // sets each column's alignment (ISSUE-045). Requiring a pipe here is what keeps a line of
  // dashes under a paragraph from turning the paragraph into a header row.
  const TABLE_DELIM_RE = /^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-*:?\s*\|?\s*$/;

  function isTableDelimiter(line) {
    return line != null && line.indexOf('|') !== -1 && TABLE_DELIM_RE.test(line);
  }

  // Splits a row on its unescaped pipes, so a cell written as `a \| b` keeps the
  // pipe as text. The border pipes produce an empty cell at each end, which is
  // dropped only when the row actually had that border; a table written without
  // one keeps its first and last cells, empty or not.
  function splitTableRow(line) {
    const s = line.trim();
    const cells = [];
    let cur = '';
    for (let k = 0; k < s.length; k++) {
      if (s[k] === '\\' && s[k + 1] === '|') {
        cur += '|';
        k++;
      } else if (s[k] === '|') {
        cells.push(cur);
        cur = '';
      } else {
        cur += s[k];
      }
    }
    cells.push(cur);
    if (s.startsWith('|')) cells.shift();
    if (s.length > 1 && s.endsWith('|')) cells.pop();
    return cells.map((c) => c.trim());
  }

  function tableAlignClass(delimCell) {
    const cell = String(delimCell == null ? '' : delimCell);
    const left = cell.startsWith(':');
    const right = cell.endsWith(':');
    if (left && right) return 'md-align-center';
    if (right) return 'md-align-right';
    return null;
  }

  // A small markdown-lite renderer: headings, bullet and numbered lists, pipe
  // tables, fenced code blocks, and the inline formatting above. Anything that
  // doesn't match one of those block forms falls back to a paragraph with its
  // line breaks preserved. Built entirely with DOM nodes and textContent, never
  // innerHTML, since every string it renders comes off the wire.
  //
  // Used for three things now: a plan, an assistant message, and a thinking
  // block. They differ only in type size and colour, which is what the extra
  // root class is for.
  function renderMarkdown(text, rootClass) {
    const root = h('div', { class: rootClass ? `md-body ${rootClass}` : 'md-body' });
    const lines = String(text == null ? '' : text).split('\n');
    const total = lines.length;
    let i = 0;
    let paragraphLines = [];

    function flushParagraph() {
      if (!paragraphLines.length) return;
      const p = h('p', { class: 'md-paragraph' });
      paragraphLines.forEach((line, idx) => {
        if (idx > 0) p.appendChild(document.createTextNode('\n'));
        for (const node of parseInlineMarkdown(line)) p.appendChild(node);
      });
      root.appendChild(p);
      paragraphLines = [];
    }

    // Consumes a run of list items starting at the current line, advancing
    // `i` past them. A line that does not start a new item is treated as a
    // wrapped continuation of the previous item (plan text commonly wraps a
    // list item across lines) unless it looks like the start of something
    // else: blank, a fence, a heading, or the other list marker.
    function consumeList(itemMarker, tagName) {
      const list = h(tagName, { class: 'md-list' });
      let li = null;
      while (i < total) {
        const cur = lines[i];
        if (itemMarker.test(cur)) {
          li = h('li', {});
          for (const node of parseInlineMarkdown(cur.replace(itemMarker, ''))) li.appendChild(node);
          list.appendChild(li);
          i++;
          continue;
        }
        if (cur.trim() === '' || /^```/.test(cur) || /^#{1,6}\s+/.test(cur) ||
            /^[-*]\s+/.test(cur) || /^\d+\.\s+/.test(cur) || /^\s*\|/.test(cur)) {
          break;
        }
        li.appendChild(document.createTextNode(' '));
        for (const node of parseInlineMarkdown(cur.trim())) li.appendChild(node);
        i++;
      }
      return list;
    }

    while (i < total) {
      const line = lines[i];

      if (/^```/.test(line)) {
        flushParagraph();
        i++;
        const codeLines = [];
        while (i < total && !/^```/.test(lines[i])) {
          codeLines.push(lines[i]);
          i++;
        }
        if (i < total) i++; // consume the closing fence
        root.appendChild(h('pre', { class: 'code-block md-code-block' }, codeLines.join('\n')));
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        flushParagraph();
        const level = Math.min(heading[1].length, 6);
        const headingEl = h(`h${level}`, { class: 'md-heading' });
        for (const node of parseInlineMarkdown(heading[2])) headingEl.appendChild(node);
        root.appendChild(headingEl);
        i++;
        continue;
      }

      if (/^\s*\|/.test(line) && i + 1 < total && isTableDelimiter(lines[i + 1])) {
        flushParagraph();
        const headerCells = splitTableRow(line);
        const aligns = splitTableRow(lines[i + 1]).map(tableAlignClass);
        i += 2;
        const headRow = h('tr', {});
        headerCells.forEach((cell, col) => {
          const th = h('th', { class: aligns[col] });
          for (const node of parseInlineMarkdown(cell)) th.appendChild(node);
          headRow.appendChild(th);
        });
        const body = h('tbody', {});
        while (i < total && /^\s*\|/.test(lines[i])) {
          const cells = splitTableRow(lines[i]);
          const row = h('tr', {});
          // The header decides the column count. A short row is padded and a
          // long one truncated, so a ragged table still lines up.
          for (let col = 0; col < headerCells.length; col++) {
            const td = h('td', { class: aligns[col] });
            for (const node of parseInlineMarkdown(cells[col] || '')) td.appendChild(node);
            row.appendChild(td);
          }
          body.appendChild(row);
          i++;
        }
        const table = h('table', { class: 'md-table' }, [h('thead', {}, headRow), body]);
        // A wide table scrolls inside the message rather than stretching it.
        root.appendChild(h('div', { class: 'md-table-wrap' }, table));
        continue;
      }

      if (/^[-*]\s+/.test(line)) {
        flushParagraph();
        root.appendChild(consumeList(/^[-*]\s+/, 'ul'));
        continue;
      }

      if (/^\d+\.\s+/.test(line)) {
        flushParagraph();
        root.appendChild(consumeList(/^\d+\.\s+/, 'ol'));
        continue;
      }

      if (line.trim() === '') {
        flushParagraph();
        i++;
        continue;
      }

      paragraphLines.push(line);
      i++;
    }
    flushParagraph();
    return root;
  }

  // A plain LCS line diff. old_string/new_string pairs from Edit/MultiEdit
  // are small enough that an O(n*m) table costs nothing in practice; the
  // guard below only protects against a pathological input.
  function diffLines(oldText, newText) {
    const a = String(oldText == null ? '' : oldText).split('\n');
    const b = String(newText == null ? '' : newText).split('\n');
    const n = a.length;
    const m = b.length;

    if (n * m > 250000) {
      const ops = [];
      for (const line of a) ops.push({ type: 'remove', text: line });
      for (const line of b) ops.push({ type: 'add', text: line });
      return ops;
    }

    const dp = new Array(n + 1);
    for (let i = 0; i <= n; i++) dp[i] = new Int32Array(m + 1);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }

    const ops = [];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) {
        ops.push({ type: 'context', text: a[i] });
        i++;
        j++;
      } else if (dp[i + 1][j] >= dp[i][j + 1]) {
        ops.push({ type: 'remove', text: a[i] });
        i++;
      } else {
        ops.push({ type: 'add', text: b[j] });
        j++;
      }
    }
    while (i < n) {
      ops.push({ type: 'remove', text: a[i] });
      i++;
    }
    while (j < m) {
      ops.push({ type: 'add', text: b[j] });
      j++;
    }
    return ops;
  }

  function buildDiffLines(oldText, newText) {
    const ops = diffLines(oldText, newText);
    const container = h('div', { class: 'diff-lines' });
    for (const op of ops) {
      const marker = op.type === 'add' ? '+' : op.type === 'remove' ? '-' : ' ';
      const cls = op.type === 'add' ? 'diff-line-add' : op.type === 'remove' ? 'diff-line-remove' : 'diff-line-context';
      container.appendChild(h('div', { class: `diff-line ${cls}`, 'data-marker': marker }, op.text));
    }
    return { el: container, lineCount: ops.length };
  }

  // Renders Edit's old_string/new_string as a diff under the file path. Also
  // covers MultiEdit-shaped input (an `edits` array of {old_string,
  // new_string}), rendering each edit as its own hunk under the one path.
  function renderEditDiff(input) {
    const container = h('div', { class: 'edit-diff' });
    container.appendChild(h('div', { class: 'diff-file-heading' }, (input && input.file_path) || '(unknown file)'));
    let lineCount = 0;

    if (input && Array.isArray(input.edits)) {
      input.edits.forEach((edit, idx) => {
        const hunk = h('div', { class: 'diff-hunk' });
        hunk.appendChild(h('div', { class: 'diff-hunk-label' }, `Edit ${idx + 1} of ${input.edits.length}`));
        const diff = buildDiffLines((edit && edit.old_string) || '', (edit && edit.new_string) || '');
        lineCount += diff.lineCount;
        hunk.appendChild(diff.el);
        container.appendChild(hunk);
      });
    } else {
      const diff = buildDiffLines((input && input.old_string) || '', (input && input.new_string) || '');
      lineCount += diff.lineCount;
      container.appendChild(diff.el);
      if (input && input.replace_all) {
        container.appendChild(h('div', { class: 'diff-note' }, 'Replaces all occurrences.'));
      }
    }

    return { el: container, lineCount };
  }

  function renderWriteContent(input) {
    const container = h('div', { class: 'write-content' });
    container.appendChild(h('div', { class: 'diff-file-heading' }, (input && input.file_path) || '(unknown file)'));
    container.appendChild(h('div', { class: 'write-label' }, 'New file content'));
    const content = (input && input.content) || '';
    container.appendChild(h('pre', { class: 'code-block' }, content));
    return { el: container, lineCount: content.split('\n').length };
  }

  // Dispatches on tool name and input shape to one of the renderers above.
  // Returns null for anything else, so the caller falls back to the plain
  // JSON view that already handles every other tool.
  // The one line that says what a tool call is actually doing. A transcript is
  // mostly tool calls -- one real session ran 93 of them against 8 paragraphs
  // of prose -- so a card headed by the bare word "Bash" says nothing about
  // what happened, and the argument JSON underneath says too much (ISSUE-026).
  // Falls back to null for a tool with no obvious subject, leaving the head as
  // it was.
  function toolSummary(name, input) {
    if (!input || typeof input !== 'object') return null;

    function firstLine(value) {
      if (typeof value !== 'string') return null;
      const line = value.split('\n').find((l) => l.trim() !== '');
      return line ? line.trim() : null;
    }

    switch (name) {
      case 'Bash':
      case 'BashOutput':
        return firstLine(input.command) || firstLine(input.description);
      case 'Read':
      case 'Write':
      case 'NotebookEdit':
        return shortenPath(input.file_path || input.notebook_path);
      case 'Edit':
      case 'MultiEdit':
        return shortenPath(input.file_path);
      case 'Glob':
        return [input.pattern, input.path && `in ${shortenPath(input.path)}`].filter(Boolean).join(' ');
      case 'Grep':
        return [input.pattern, input.path && `in ${shortenPath(input.path)}`].filter(Boolean).join(' ');
      case 'Skill':
        return [input.skill, input.args].filter(Boolean).join(' ');
      case 'Agent':
      case 'Task':
        return input.description || input.subagent_type || null;
      case 'WebFetch':
        return input.url || null;
      case 'WebSearch':
        return input.query || null;
      case 'TodoWrite':
        return Array.isArray(input.todos) ? `${input.todos.length} items` : null;
      case 'ExitPlanMode':
        return 'plan ready for review';
      default:
        break;
    }
    // An unrecognised tool still usually has one short string argument worth
    // showing; a long one is body material, not a heading.
    for (const key of ['description', 'path', 'file_path', 'query', 'prompt', 'command']) {
      const line = firstLine(input[key]);
      if (line && line.length <= 120) return line;
    }
    return null;
  }

  // Absolute paths eat the whole line and the leading directories are the same
  // for every file in a project, so keep the tail.
  function shortenPath(path) {
    if (typeof path !== 'string' || path === '') return null;
    const parts = path.split('/').filter(Boolean);
    if (parts.length <= 2) return path;
    return parts.slice(-2).join('/');
  }

  function renderSpecialToolInput(name, input) {
    if (!input || typeof input !== 'object') return null;

    if (name === 'ExitPlanMode' && typeof input.plan === 'string') {
      return {
        label: 'Plan',
        el: renderMarkdown(input.plan, 'is-plan'),
        lineCount: input.plan.split('\n').length,
        collapseLabels: ['Show plan', 'Hide plan'],
      };
    }

    if ((name === 'Edit' || name === 'MultiEdit') && (typeof input.old_string === 'string' || Array.isArray(input.edits))) {
      const diff = renderEditDiff(input);
      return { label: 'Diff', el: diff.el, lineCount: diff.lineCount, collapseLabels: ['Show diff', 'Hide diff'] };
    }

    // A folded Bash card opens onto the command itself. Quoting it back as
    // `{"command": "..."}` behind a second toggle, inside a card that is
    // already a toggle, is two clicks to read one line of shell.
    if (name === 'Bash' && typeof input.command === 'string') {
      const el = h('div', {});
      el.appendChild(h('pre', { class: 'code-block' }, input.command));
      const notes = [];
      if (typeof input.description === 'string' && input.description) notes.push(input.description);
      if (input.run_in_background) notes.push('runs in the background');
      if (input.timeout) notes.push(`timeout ${input.timeout} ms`);
      if (notes.length) el.appendChild(h('div', { class: 'diff-note' }, notes.join('  ·  ')));
      return {
        label: 'Command',
        el,
        lineCount: countLines(input.command),
        collapseLabels: ['Show command', 'Hide command'],
      };
    }

    if (name === 'Write' && typeof input.content === 'string') {
      const write = renderWriteContent(input);
      return { label: 'File', el: write.el, lineCount: write.lineCount, collapseLabels: ['Show file', 'Hide file'] };
    }

    return null;
  }

  // Matches the existing "Show result" pattern: content past the threshold
  // starts hidden behind a toggle instead of pushing the rest of the
  // transcript off screen. Content under the threshold, the common case,
  // renders as-is with no toggle.
  function buildCollapsibleSection(bodyEl, lineCount, collapseLabels, threshold) {
    if (lineCount <= (threshold == null ? 30 : threshold)) return bodyEl;
    const [showLabel, hideLabel] = collapseLabels || ['Show more', 'Hide'];
    const wrapper = h('div', {});
    bodyEl.style.display = 'none';
    const toggle = h('button', { class: 'tool-result-toggle', type: 'button' }, showLabel);
    toggle.addEventListener('click', () => {
      const showing = bodyEl.style.display !== 'none';
      bodyEl.style.display = showing ? 'none' : 'block';
      toggle.textContent = showing ? showLabel : hideLabel;
    });
    wrapper.appendChild(toggle);
    wrapper.appendChild(bodyEl);
    return wrapper;
  }

  function renderInto(ev, target) {
    appendEventToDom(ev, target);
  }

  function appendEventToDom(ev, overrideTarget) {
    // Resolved once: a subagent's blocks render inside their Agent card, and
    // everything else at the transcript root. Approvals are the deliberate
    // exception below. An override is passed when the event was fetched rather
    // than streamed, since a fetched subagent event carries no
    // parent_tool_use_id of its own.
    const target = overrideTarget || targetFor(ev);
    const at = target.container;

    switch (ev.type) {
      case 'text': {
        // Text the CLI injected into the turn -- a skill body, a hook's output,
        // a system reminder -- is context, not conversation. Rendered as a
        // bubble it dropped a whole skill document into the middle of the
        // transcript (ISSUE-026), so it gets a folded card labelled with its
        // first line instead. Events from before this field existed have no
        // `source` and stay bubbles.
        if (ev.role === 'user' && (ev.source === 'injected' || ev.source === 'briefing')) {
          breakBubble(target);
          at.appendChild(buildContextBlock(
            ev.text, ev.source === 'briefing' ? 'Briefing' : 'Context added'));
          break;
        }
        const group = ensureBubble(ev.role, target);
        // The assistant writes markdown -- bold, bullets, `code`, headings --
        // and showing it with the asterisks still in is showing the source, not
        // the message. A user turn stays verbatim: it is what the person typed,
        // not something formatted for reading back.
        if (ev.role === 'assistant') {
          const block = h('div', { class: 'text-block is-prose' });
          block.appendChild(renderMarkdown(ev.text, 'is-message'));
          group.appendChild(block);
        } else {
          group.appendChild(h('div', { class: 'text-block' }, ev.text));
        }
        break;
      }

      case 'permission_note': {
        breakBubble(target);
        at.appendChild(buildPermissionNote(ev));
        break;
      }

      case 'thinking': {
        // NOTE: the spec's `thinking` event has no `role` field (unlike
        // `text`). Only Claude emits extended-thinking blocks, so it is
        // grouped into the assistant bubble stream.
        const group = ensureBubble('assistant', target);
        // Collapsed by default, as docs/transcript-protocol.md specifies: a
        // long block of reasoning between two tool calls pushes the turn
        // itself off the screen. The summary line opens it when you want it.
        const details = h('details', { class: 'thinking-block' });
        details.appendChild(h('summary', { class: 'thinking-summary' }, 'Thinking'));
        const body = h('div', { class: 'thinking-body' });
        body.appendChild(renderMarkdown(ev.text, 'is-thinking'));
        details.appendChild(body);
        group.appendChild(details);
        break;
      }

      case 'tool_use': {
        breakBubble(target);
        const card = buildToolCard(ev);
        // Keyed globally, not per destination, so a tool_result pairs with its
        // call wherever either of them rendered.
        renderState.toolCards.set(ev.tool_use_id, card);
        at.appendChild(card.el);
        break;
      }

      case 'tool_result': {
        breakBubble(target);
        const card = renderState.toolCards.get(ev.tool_use_id);
        if (card) {
          card.setResult(ev);
          // Replayed history: the subagent's own transcript is in a separate
          // file, so offer to fetch it rather than seeding it.
          if (ev.agent_id && card.nested) card.nested.offerLoad(ev);
        } else {
          at.appendChild(buildUnknownEvent(ev, 'tool_result with no matching tool_use'));
        }
        break;
      }

      case 'approval_request': {
        // Deliberately at the root even when a subagent raised it. An approval
        // is the one card the user has to act on, and one folded inside a
        // collapsed subagent is one nobody answers.
        breakBubble();
        const card = buildApprovalCard(ev);
        renderState.approvalCards.set(ev.request_id, card);
        renderState.pendingApprovals.set(ev.request_id, card.el);
        transcriptEl.appendChild(card.el);
        updateApprovalJump();
        break;
      }

      case 'approval_resolved': {
        breakBubble();
        renderState.pendingApprovals.delete(ev.request_id);
        const card = renderState.approvalCards.get(ev.request_id);
        if (card) {
          card.resolve(ev);
        } else {
          transcriptEl.appendChild(buildResolvedLine(ev));
        }
        updateApprovalJump();
        break;
      }

      case 'question_request': {
        // At the root for the same reason an approval is: a question folded
        // inside a collapsed subagent is one nobody answers.
        breakBubble();
        const qcard = buildQuestionCard(ev);
        renderState.approvalCards.set(ev.request_id, qcard);
        renderState.pendingApprovals.set(ev.request_id, qcard.el);
        transcriptEl.appendChild(qcard.el);
        updateApprovalJump();
        break;
      }

      case 'question_resolved': {
        breakBubble();
        renderState.pendingApprovals.delete(ev.request_id);
        const qc = renderState.approvalCards.get(ev.request_id);
        if (qc) qc.resolve(ev);
        else transcriptEl.appendChild(buildQuestionResolvedLine(ev));
        updateApprovalJump();
        break;
      }

      case 'approval_expired': {
        // The card is marked in place rather than replaced by a line, which is
        // what a resolution does. Nobody decided anything here, and a user
        // part-way through answering should still be able to see what they
        // had chosen (ISSUE-052).
        breakBubble();
        renderState.pendingApprovals.delete(ev.request_id);
        const dead = renderState.approvalCards.get(ev.request_id);
        if (dead && dead.expired) dead.expired(expiredText(ev));
        else transcriptEl.appendChild(buildExpiredLine(ev));
        updateApprovalJump();
        break;
      }

      case 'usage': {
        breakBubble(target);
        at.appendChild(buildUsageLine(ev));
        break;
      }

      case 'rate_limit': {
        if (ev.status && ev.status !== 'allowed') {
          breakBubble(target);
          at.appendChild(buildRateLimitBanner(ev));
        }
        break;
      }

      case 'system': {
        const line = buildSystemLine(ev);
        // Some system subtypes carry nothing worth showing, in which case the
        // builder returns null and the bubble is left alone rather than broken
        // apart for an empty line.
        if (line) {
          breakBubble(target);
          at.appendChild(line);
        }
        break;
      }

      case 'plan_usage': {
        // Footer only. The reading was taken from a `/usage` whose output is
        // already in the transcript a few lines up; drawing it again as a card
        // would be the same numbers twice.
        break;
      }

      case 'status': {
        // Drives the sidebar dot and pane header only; no timeline entry,
        // per the spec's rendering notes.
        break;
      }

      default: {
        breakBubble(target);
        at.appendChild(buildUnknownEvent(ev));
      }
    }
  }

  // A subagent's transcript, folded inside the Agent card that spawned it.
  //
  // Collapsed by default and counted rather than shown: a subagent can produce
  // more events than the parent it runs under (measured at 7.2 MiB of subagent
  // transcript against a 9.4 MiB parent), and expanding every one by default
  // would bury the conversation it belongs to.
  //
  // It keeps its own bubble-grouping state, so consecutive text from the
  // subagent groups within the card instead of joining whatever the parent last
  // rendered.
  function state_currentId() {
    return state.currentId;
  }

  function buildNestedTranscript(ev) {
    const count = h('span', { class: 'subagent-count' }, 'no activity yet');
    const label = h('span', { class: 'subagent-label' }, 'Subagent');
    const toggle = h('button', { class: 'subagent-toggle', type: 'button', 'aria-expanded': 'false' },
                     [label, count]);
    const body = h('div', { class: 'subagent-body' });
    body.hidden = true;
    toggle.addEventListener('click', () => {
      body.hidden = !body.hidden;
      toggle.setAttribute('aria-expanded', String(!body.hidden));
      updateApprovalJump();
    });

    const state = { events: 0, lastBubbleRole: null, lastBubbleEl: null,
                    sessionId: ev.session_id || state_currentId() };

    // A replayed session has no subagent events in its history: they live in
    // their own file and are fetched when asked for. A live session streams
    // them instead, so this button never appears there.
    let loader = null;
    function offerLoad(resultEv) {
      if (loader || state.events > 0) return;
      const desc = [resultEv.agent_description, resultEv.agent_model]
        .filter(Boolean).join(' · ');
      count.textContent = desc || 'transcript available';
      loader = h('button', { class: 'subagent-load', type: 'button' }, 'Load transcript');
      loader.addEventListener('click', async () => {
        loader.disabled = true;
        loader.textContent = 'Loading…';
        const events = await fetchSubagent(state.sessionId, resultEv.agent_id);
        loader.remove();
        loader = null;
        if (!events) {
          body.appendChild(h('div', { class: 'subagent-note' },
                             'Could not read this subagent\'s transcript.'));
          return;
        }
        if (events.length === 0) {
          body.appendChild(h('div', { class: 'subagent-note' },
                             'This subagent recorded nothing.'));
          return;
        }
        for (const child of events) renderInto(child, { container: body, bubbles: state });
        count.textContent = events.length === 1 ? '1 event' : `${events.length} events`;
        updateApprovalJump();
      });
      body.appendChild(loader);
      if (body.hidden) {
        // Expand on the user's behalf the first time there is something to
        // offer, so the button is not hidden behind a toggle that says nothing
        // is there.
        body.hidden = false;
        toggle.setAttribute('aria-expanded', 'true');
      }
    }

    return {
      el: h('div', { class: 'subagent' }, [toggle, body]),
      body,
      state,
      offerLoad,
      noteEvent() {
        state.events += 1;
        count.textContent = state.events === 1 ? '1 event' : `${state.events} events`;
      },
    };
  }

  async function fetchSubagent(sessionId, agentId) {
    try {
      const res = await apiFetch(
        `/api/sessions/${encodeURIComponent(sessionId)}/subagents/${encodeURIComponent(agentId)}`
      );
      if (!res.ok) return null;
      const body = await res.json();
      return Array.isArray(body.events) ? body.events : [];
    } catch {
      return null;
    }
  }

  function buildToolCard(ev) {
    // A shell call is the noisiest thing in a transcript and the least often
    // read: the head already says the command and the badge says how it went,
    // so the card folds down to that one line and opens on click. A failure
    // opens itself in setResult, since that is the one you came to read.
    const foldsToHead = ev.name === 'Bash' || ev.name === 'BashOutput';
    const stateBadge = h('span', { class: 'tool-card-state state-pending' }, 'running…');
    const head = h(foldsToHead ? 'summary' : 'div', { class: 'tool-card-head' },
                   [h('span', { class: 'tool-card-name' }, ev.name)]);
    const summary = toolSummary(ev.name, ev.input);
    if (summary) head.appendChild(h('span', { class: 'tool-card-summary', title: summary }, summary));
    head.appendChild(stateBadge);
    const el = h(foldsToHead ? 'details' : 'div',
                 { class: foldsToHead ? 'tool-card is-folding' : 'tool-card' }, head);

    // An Agent call gets somewhere to put the subagent's work. The SDK forwards
    // that subagent's blocks tagged with this call's id, and without a home
    // they render at the top level as though the main agent had done them
    // (ISSUE-017).
    // A skill load is where the model changes approach mid-turn, so it is
    // marked rather than left to look like any other tool call (ISSUE-016).
    if (ev.name === 'Skill') el.classList.add('is-skill');

    let nested = null;
    let isAgent = false;
    if (ev.name === 'Agent' || ev.name === 'Task') {
      el.classList.add('is-agent');
      isAgent = true;
      if (renderState) renderState.runningAgents.add(el);
      updateAgentsRunning();
      nested = buildNestedTranscript(ev);
      el.appendChild(nested.el);
    }

    const special = renderSpecialToolInput(ev.name, ev.input);
    const inputSection = h('div', { class: 'tool-card-section' });
    if (special) {
      inputSection.appendChild(h('div', { class: 'tool-card-section-label' }, special.label));
      inputSection.appendChild(buildCollapsibleSection(special.el, special.lineCount, special.collapseLabels));
    } else {
      // The head now carries the subject of the call, so the argument JSON is
      // reference rather than the headline. Where the head already says it, the
      // JSON is folded whatever its size -- printing `{"command": "git status"}`
      // under a card headed `Bash git status` is the duplication ISSUE-026 asked
      // to be rid of. Without a summary it is the only description there is, so
      // it stays open until it gets long.
      const json = prettyJson(ev.input);
      inputSection.appendChild(h('div', { class: 'tool-card-section-label' }, 'Input'));
      inputSection.appendChild(
        buildCollapsibleSection(
          h('pre', { class: 'code-block' }, json),
          countLines(json),
          ['Show input', 'Hide input'],
          summary ? 0 : 8
        )
      );
    }
    el.appendChild(inputSection);

    return {
      el,
      nested,
      setResult(resultEv) {
        if (isAgent && renderState) {
          renderState.runningAgents.delete(el);
          updateAgentsRunning();
        }
        stateBadge.classList.remove('state-pending');
        stateBadge.classList.add(resultEv.is_error ? 'state-error' : 'state-ok');
        stateBadge.textContent = resultEv.is_error ? 'error' : 'done';
        if (resultEv.is_error) {
          el.classList.add('is-error');
          if (foldsToHead) el.open = true;
        }

        const resultSection = h('div', { class: 'tool-card-section' });
        resultSection.appendChild(h('div', { class: 'tool-card-section-label' }, 'Result'));
        const images = resultImages(resultEv.content);
        for (const image of images) resultSection.appendChild(buildResultImage(image, ev.name));
        const text = resultText(resultEv.content);
        const lines = countLines(text);
        // A short result is the answer -- "3 files changed", an error message --
        // and hiding it behind a toggle was most of why the transcript read as
        // a list of things happening to no visible effect. Long output still
        // folds, but says how much there is and shows its first line, so the
        // toggle is a decision rather than a guess (ISSUE-026).
        if (images.length && text.trim() === '') {
          // An image-only result: the picture is the whole answer, and an empty
          // `pre` under it says nothing. A result that is empty for any other
          // reason keeps the empty box it has always had.
        } else if (lines <= 6 && text.length <= 600) {
          resultSection.appendChild(h('pre', { class: 'code-block' }, text));
        } else {
          const body = h('pre', { class: 'code-block' }, text);
          body.style.display = 'none';
          const label = `Show result (${lines.toLocaleString()} lines)`;
          const toggle = h('button', { class: 'tool-result-toggle', type: 'button' }, label);
          toggle.addEventListener('click', () => {
            const showing = body.style.display !== 'none';
            body.style.display = showing ? 'none' : 'block';
            toggle.textContent = showing ? label : 'Hide result';
          });
          const preview = firstMeaningfulLine(text);
          if (preview) resultSection.appendChild(h('div', { class: 'tool-result-preview' }, preview));
          resultSection.appendChild(toggle);
          resultSection.appendChild(body);
        }
        el.appendChild(resultSection);
      },
    };
  }

  // Shown at a size that leaves the transcript readable, and clicking swaps
  // between that and full size. Deliberately not a link to the image: in the
  // desktop pane there is nowhere for a new tab to go, and a data: URI of a
  // couple of megabytes is not something to hand to the system browser.
  function buildResultImage(image, toolName) {
    const img = h('img', {
      class: 'tool-result-image',
      src: image.src,
      alt: `Image returned by ${toolName}`,
      title: 'Click to toggle full size',
      // The picture is now a fetch rather than bytes already in hand, so a
      // transcript with twenty screenshots in it should ask for the ones on
      // screen and not the rest.
      loading: 'lazy',
      decoding: 'async',
      referrerpolicy: 'no-referrer',
    });
    img.addEventListener('click', () => img.classList.toggle('is-full'));
    // A reference can outlive what it points at -- a session closed, a
    // transcript deleted. Say that, rather than leaving the browser's broken
    // image icon to imply the tool failed.
    img.addEventListener('error', () => {
      if (!img.parentNode) return;
      img.replaceWith(h('div', { class: 'tool-result-image-missing' },
                        'This image is no longer available.'));
    });
    return img;
  }

  function buildApprovalCard(ev) {
    const isPlan = ev.name === 'ExitPlanMode';
    const el = h('div', { class: 'approval-card' });
    el.appendChild(h('div', { class: 'approval-card-head' }, isPlan ? 'Plan ready for review' : `Approval needed - ${ev.name}`));

    const body = h('div', { class: 'approval-card-body' });
    const special = renderSpecialToolInput(ev.name, ev.input);
    if (special) {
      body.appendChild(buildCollapsibleSection(special.el, special.lineCount, special.collapseLabels));
    } else {
      body.appendChild(h('pre', { class: 'code-block' }, prettyJson(ev.input)));
    }

    const reasonInput = h('input', { class: 'approval-reason', type: 'text', placeholder: 'reason (optional)' });

    const textarea = h('textarea', {});
    textarea.value = prettyJson(ev.input);
    const editError = h('div', { class: 'approval-edit-error' });
    editError.style.display = 'none';
    const editSubmit = h('button', { type: 'button' }, 'Submit edited input');
    const editBox = h('div', { class: 'approval-edit-box' }, [textarea, editError, editSubmit]);
    editBox.style.display = 'none';

    const btnApprove = h('button', { class: 'btn-approve', type: 'button' }, isPlan ? 'Accept plan' : 'Approve');
    const btnDeny = h('button', { class: 'btn-deny', type: 'button' }, isPlan ? 'Reject plan' : 'Deny');
    const btnEdit = h('button', { class: 'btn-edit', type: 'button' }, 'Approve with edits');
    const actions = h('div', { class: 'approval-actions' }, [btnApprove, btnDeny, btnEdit]);

    const sendError = h('div', { class: 'approval-edit-error' });
    sendError.style.display = 'none';

    let settled = false;
    function setButtonsDisabled(disabled) {
      btnApprove.disabled = disabled;
      btnDeny.disabled = disabled;
      btnEdit.disabled = disabled;
      editSubmit.disabled = disabled;
    }

    function resolveWith(decision, updatedInput) {
      if (settled) return;
      settled = true;
      sendError.style.display = 'none';
      setButtonsDisabled(true);
      send({
        t: 'resolve',
        session_id: ev.session_id,
        request_id: ev.request_id,
        decision,
        updated_input: updatedInput || null,
        reason: reasonInput.value.trim() || null,
      });
    }

    btnApprove.addEventListener('click', () => resolveWith('allow', null));
    btnDeny.addEventListener('click', () => resolveWith('deny', null));
    btnEdit.addEventListener('click', () => {
      editBox.style.display = editBox.style.display === 'none' ? 'block' : 'none';
    });
    editSubmit.addEventListener('click', () => {
      let parsed;
      try {
        parsed = JSON.parse(textarea.value);
      } catch (e) {
        editError.style.display = 'block';
        editError.textContent = 'Not valid JSON: ' + e.message;
        return;
      }
      editError.style.display = 'none';
      resolveWith('allow', parsed);
    });

    body.appendChild(actions);
    body.appendChild(reasonInput);
    body.appendChild(editBox);
    body.appendChild(sendError);
    el.appendChild(body);

    return {
      el,
      resolve(resolvedEv) {
        settled = true;
        el.replaceWith(buildResolvedLine(resolvedEv, ev.name));
      },
      rejected(message) {
        // The server would not take that answer. Hand the card back rather
        // than leaving a dimmed one the user cannot act on. If the request
        // really is gone, its approval_resolved event replaces the card.
        settled = false;
        setButtonsDisabled(false);
        sendError.textContent = message;
        sendError.style.display = 'block';
      },
      expired(message) {
        // Unlike `rejected`, this one is final: there is nothing left to
        // approve, so handing the buttons back would only invite a second
        // refusal (ISSUE-052).
        settled = true;
        setButtonsDisabled(true);
        el.classList.add('is-expired');
        sendError.textContent = message;
        sendError.style.display = 'block';
      },
    };
  }

  // The model asking the user something, rather than asking to do something
  // (ISSUE-050). Built like an approval card and registered in the same maps,
  // so the jump-to-pending control and the stale-request handling cover it
  // without knowing the difference. What it is not is an approval: there is no
  // Deny, because refusing a question only makes the model guess. Skipping
  // sends no choices, which is what the CLI does when a question times out.
  function buildQuestionCard(ev) {
    const questions = ev.questions || [];
    const el = h('div', { class: 'question-card' });
    el.appendChild(h('div', { class: 'question-card-head' },
      questions.length > 1 ? `${questions.length} questions` : 'A question for you'));

    const body = h('div', { class: 'question-card-body' });
    const picked = new Map();   // question text -> Set of chosen labels
    const optionButtons = [];

    for (const q of questions) {
      const qtext = q.question || '';
      const multi = Boolean(q.multiSelect);
      picked.set(qtext, new Set());

      const block = h('div', { class: 'question-block' });
      const head = h('div', { class: 'question-text' }, [
        q.header ? h('span', { class: 'question-header-chip' }, q.header) : null,
        h('span', {}, qtext),
      ]);
      block.appendChild(head);

      const opts = h('div', { class: 'question-options' });
      for (const opt of q.options || []) {
        const btn = h('button', { class: 'question-option', type: 'button' }, [
          h('span', { class: 'question-option-label' }, opt.label),
          opt.description ? h('span', { class: 'question-option-desc' }, opt.description) : null,
        ]);
        btn.addEventListener('click', () => {
          const chosen = picked.get(qtext);
          if (multi) {
            if (chosen.has(opt.label)) chosen.delete(opt.label);
            else chosen.add(opt.label);
          } else {
            chosen.clear();
            chosen.add(opt.label);
            for (const sibling of opts.querySelectorAll('.question-option')) {
              sibling.classList.remove('is-chosen');
            }
          }
          btn.classList.toggle('is-chosen', chosen.has(opt.label));
          updateSubmit();
        });
        opts.appendChild(btn);
        optionButtons.push(btn);
      }
      block.appendChild(opts);
      body.appendChild(block);
    }

    // The CLI adds an "Other" affordance to its own dialog rather than letting
    // the model offer one, so this is the same escape hatch, not an extra.
    const freeform = h('input', { class: 'question-freeform', type: 'text',
                                  placeholder: 'or answer in your own words' });
    freeform.addEventListener('input', updateSubmit);
    body.appendChild(freeform);

    const btnSend = h('button', { class: 'btn-approve', type: 'button' }, 'Answer');
    const btnSkip = h('button', { class: 'btn-deny', type: 'button' }, 'Skip');
    const actions = h('div', { class: 'approval-actions' }, [btnSend, btnSkip]);
    const sendError = h('div', { class: 'approval-edit-error' });
    sendError.style.display = 'none';

    function anyChosen() {
      if (freeform.value.trim()) return true;
      for (const chosen of picked.values()) if (chosen.size) return true;
      return false;
    }
    function updateSubmit() { btnSend.disabled = settled || !anyChosen(); }

    // What the user chose, written out as prose. Only needed when the picker
    // dies under them (ISSUE-052): the choices are still on screen and still
    // the answer, so this is what gets handed back rather than retyped. Each
    // answer keeps its question above it, because four bare labels do not say
    // which was which.
    function composedAnswer() {
      const lines = [];
      for (const [qtext, chosen] of picked) {
        if (chosen.size) lines.push(`${qtext}\n${[...chosen].join(', ')}`);
      }
      const free = freeform.value.trim();
      if (free) lines.push(free);
      return lines.join('\n\n');
    }

    let settled = false;
    function answer(skip) {
      if (settled) return;
      settled = true;
      btnSend.disabled = true;
      btnSkip.disabled = true;
      sendError.style.display = 'none';
      const answers = {};
      if (!skip) {
        for (const [qtext, chosen] of picked) {
          if (chosen.size) answers[qtext] = [...chosen].join(', ');
        }
      }
      send({
        t: 'answer',
        session_id: ev.session_id,
        request_id: ev.request_id,
        answers,
        response: (!skip && freeform.value.trim()) || null,
      });
    }

    btnSend.addEventListener('click', () => answer(false));
    btnSkip.addEventListener('click', () => answer(true));
    updateSubmit();

    body.appendChild(actions);
    body.appendChild(sendError);
    el.appendChild(body);

    return {
      el,
      resolve(resolvedEv) {
        settled = true;
        el.replaceWith(buildQuestionResolvedLine(resolvedEv));
      },
      rejected(message) {
        settled = false;
        btnSkip.disabled = false;
        updateSubmit();
        sendError.textContent = message;
        sendError.style.display = 'block';
      },
      expired(message) {
        // Final, where `rejected` hands the card back: nothing is listening
        // for this answer any more, so the card stops pretending otherwise
        // (ISSUE-052). The questions and the selections stay on screen. They
        // are what the user spent the effort on, and they are about to be
        // worth something again.
        settled = true;
        btnSend.disabled = true;
        btnSkip.disabled = true;
        freeform.disabled = true;
        for (const btn of optionButtons) btn.disabled = true;
        el.classList.add('is-expired');
        sendError.textContent = message;
        sendError.style.display = 'block';
        if (!anyChosen()) return;

        // Into the composer rather than straight down the socket: the turn is
        // still running, and a message the user has not seen leaving is a poor
        // way to find out what was sent on their behalf.
        const recover = h('button', { class: 'btn-edit', type: 'button' },
                          'Put my answers in the composer');
        recover.addEventListener('click', () => {
          composerInput.value = composedAnswer();
          growComposer();
          composerInput.focus();
          recover.disabled = true;
        });
        actions.appendChild(recover);
      },
    };
  }

  function buildQuestionResolvedLine(resolvedEv) {
    const answers = resolvedEv.answers || {};
    const pairs = Object.entries(answers);
    const el = h('div', { class: 'approval-resolved-line' });
    el.appendChild(h('span', {}, 'Answered: '));
    if (!pairs.length && !resolvedEv.response) {
      el.appendChild(h('span', { class: 'decision-deny' }, 'skipped'));
    } else {
      const said = pairs.map(([q, a]) => a).join('; ');
      el.appendChild(h('span', { class: 'decision-allow' },
                       said || resolvedEv.response));
      if (said && resolvedEv.response) {
        el.appendChild(h('span', {}, `- ${resolvedEv.response}`));
      }
    }
    if (resolvedEv.answered_by) {
      el.appendChild(h('span', { class: 'decided-by' }, `by ${resolvedEv.answered_by}`));
    }
    return el;
  }

  // Says what expiring cost, which is different for the two kinds of card: a
  // question that nobody answered leaves Claude guessing, while an approval
  // that nobody gave means the tool never ran (ISSUE-052). Neither line says
  // why it expired, because the two reasons (the wait ran out, or the turn was
  // interrupted) produce the same dead card and the second one the user did
  // themselves.
  function expiredText(ev) {
    return ev.name === 'AskUserQuestion'
      ? 'Expired. Claude did not get an answer to this one.'
      : 'Expired. The tool call did not run.';
  }

  function buildExpiredLine(ev) {
    const el = h('div', { class: 'approval-resolved-line' });
    el.appendChild(h('span', {}, ev.name ? `${ev.name}: ` : 'Approval: '));
    el.appendChild(h('span', { class: 'decision-deny' }, 'expired'));
    el.appendChild(h('span', {}, 'nobody answered it'));
    return el;
  }

  function buildResolvedLine(resolvedEv, name) {
    const isPlan = name === 'ExitPlanMode';
    const decisionCls = resolvedEv.decision === 'allow' ? 'decision-allow' : 'decision-deny';
    const decisionText =
      resolvedEv.decision === 'allow' ? (isPlan ? 'Accepted' : 'Approved') : (isPlan ? 'Rejected' : 'Denied');
    const el = h('div', { class: 'approval-resolved-line' });
    el.appendChild(h('span', {}, name ? `${name}: ` : 'Approval: '));
    el.appendChild(h('span', { class: decisionCls }, decisionText));
    if (resolvedEv.updated_input) el.appendChild(h('span', {}, '(edited)'));
    // Who answered, when the request arrived with a Tailscale identity
    // (ISSUE-022). Absent for a client that authenticated with the token only.
    if (resolvedEv.decided_by) {
      el.appendChild(h('span', { class: 'decided-by' }, `by ${resolvedEv.decided_by}`));
    }
    if (resolvedEv.reason) el.appendChild(h('span', {}, `- ${resolvedEv.reason}`));
    return el;
  }

  function buildUsageLine(ev) {
    const parts = [];
    if (ev.total_cost_usd != null) parts.push(`$${ev.total_cost_usd.toFixed(4)}`);
    // NOTE: the protocol doc leaves `tokens` as an untyped object; render the
    // field names the SDK commonly uses and degrade gracefully otherwise.
    // `tokens` covers only the last API call of the turn and frequently comes
    // back all zeros, which printed as "0 in / 0 out" and read as a bug. Show
    // it when it says something; the running total lives in the footer.
    if (ev.tokens) {
      const t = ev.tokens;
      const anyCount = (t.input_tokens || 0) + (t.output_tokens || 0) +
        (t.cache_read_input_tokens || 0) + (t.cache_creation_input_tokens || 0);
      if (anyCount > 0) {
        const bits = [];
        if (t.input_tokens) bits.push(`${t.input_tokens} in`);
        if (t.output_tokens) bits.push(`${t.output_tokens} out`);
        if (t.cache_read_input_tokens) bits.push(`${t.cache_read_input_tokens} cache read`);
        if (bits.length) parts.push(bits.join(' / '));
      }
    }
    if (!parts.length) parts.push(ev.is_error ? 'turn failed' : 'turn complete');
    return h('div', { class: 'usage-line' + (ev.is_error ? ' is-error' : '') }, parts.join('  ·  '));
  }

  function buildRateLimitBanner(ev) {
    // Same words as the footer, so the banner and the strip below it are not
    // two vocabularies for one fact. `allowed_warning` in particular says
    // nothing to a reader who has not read the SDK's type definitions.
    const window_ = RATE_LIMIT_WINDOW_LABELS[ev.rate_limit_type] || ev.rate_limit_type;
    const status = RATE_LIMIT_STATUS_LABELS[ev.status] || ev.status;
    // "Session limit: limit reached" says limit twice; "Session limit reached"
    // says it once and reads as the sentence it is.
    const head = ev.status === 'rejected'
      ? (window_ ? `${window_} limit reached` : 'Rate limit reached')
      : (window_ ? `${window_} limit: ${status}` : `Rate limit: ${status}`);
    const bits = [head];
    if (ev.utilization != null) bits.push(`${Math.round(ev.utilization * 100)}% used`);
    // NOTE: `resets_at` type is "number|null" with no unit specified; assumed
    // Unix epoch seconds (matches the SDK's other timestamp fields).
    if (ev.resets_at) {
      const verb = ev.status === 'rejected' ? 'resumes' : 'resets';
      bits.push(`${verb} ${fmtResetTime(ev.resets_at)}`);
    }
    // Overage is the only thing that keeps a rejected window working, so say
    // when there is none rather than leaving the reader to wonder.
    if (ev.status === 'rejected' && ev.overage_status === 'rejected') {
      bits.push('no usage credits available');
    }
    return h('div', { class: 'rate-limit-banner' }, bits.join(' · '));
  }

  // A tool call the permission mode let through without asking. Says which
  // mode did it, so a session that stopped prompting is legibly in Auto rather
  // than mysteriously quiet (ISSUE-027).
  function buildPermissionNote(ev) {
    const label = PERMISSION_MODE_LABELS[ev.mode] || ev.mode || 'the permission mode';
    const verb = ev.outcome === 'allow' ? 'auto-approved by' : 'left to the CLI by';
    return h('div', { class: 'system-line' }, `${ev.name || 'Tool call'} ${verb} ${label}`);
  }

  function buildContextBlock(text, label) {
    const body = String(text == null ? '' : text);
    const details = h('details', { class: 'context-block' });
    const heading = firstMeaningfulLine(body) || 'context';
    details.appendChild(
      h('summary', { class: 'context-summary' }, [
        h('span', { class: 'context-label' }, label || 'Context added'),
        h('span', { class: 'context-hint' }, heading),
      ])
    );
    details.appendChild(h('div', { class: 'context-body' }, body));
    return details;
  }

  function buildSystemLine(ev) {
    const isError = ev.subtype === 'error';
    let text;
    if (ev.subtype === 'init') {
      const d = ev.data || {};
      text = `Session initialized — model ${d.model || 'unknown'}${d.cwd ? `, ${d.cwd}` : ''}`;
    } else if (ev.subtype === 'error') {
      text = (ev.data && ev.data.message) || 'Session error';
    } else if (ev.subtype === 'permission_mode') {
      const mode = ev.data && ev.data.permission_mode;
      const label = PERMISSION_MODE_LABELS[mode] || mode || 'unknown';
      text = `Permission mode changed to ${label}. Takes effect on the next tool call.`;
    } else {
      // Bookkeeping subtypes such as thinking_tokens carry no message for a
      // reader. They used to render as a bare "note", which said nothing and
      // pushed the real conversation apart.
      text = (ev.data && ev.data.message) || '';
    }
    if (!text) return null;
    return h('div', { class: 'system-line' + (isError ? ' is-error' : '') }, text);
  }

  function buildUnknownEvent(ev, label) {
    return h('div', { class: 'unknown-event' }, label ? `${label}: ${prettyJson(ev)}` : `Unrecognized event type "${ev.type}": ${prettyJson(ev)}`);
  }

  // ---------------------------------------------------------------------
  // boot
  // ---------------------------------------------------------------------

  async function loadHistory(sdkSessionId) {
    resetTranscript();
    transcriptEl.appendChild(h('div', { class: 'empty-state' }, 'Loading transcript…'));
    setConnStatus('history');
    let body;
    try {
      const res = await apiFetch(`/api/history/${encodeURIComponent(sdkSessionId)}`);
      if (!res.ok) throw new Error(String(res.status));
      body = await res.json();
    } catch {
      resetTranscript();
      transcriptEl.appendChild(
        h('div', { class: 'empty-state' }, 'That transcript could not be read.')
      );
      return;
    }

    paneTitle.textContent = body.title || sdkSessionId;
    paneStatus.textContent = body.cwd || '';
    document.title = `${body.title || 'Transcript'} — Codinian`;

    resetTranscript();
    if (!body.events.length) {
      transcriptEl.appendChild(h('div', { class: 'empty-state' }, 'This conversation is empty.'));
    } else {
      // The same path a live event takes, so a stored transcript and a running
      // one cannot drift apart in how they look.
      for (const ev of body.events) appendEventToDom(ev);
      scrollToBottom();
    }

    // Read-only is stated rather than implied: a composer that silently does
    // nothing reads as broken.
    composerInput.disabled = true;
    composerSend.disabled = true;
    composerInput.placeholder = 'Reading a stored transcript — resume it to continue';
  }

  resetTranscript();
  renderSidebar();
  renderPaneHeader();
  updateComposerState();
  if (historyId) {
    loadHistory(historyId);
  } else {
    startConnection();
  }
})();
