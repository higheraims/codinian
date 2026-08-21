// Search across every stored transcript (ISSUE-015).
//
// Reading a past conversation is a different act from continuing it, and
// conflating the two is why the resume picker was a poor search tool. So a hit
// offers both: Read opens the transcript in the ordinary renderer with the
// composer closed, Resume starts a live session from it.

(function () {
  'use strict';

  const appEl = document.getElementById('app');
  const form = document.getElementById('search-form');
  const input = document.getElementById('search-input');
  const goBtn = document.getElementById('search-go');
  const statusEl = document.getElementById('search-status');
  const resultsEl = document.getElementById('search-results');
  const scopeEl = document.getElementById('search-scope');
  const unauthorizedEl = document.getElementById('unauthorized-screen');

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
      for (const c of (Array.isArray(children) ? children : [children])) {
        if (c == null) continue;
        el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
      }
    }
    return el;
  }

  // Same token handling as the other pages: a token on the URL is saved and
  // stripped so it does not sit in the address bar, and every later request
  // sends it as a header rather than a query string.
  const TOKEN_STORAGE_KEY = 'codinian_token';
  const params = new URLSearchParams(location.search);
  const embedMode = params.get('embed') === '1';
  const themeParam = params.get('theme');
  const scope = params.get('under') || null;
  let authToken = '';

  const tokenFromUrl = params.get('token');
  if (tokenFromUrl) {
    authToken = tokenFromUrl;
    try { localStorage.setItem(TOKEN_STORAGE_KEY, authToken); } catch { /* private mode */ }
    params.delete('token');
    const rest = params.toString();
    history.replaceState(null, '', location.pathname + (rest ? `?${rest}` : ''));
  } else {
    try { authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || ''; } catch { authToken = ''; }
  }
  if (embedMode) appEl.classList.add('embed-mode');
  if (scope) {
    scopeEl.hidden = false;
    scopeEl.textContent = `Searching only ${scope}`;
  }

  function api(path, options) {
    return fetch(path, {
      ...options,
      headers: authToken ? { Authorization: `Bearer ${authToken}` } : {},
    });
  }

  // Carried onto the links this page opens, so Read and Resume land on a page
  // that is authenticated and in the same theme.
  function passthrough() {
    const p = new URLSearchParams();
    if (authToken) p.set('token', authToken);
    if (embedMode) p.set('embed', '1');
    if (themeParam) p.set('theme', themeParam);
    return p;
  }

  function relative(iso) {
    const then = new Date(iso);
    const mins = Math.round((Date.now() - then.getTime()) / 60000);
    if (mins < 60) return `${mins} min ago`;
    if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
    const days = Math.round(mins / (60 * 24));
    if (days < 30) return `${days} d ago`;
    return then.toISOString().slice(0, 10);
  }

  // The needle, marked in the snippet. Built with DOM nodes rather than
  // innerHTML: this text is whatever was in the user's transcript.
  function highlight(text, needle) {
    const frag = document.createDocumentFragment();
    if (!needle) { frag.appendChild(document.createTextNode(text)); return frag; }
    const lower = text.toLowerCase();
    const lowerNeedle = needle.toLowerCase();
    let at = 0;
    for (;;) {
      const found = lower.indexOf(lowerNeedle, at);
      if (found < 0) break;
      if (found > at) frag.appendChild(document.createTextNode(text.slice(at, found)));
      frag.appendChild(h('mark', {}, text.slice(found, found + needle.length)));
      at = found + needle.length;
    }
    frag.appendChild(document.createTextNode(text.slice(at)));
    return frag;
  }

  function buildHit(hit, needle) {
    const card = h('div', { class: 'history-hit' });

    const head = h('div', { class: 'history-hit-head' }, [
      h('span', { class: 'history-hit-title' }, hit.title || '(untitled)'),
      h('span', { class: 'history-hit-count' },
        hit.match_count === 1 ? '1 match' : `${hit.match_count} matches`),
    ]);
    card.appendChild(head);

    card.appendChild(h('div', { class: 'history-hit-meta' }, [
      h('span', { class: 'history-hit-cwd' }, hit.cwd || 'unknown folder'),
      h('span', { class: 'history-hit-date', title: hit.mtime }, relative(hit.mtime)),
    ]));

    for (const snip of hit.snippets) {
      const line = h('div', { class: 'history-snippet' });
      line.appendChild(h('span', { class: 'history-snippet-role' }, snip.role));
      const body = h('span', { class: 'history-snippet-text' });
      body.appendChild(highlight(snip.text, needle));
      line.appendChild(body);
      card.appendChild(line);
    }

    const readHref = `index.html?history=${encodeURIComponent(hit.session_id)}`
      + (passthrough().toString() ? `&${passthrough().toString()}` : '');
    const read = h('a', { class: 'history-action', href: readHref }, 'Read');

    const resume = h('button', { class: 'history-action history-resume', type: 'button' }, 'Resume');
    const note = h('span', { class: 'history-note' });
    resume.addEventListener('click', async () => {
      resume.disabled = true;
      resume.textContent = 'Resuming…';
      try {
        const res = await api(`/api/history/${encodeURIComponent(hit.session_id)}/resume`,
                              { method: 'POST' });
        const body = await res.json();
        if (!res.ok) {
          note.textContent = body.message || body.error || 'Could not resume.';
          resume.disabled = false;
          resume.textContent = 'Resume';
          return;
        }
        // Already-open sessions come back rather than being started twice, so
        // this lands on the running one either way.
        const p = passthrough();
        p.set('session', body.session.id);
        location.href = `index.html?${p.toString()}`;
      } catch {
        note.textContent = 'Could not reach the server.';
        resume.disabled = false;
        resume.textContent = 'Resume';
      }
    });

    card.appendChild(h('div', { class: 'history-actions' }, [read, resume, note]));
    return card;
  }

  let inFlight = 0;
  async function runSearch(needle) {
    const mine = ++inFlight;
    resultsEl.innerHTML = '';
    statusEl.textContent = 'Searching…';
    const started = performance.now();

    let body;
    try {
      const q = new URLSearchParams({ q: needle });
      if (scope) q.set('under', scope);
      const res = await api(`/api/history/search?${q.toString()}`);
      if (res.status === 401) {
        appEl.hidden = true;
        unauthorizedEl.hidden = false;
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      body = await res.json();
    } catch {
      statusEl.textContent = 'The search could not be run.';
      return;
    }
    // A slower earlier query must not overwrite a newer one's results.
    if (mine !== inFlight) return;

    const took = Math.round(performance.now() - started);
    if (!body.hits.length) {
      statusEl.textContent = `Nothing matched "${needle}".`;
      return;
    }
    statusEl.textContent =
      `${body.hits.length} ${body.hits.length === 1 ? 'conversation' : 'conversations'}`
      + ` matched "${needle}" in ${took} ms`;
    for (const hit of body.hits) resultsEl.appendChild(buildHit(hit, needle));
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const needle = input.value.trim();
    if (!needle) return;
    const p = new URLSearchParams(location.search);
    p.set('q', needle);
    history.replaceState(null, '', location.pathname + `?${p.toString()}`);
    runSearch(needle);
  });

  // A query in the URL runs on load, so a search is a link worth keeping.
  const initial = params.get('q');
  if (initial) {
    input.value = initial;
    runSearch(initial);
  }
  goBtn.disabled = false;
})();
