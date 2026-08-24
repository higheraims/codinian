// Codinian project workspace client.
//
// Renders the REST surface described in docs/project-workspace-protocol.md:
// the registered-project list, and four tabs (files, issues, git, sessions)
// for one project. Unlike app.js there is no WebSocket here — nothing on
// this page is pushed, so every view is a plain fetch-then-render.
//
// House style matches app.js: plain DOM built through the h() helper below,
// no framework, no build step, no external dependencies.

(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // DOM builder (same shape as app.js's h())
  // ---------------------------------------------------------------------

  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null) continue;
        if (k === 'class') el.className = v;
        else if (k === 'value' && (tag === 'input' || tag === 'textarea' || tag === 'select')) el.value = v;
        else if (k === 'checked') el.checked = !!v;
        else if (k === 'disabled') el.disabled = !!v;
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

  function clear(el) {
    el.innerHTML = '';
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtBytes(n) {
    if (n == null) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  function fmtWhen(iso) {
    if (!iso) return '';
    const then = new Date(iso);
    if (isNaN(then)) return '';
    const mins = (Date.now() - then.getTime()) / 60000;
    if (mins < 1) return 'just now';
    if (mins < 60) return `${Math.floor(mins)}m ago`;
    if (mins < 60 * 24) return `${Math.floor(mins / 60)}h ago`;
    if (mins < 60 * 24 * 30) return `${Math.floor(mins / 1440)}d ago`;
    return then.toISOString().slice(0, 10);
  }

  // ---------------------------------------------------------------------
  // DOM refs
  // ---------------------------------------------------------------------

  const appEl = document.getElementById('app');
  const unauthorizedEl = document.getElementById('unauthorized-screen');
  const breadcrumbEl = document.getElementById('project-breadcrumb');
  const globalNoticeEl = document.getElementById('global-notice');
  const globalNoticeTextEl = document.getElementById('global-notice-text');
  const globalNoticeCloseEl = document.getElementById('global-notice-close');

  const projectListView = document.getElementById('project-list-view');
  const projectCardsEl = document.getElementById('project-cards');
  const projectAddPathEl = document.getElementById('project-add-path');
  const projectAddBtnEl = document.getElementById('project-add-btn');
  const projectAddErrorEl = document.getElementById('project-add-error');

  const workspaceView = document.getElementById('project-workspace-view');
  const tabNavEl = document.getElementById('tab-nav');
  const tabPanels = {
    files: document.getElementById('tab-files'),
    issues: document.getElementById('tab-issues'),
    git: document.getElementById('tab-git'),
    sessions: document.getElementById('tab-sessions'),
  };

  // ---------------------------------------------------------------------
  // token handling — identical policy to app.js: read ?token=, persist to
  // localStorage under the same key, strip it from the visible URL, send it
  // as Authorization: Bearer on every request.
  // ---------------------------------------------------------------------

  const TOKEN_STORAGE_KEY = 'codinian_token';
  let authToken = '';

  const initialParams = new URLSearchParams(location.search);
  const tokenFromUrl = initialParams.get('token');
  if (tokenFromUrl) {
    authToken = tokenFromUrl;
    try {
      localStorage.setItem(TOKEN_STORAGE_KEY, authToken);
    } catch {
      // Storage unavailable — the token still works for this page load.
    }
  } else {
    try {
      authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || '';
    } catch {
      authToken = '';
    }
  }

  const embedMode = initialParams.get('embed') === '1';
  if (embedMode) appEl.classList.add('embed-mode');

  // Carried through syncUrl below rather than read again: the head script has
  // already stamped data-theme from it, but syncUrl rebuilds the query string
  // from scratch, so without this the choice is dropped on the first
  // replaceState and lost on reload (ISSUE-030).
  const themeParam = initialParams.get('theme');

  // ---------------------------------------------------------------------
  // URL state: project, tab, open file, open issue. Kept in sync with
  // history.replaceState so a reload lands back where the user was.
  // ---------------------------------------------------------------------

  const urlState = {
    project: initialParams.get('project') || null,
    tab: initialParams.get('tab') || 'files',
    file: initialParams.get('file') || null,
    issue: initialParams.get('issue') || null,
  };
  if (!['files', 'issues', 'git', 'sessions'].includes(urlState.tab)) urlState.tab = 'files';

  function syncUrl() {
    const params = new URLSearchParams();
    if (urlState.project) params.set('project', urlState.project);
    if (urlState.project) params.set('tab', urlState.tab);
    if (urlState.tab === 'files' && urlState.file) params.set('file', urlState.file);
    if (urlState.tab === 'issues' && urlState.issue) params.set('issue', urlState.issue);
    if (embedMode) params.set('embed', '1');
    if (themeParam) params.set('theme', themeParam);
    const qs = params.toString();
    history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : ''));
  }
  // Strip the token (and normalise everything else) on first load.
  syncUrl();

  // ---------------------------------------------------------------------
  // fetch helper
  // ---------------------------------------------------------------------

  class ApiError extends Error {
    constructor(status, code, detail, data) {
      super(`${code}: ${detail}`);
      this.status = status;
      this.code = code;
      this.detail = detail;
      this.data = data || null;
    }
  }

  function showUnauthorized() {
    appEl.hidden = true;
    unauthorizedEl.hidden = false;
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign({}, opts.headers || {});
    if (authToken) headers.Authorization = `Bearer ${authToken}`;
    if (opts.jsonBody !== undefined) {
      headers['Content-Type'] = 'application/json';
      opts = Object.assign({}, opts, { body: JSON.stringify(opts.jsonBody) });
    }
    let res;
    try {
      res = await fetch(path, Object.assign({}, opts, { headers }));
    } catch (e) {
      throw new ApiError(0, 'network_error', 'Could not reach the server.');
    }
    if (res.status === 401) {
      showUnauthorized();
      throw new ApiError(401, 'unauthorized', 'Access token required.');
    }
    let data = null;
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
    if (!res.ok) {
      const code = (data && data.error) || `http_${res.status}`;
      const detail = (data && data.detail) || res.statusText || '';
      throw new ApiError(res.status, code, detail, data);
    }
    return data;
  }

  function apiUrl(pid, suffix) {
    return `/api/projects/${encodeURIComponent(pid)}${suffix}`;
  }

  // ---------------------------------------------------------------------
  // global notice — transient info/error banner for things that don't have
  // a more specific home (network hiccups, unexpected failures). Specific,
  // contract-mandated messages (413/415 in the file viewer, git_failed
  // detail, the open-external 403 explanation, commit disabled) render
  // inline in their own tab instead of coming through here.
  // ---------------------------------------------------------------------

  let noticeTimer = null;
  function notify(text, kind) {
    globalNoticeTextEl.textContent = text;
    globalNoticeEl.className = `kind-${kind || 'info'}`;
    globalNoticeEl.hidden = false;
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { globalNoticeEl.hidden = true; }, 7000);
  }
  globalNoticeCloseEl.addEventListener('click', () => { globalNoticeEl.hidden = true; });

  function reportUnexpected(err, context) {
    if (err instanceof ApiError && err.status === 401) return; // already handled
    console.error('codinian project:', context, err);
    notify(`${context}: ${err.detail || err.message || 'unknown error'}`, 'error');
  }

  // ---------------------------------------------------------------------
  // state
  // ---------------------------------------------------------------------

  const state = {
    project: null,       // ProjectMeta
    settings: null,       // Settings (issues schema lives at settings.issues)
    git: null,             // GitInfo | {is_repo:false} | null
    sessions: [],
    history: [],           // prior `claude` sessions run in this folder
    resumingId: null,      // sdk_session_id currently being resumed
    startingSession: false, // a fresh session is being created

    // files tab
    fileTreeCache: new Map(), // rel path ("" for root) -> entries[]
    fileTreeExpanded: new Set(),
    openFile: null,        // {path, content, size, mtime}
    fileEditing: false,
    fileDraft: '',
    fileViewerError: null, // {kind: 'too_large'|'binary', size}
    fileNewForm: null,     // {parentPath, kind: 'file'|'dir'}
    fileRenamePath: null,

    // issues tab
    issuesList: [],
    issuesSchema: null,
    issuesFilter: { status: null, type: null, area: null },
    issuesSort: 'id',
    currentIssue: null,     // full Issue being viewed/edited
    issueDraft: null,       // editable copy of currentIssue
    issuePristine: null,    // signature of issueDraft as loaded; see issueDirty
    issueSaving: false,
    issueSaveError: null,
    sectionPreview: new Set(), // section indices currently showing preview

    // git tab
    gitChecked: new Set(),  // paths checked for commit
    commitMessage: '',
    gitBusy: false,
    gitError: null,         // verbatim stderr from a failed git call
    gitignore: null,        // {content, exists}
  };

  // ---------------------------------------------------------------------
  // boot
  // ---------------------------------------------------------------------

  async function boot() {
    if (!urlState.project) {
      showProjectList();
      await loadProjectList();
      return;
    }
    showWorkspace();
    await loadProject(urlState.project);
  }

  function showProjectList() {
    projectListView.hidden = false;
    workspaceView.hidden = true;
    breadcrumbEl.textContent = '';
  }

  function showWorkspace() {
    projectListView.hidden = true;
    workspaceView.hidden = false;
  }

  // ---------------------------------------------------------------------
  // project list
  // ---------------------------------------------------------------------

  async function loadProjectList() {
    clear(projectCardsEl);
    projectCardsEl.appendChild(h('div', { class: 'empty-note' }, 'Loading…'));
    let data;
    try {
      data = await api('/api/projects');
    } catch (e) {
      if (e.status === 401) return;
      reportUnexpected(e, 'Could not load projects');
      clear(projectCardsEl);
      return;
    }
    renderProjectCards(data.projects || []);
  }

  function renderProjectCards(projects) {
    clear(projectCardsEl);
    if (projects.length === 0) {
      projectCardsEl.appendChild(h('div', { class: 'empty-note' }, 'No projects registered yet. Add one above.'));
      return;
    }
    for (const p of projects) {
      const card = h('div', { class: 'project-card' + (p.exists ? '' : ' is-missing') });
      const nameRow = h('div', { class: 'project-card-name' }, [
        h('span', {}, p.name),
        p.exists ? null : h('span', { class: 'project-card-missing-badge' }, 'missing'),
      ]);
      card.appendChild(nameRow);
      card.appendChild(h('div', { class: 'project-card-path' }, p.path));
      const actions = h('div', { class: 'project-card-actions' });
      const openLink = h('a', { class: 'btn-open', href: `project.html?project=${encodeURIComponent(p.id)}` }, 'Open');
      actions.appendChild(openLink);
      const removeBtn = h('button', { class: 'btn-remove', type: 'button' }, 'Remove');
      removeBtn.addEventListener('click', () => removeProject(p));
      actions.appendChild(removeBtn);
      card.appendChild(actions);
      projectCardsEl.appendChild(card);
    }
  }

  async function removeProject(p) {
    const ok = window.confirm(
      `Deregister "${p.name}"? This only removes it from Codinian — nothing on disk at ${p.path} is touched.`
    );
    if (!ok) return;
    try {
      await api(apiUrl(p.id, ''), { method: 'DELETE' });
    } catch (e) {
      reportUnexpected(e, 'Could not remove project');
      return;
    }
    loadProjectList();
  }

  projectAddBtnEl.addEventListener('click', async () => {
    const path = projectAddPathEl.value.trim();
    projectAddErrorEl.hidden = true;
    if (!path) return;
    projectAddBtnEl.disabled = true;
    try {
      await api('/api/projects', { method: 'POST', jsonBody: { path } });
      projectAddPathEl.value = '';
      await loadProjectList();
    } catch (e) {
      projectAddErrorEl.textContent = e.detail || 'Could not add that project.';
      projectAddErrorEl.hidden = false;
    } finally {
      projectAddBtnEl.disabled = false;
    }
  });
  projectAddPathEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') projectAddBtnEl.click();
  });

  // ---------------------------------------------------------------------
  // workspace shell
  // ---------------------------------------------------------------------

  async function loadProject(pid) {
    let data;
    try {
      data = await api(apiUrl(pid, ''));
    } catch (e) {
      if (e.status === 401) return;
      if (e.status === 404) {
        notify('That project is not registered on this server.', 'error');
        urlState.project = null;
        syncUrl();
        showProjectList();
        loadProjectList();
        return;
      }
      reportUnexpected(e, 'Could not load project');
      return;
    }
    state.project = data.project;
    state.settings = data.settings;
    state.git = data.git;
    state.sessions = data.sessions || [];
    state.history = data.history || [];
    state.fileTreeCache = new Map();
    state.fileTreeExpanded = new Set();
    state.gitChecked = new Set();

    renderBreadcrumb();
    renderTabNav();
    selectTab(urlState.tab, { skipUrlSync: true });
  }

  // The page fetches when a tab opens and never hears about a file changing
  // underneath it, so an edit made in an editor elsewhere was invisible until
  // a reload. Refreshing when the page regains focus covers that without a
  // filesystem watcher, a per-project subscription, or any new protocol.
  //
  // It refuses while anything is open for editing. loadProject re-renders the
  // tab from scratch and there is no draft to restore afterwards, so a refresh
  // mid-edit would discard what you had typed.
  //
  // An issue is the one case where being open is not the same as being edited.
  // There is no read-only mode: opening one for a look fills `issueDraft` in
  // exactly as opening one to change it does, and nothing clears it, so gating
  // on the draft's existence meant a single look at a single issue switched
  // focus-refresh off for the rest of the page's life -- Files and Git tabs
  // included (ISSUE-039). What blocks a refresh is the draft having actually
  // diverged from what was loaded.
  function issueDirty() {
    if (!state.issueDraft) return false;
    return issueDraftSignature(state.issueDraft) !== state.issuePristine;
  }

  // Everything `saveIssue` would send, in a stable order, as one string to
  // compare against the snapshot taken when the draft was made. Deliberately
  // not `extraFrontmatter`: it is carried through verbatim and nothing in the
  // editor can change it.
  function issueDraftSignature(draft) {
    return JSON.stringify([
      draft.id, draft.title, draft.status, draft.type, draft.area,
      draft.related,
      draft.sections.map((s) => [s.heading, s.body]),
    ]);
  }

  function markIssueDraftPristine() {
    state.issuePristine = state.issueDraft ? issueDraftSignature(state.issueDraft) : null;
  }

  function isEditing() {
    return Boolean(state.fileEditing || issueDirty() ||
                   state.fileNewForm || state.fileRenamePath);
  }

  let refreshing = false;
  async function refreshFromDisk() {
    if (refreshing || !state.project || isEditing()) return false;
    refreshing = true;
    // loadProject resets these, and losing the tree you had open on every
    // window switch would be worse than the staleness being fixed.
    const expanded = new Set(state.fileTreeExpanded);
    try {
      await loadProject(state.project.id);
      state.fileTreeExpanded = expanded;
      if (urlState.tab === 'files') await renderFilesTab();
    } finally {
      refreshing = false;
    }
    return true;
  }

  // Reachable from the desktop, which re-selects a pane that never lost focus
  // in the browser sense.
  window.codinianRefresh = refreshFromDisk;
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshFromDisk();
  });
  window.addEventListener('focus', () => { refreshFromDisk(); });

  function renderBreadcrumb() {
    clear(breadcrumbEl);
    const backLink = h('a', { href: 'project.html' }, 'Projects');
    breadcrumbEl.appendChild(backLink);
    breadcrumbEl.appendChild(document.createTextNode(' / ' + (state.project ? state.project.name : '')));
  }

  function renderTabNav() {
    for (const btn of tabNavEl.querySelectorAll('.tab-btn')) {
      btn.classList.toggle('active', btn.dataset.tab === urlState.tab);
    }
  }

  for (const btn of tabNavEl.querySelectorAll('.tab-btn')) {
    btn.addEventListener('click', () => selectTab(btn.dataset.tab));
  }

  function selectTab(tab, opts) {
    urlState.tab = tab;
    for (const [name, panel] of Object.entries(tabPanels)) {
      panel.classList.toggle('active', name === tab);
    }
    renderTabNav();
    if (!(opts && opts.skipUrlSync)) syncUrl();
    else syncUrl();

    if (tab === 'files') renderFilesTab();
    else if (tab === 'issues') renderIssuesTab();
    else if (tab === 'git') renderGitTab();
    else if (tab === 'sessions') renderSessionsTab();
  }

  // =======================================================================
  // FILES TAB
  // =======================================================================

  async function renderFilesTab() {
    const panel = tabPanels.files;
    clear(panel);

    const toolbar = h('div', { class: 'files-toolbar' });
    const newFileBtn = h('button', { type: 'button' }, '+ File (root)');
    const newFolderBtn = h('button', { type: 'button' }, '+ Folder (root)');
    newFileBtn.addEventListener('click', () => startNewEntry('', 'file'));
    newFolderBtn.addEventListener('click', () => startNewEntry('', 'dir'));
    toolbar.appendChild(newFileBtn);
    toolbar.appendChild(newFolderBtn);

    const layout = h('div', { class: 'files-layout' });
    const treePane = h('div', { class: 'file-tree-pane', id: 'file-tree-root' });
    const viewerPane = h('div', { class: 'file-viewer-pane', id: 'file-viewer-pane' });
    layout.appendChild(treePane);
    layout.appendChild(viewerPane);

    panel.appendChild(toolbar);
    panel.appendChild(layout);

    await renderTreeLevel(treePane, '', 0);
    renderFileViewer();

    // If the URL named an open file, load it now.
    if (urlState.file && (!state.openFile || state.openFile.path !== urlState.file)) {
      openFile(urlState.file);
    }
  }

  async function fetchDirEntries(relPath) {
    if (state.fileTreeCache.has(relPath)) return state.fileTreeCache.get(relPath);
    const data = await api(apiUrl(state.project.id, `/files?path=${encodeURIComponent(relPath)}`));
    state.fileTreeCache.set(relPath, data.entries || []);
    return data.entries || [];
  }

  async function renderTreeLevel(container, relPath, depth) {
    clear(container);
    let entries;
    try {
      entries = await fetchDirEntries(relPath);
    } catch (e) {
      container.appendChild(h('div', { class: 'tree-empty' }, `Could not list: ${e.detail || e.message}`));
      return;
    }
    if (relPath === '' && depth === 0) {
      // top-level new-entry form, if active on root
      if (state.fileNewForm && state.fileNewForm.parentPath === '') {
        container.appendChild(buildNewEntryRow('', depth));
      }
    }
    if (entries.length === 0 && !(state.fileNewForm && state.fileNewForm.parentPath === relPath)) {
      container.appendChild(h('div', { class: 'tree-empty' }, '(empty)'));
    }
    for (const entry of entries) {
      container.appendChild(buildTreeRow(entry, depth));
      if (entry.is_dir && state.fileTreeExpanded.has(entry.path)) {
        const childWrap = h('div', { class: 'tree-children' });
        container.appendChild(childWrap);
        renderTreeLevel(childWrap, entry.path, depth + 1);
      }
    }
  }

  function buildNewEntryRow(parentPath, depth) {
    const form = h('div', { class: 'tree-new-row', style: `padding-left:${0.6 + depth * 1.1}rem` });
    const input = h('input', { type: 'text', placeholder: state.fileNewForm.kind === 'dir' ? 'folder name' : 'file name' });
    const create = h('button', { class: 'tree-icon-btn', type: 'button' }, 'Create');
    const cancel = h('button', { class: 'tree-icon-btn', type: 'button' }, 'Cancel');
    create.addEventListener('click', () => submitNewEntry(parentPath, input.value.trim()));
    cancel.addEventListener('click', () => { state.fileNewForm = null; refreshFileTree(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') submitNewEntry(parentPath, input.value.trim());
      if (e.key === 'Escape') { state.fileNewForm = null; refreshFileTree(); }
    });
    form.appendChild(input);
    form.appendChild(create);
    form.appendChild(cancel);
    setTimeout(() => input.focus(), 0);
    return form;
  }

  async function submitNewEntry(parentPath, name) {
    if (!name) return;
    const kind = state.fileNewForm.kind;
    const relPath = parentPath ? `${parentPath}/${name}` : name;
    try {
      await api(apiUrl(state.project.id, '/file/new'), { method: 'POST', jsonBody: { path: relPath, kind } });
    } catch (e) {
      notify(e.code === 'exists' ? `${relPath} already exists.` : (e.detail || 'Could not create that.'), 'error');
      return;
    }
    state.fileNewForm = null;
    state.fileTreeCache.delete(parentPath);
    if (kind === 'dir') state.fileTreeExpanded.add(relPath);
    await refreshFileTree();
    if (kind === 'file') openFile(relPath);
  }

  function startNewEntry(parentPath, kind) {
    state.fileNewForm = { parentPath, kind };
    if (parentPath) state.fileTreeExpanded.add(parentPath);
    refreshFileTree();
  }

  function buildTreeRow(entry, depth) {
    const isRenaming = state.fileRenamePath === entry.path;
    if (isRenaming) {
      const row = h('div', { class: 'tree-rename-row', style: `padding-left:${0.6 + depth * 1.1}rem` });
      const input = h('input', { type: 'text', value: entry.name });
      const confirm = h('button', { class: 'tree-icon-btn', type: 'button' }, 'Rename');
      const cancel = h('button', { class: 'tree-icon-btn', type: 'button' }, 'Cancel');
      confirm.addEventListener('click', () => submitRename(entry, input.value.trim()));
      cancel.addEventListener('click', () => { state.fileRenamePath = null; refreshFileTree(); });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') submitRename(entry, input.value.trim());
        if (e.key === 'Escape') { state.fileRenamePath = null; refreshFileTree(); }
      });
      row.appendChild(input);
      row.appendChild(confirm);
      row.appendChild(cancel);
      setTimeout(() => { input.focus(); input.select(); }, 0);
      return row;
    }

    const row = h('div', {
      class: 'tree-row' + (entry.ignored ? ' is-ignored' : '') + (state.openFile && state.openFile.path === entry.path ? ' is-selected' : ''),
      style: `padding-left:${depth * 1.1}rem`,
    });

    const expanded = entry.is_dir && state.fileTreeExpanded.has(entry.path);
    const toggle = h('button', { class: 'tree-toggle' + (entry.is_dir ? '' : ' is-leaf'), type: 'button' }, entry.is_dir ? (expanded ? '▾' : '▸') : '');
    if (entry.is_dir) {
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleDir(entry.path);
      });
    }
    row.appendChild(toggle);
    row.appendChild(h('span', { class: 'tree-name' }, entry.name));

    const actions = h('div', { class: 'tree-actions' });
    if (entry.is_dir) {
      const newFileBtn = h('button', { class: 'tree-icon-btn', type: 'button', title: 'New file here' }, '+f');
      const newFolderBtn = h('button', { class: 'tree-icon-btn', type: 'button', title: 'New folder here' }, '+d');
      newFileBtn.addEventListener('click', (e) => { e.stopPropagation(); startNewEntry(entry.path, 'file'); });
      newFolderBtn.addEventListener('click', (e) => { e.stopPropagation(); startNewEntry(entry.path, 'dir'); });
      actions.appendChild(newFileBtn);
      actions.appendChild(newFolderBtn);
    } else {
      const openExtBtn = h('button', { class: 'tree-icon-btn', type: 'button', title: 'Open externally' }, '↗');
      openExtBtn.addEventListener('click', (e) => { e.stopPropagation(); openExternal(entry.path); });
      actions.appendChild(openExtBtn);
    }
    const renameBtn = h('button', { class: 'tree-icon-btn', type: 'button', title: 'Rename' }, '✎');
    renameBtn.addEventListener('click', (e) => { e.stopPropagation(); state.fileRenamePath = entry.path; refreshFileTree(); });
    actions.appendChild(renameBtn);
    row.appendChild(actions);

    row.addEventListener('click', () => {
      if (entry.is_dir) toggleDir(entry.path);
      else openFile(entry.path);
    });

    return row;
  }

  function toggleDir(path) {
    if (state.fileTreeExpanded.has(path)) state.fileTreeExpanded.delete(path);
    else state.fileTreeExpanded.add(path);
    refreshFileTree();
  }

  async function submitRename(entry, newName) {
    if (!newName || newName === entry.name) {
      state.fileRenamePath = null;
      refreshFileTree();
      return;
    }
    const parentPath = entry.path.includes('/') ? entry.path.slice(0, entry.path.lastIndexOf('/')) : '';
    const to = parentPath ? `${parentPath}/${newName}` : newName;
    try {
      await api(apiUrl(state.project.id, '/file/rename'), { method: 'POST', jsonBody: { path: entry.path, to } });
    } catch (e) {
      notify(e.code === 'exists' ? `${to} already exists.` : (e.detail || 'Could not rename that.'), 'error');
      return;
    }
    state.fileRenamePath = null;
    state.fileTreeCache.delete(parentPath);
    if (state.openFile && state.openFile.path === entry.path) {
      state.openFile = null;
      urlState.file = null;
      syncUrl();
    }
    await refreshFileTree();
    renderFileViewer();
  }

  async function openExternal(path) {
    try {
      await api(apiUrl(state.project.id, '/open-external'), { method: 'POST', jsonBody: { path } });
      notify(`Opened ${path} on the machine running the server.`, 'info');
    } catch (e) {
      if (e.code === 'not_local') {
        notify('Opening externally only works from the desktop app on the machine running the server, not from a remote browser.', 'info');
      } else {
        reportUnexpected(e, 'Could not open externally');
      }
    }
  }

  async function refreshFileTree() {
    const root = document.getElementById('file-tree-root');
    if (root) await renderTreeLevel(root, '', 0);
  }

  async function openFile(path) {
    state.openFile = null;
    state.fileEditing = false;
    state.fileViewerError = null;
    urlState.file = path;
    syncUrl();
    renderFileViewer(true);
    let data;
    try {
      data = await api(apiUrl(state.project.id, `/file?path=${encodeURIComponent(path)}`));
    } catch (e) {
      if (e.status === 413) {
        state.fileViewerError = { kind: 'too_large', size: (e.data && e.data.size) };
      } else if (e.status === 415) {
        state.fileViewerError = { kind: 'binary', size: (e.data && e.data.size) };
      } else {
        reportUnexpected(e, 'Could not open file');
        state.openFile = null;
        renderFileViewer();
        return;
      }
      state.openFile = { path };
      renderFileViewer();
      return;
    }
    state.openFile = data;
    state.fileDraft = data.content;
    renderFileViewer();
    refreshFileTree();
  }

  function renderFileViewer(loading) {
    const pane = document.getElementById('file-viewer-pane');
    if (!pane) return;
    clear(pane);

    if (loading) {
      pane.appendChild(h('div', { class: 'file-viewer-empty' }, 'Loading…'));
      return;
    }
    if (!state.openFile) {
      pane.appendChild(h('div', { class: 'file-viewer-empty' }, 'Select a file to view it.'));
      return;
    }
    const header = h('div', { class: 'file-viewer-header' });
    header.appendChild(h('span', { class: 'file-viewer-path' }, state.openFile.path));
    if (state.openFile.size != null) {
      header.appendChild(h('span', { class: 'file-viewer-meta' }, fmtBytes(state.openFile.size)));
    }
    const actions = h('div', { class: 'file-viewer-actions' });
    header.appendChild(actions);
    pane.appendChild(header);

    const body = h('div', { class: 'file-viewer-body' });
    pane.appendChild(body);

    if (state.fileViewerError) {
      const kind = state.fileViewerError.kind;
      const size = state.fileViewerError.size;
      const msg = kind === 'too_large'
        ? `This file is too large to open here (${fmtBytes(size)}, over the 512 KB limit).`
        : `This does not look like a text file (${fmtBytes(size)}). Binary files can't be opened in this viewer.`;
      body.appendChild(h('div', { class: 'file-viewer-error' }, msg));
      return;
    }

    if (state.fileEditing) {
      const textarea = h('textarea', { class: 'file-viewer-textarea', spellcheck: 'false' });
      textarea.value = state.fileDraft;
      textarea.addEventListener('input', () => { state.fileDraft = textarea.value; });
      body.appendChild(textarea);

      const saveBtn = h('button', { type: 'button', class: 'is-primary' }, 'Save');
      const cancelBtn = h('button', { type: 'button' }, 'Cancel');
      saveBtn.addEventListener('click', () => saveFile(saveBtn));
      cancelBtn.addEventListener('click', () => {
        state.fileEditing = false;
        state.fileDraft = state.openFile.content;
        renderFileViewer();
      });
      actions.appendChild(saveBtn);
      actions.appendChild(cancelBtn);
    } else {
      body.appendChild(h('pre', { class: 'file-viewer-pre' }, state.openFile.content || ''));
      const editBtn = h('button', { type: 'button' }, 'Edit');
      editBtn.addEventListener('click', () => {
        state.fileEditing = true;
        state.fileDraft = state.openFile.content;
        renderFileViewer();
      });
      actions.appendChild(editBtn);
    }
  }

  async function saveFile(btn) {
    btn.disabled = true;
    try {
      const data = await api(apiUrl(state.project.id, '/file'), {
        method: 'PUT',
        jsonBody: { path: state.openFile.path, content: state.fileDraft },
      });
      state.openFile = Object.assign({}, state.openFile, { content: state.fileDraft, size: data.size, mtime: data.mtime });
      state.fileEditing = false;
      renderFileViewer();
      notify('Saved.', 'info');
    } catch (e) {
      reportUnexpected(e, 'Could not save file');
    } finally {
      btn.disabled = false;
    }
  }

  // =======================================================================
  // ISSUES TAB
  // =======================================================================

  async function renderIssuesTab() {
    const panel = tabPanels.issues;
    clear(panel);

    const layout = h('div', { class: 'issues-layout' });
    const listPane = h('div', { class: 'issues-list-pane' });
    const editorPane = h('div', { class: 'issue-editor-pane', id: 'issue-editor-pane' });
    layout.appendChild(listPane);
    layout.appendChild(editorPane);
    panel.appendChild(layout);

    listPane.appendChild(h('div', { class: 'empty-note' }, 'Loading…'));

    let data;
    try {
      data = await api(apiUrl(state.project.id, '/issues'));
    } catch (e) {
      clear(listPane);
      listPane.appendChild(h('div', { class: 'empty-note' }, `Could not load issues: ${e.detail || e.message}`));
      return;
    }
    state.issuesList = data.issues || [];
    state.issuesSchema = data.schema || {};

    renderIssuesListPane(listPane);
    renderIssueEditorPane();

    if (urlState.issue && (!state.currentIssue || state.currentIssue.id !== urlState.issue)) {
      loadIssue(urlState.issue);
    }
  }

  function renderIssuesListPane(listPane) {
    clear(listPane);
    const schema = state.issuesSchema || {};

    const toolbar = h('div', { class: 'issues-toolbar' });
    const topRow = h('div', { class: 'issues-toolbar-row' });
    const newBtn = h('button', { class: 'new-issue-btn', type: 'button' }, '+ New issue');
    newBtn.addEventListener('click', startNewIssue);
    topRow.appendChild(newBtn);

    const sortSelect = h('select', { class: 'issues-sort-select' }, [
      h('option', { value: 'id' }, 'Sort: id'),
      h('option', { value: 'updated' }, 'Sort: updated'),
      h('option', { value: 'status' }, 'Sort: status'),
    ]);
    sortSelect.value = state.issuesSort;
    sortSelect.addEventListener('change', () => {
      state.issuesSort = sortSelect.value;
      renderIssuesList(listPane.querySelector('.issues-list'));
    });
    topRow.appendChild(sortSelect);
    toolbar.appendChild(topRow);

    // Filter chips, built entirely from schema — never hard-coded (ISSUE-019).
    function buildChipGroup(label, key, options) {
      const group = h('div', { class: 'chip-group' });
      group.appendChild(h('span', { class: 'chip-group-label' }, label));
      const allChip = h('button', { class: 'chip' + (state.issuesFilter[key] ? '' : ' is-active'), type: 'button' }, 'all');
      allChip.addEventListener('click', () => { state.issuesFilter[key] = null; renderIssuesListPane(listPane); });
      group.appendChild(allChip);
      for (const opt of options || []) {
        const chip = h('button', { class: 'chip' + (state.issuesFilter[key] === opt ? ' is-active' : ''), type: 'button' }, opt);
        chip.addEventListener('click', () => { state.issuesFilter[key] = opt; renderIssuesListPane(listPane); });
        group.appendChild(chip);
      }
      return group;
    }
    toolbar.appendChild(buildChipGroup('Status', 'status', schema.statuses));
    toolbar.appendChild(buildChipGroup('Type', 'type', schema.types));
    toolbar.appendChild(buildChipGroup('Area', 'area', schema.areas));

    listPane.appendChild(toolbar);

    const listEl = h('div', { class: 'issues-list' });
    listPane.appendChild(listEl);
    renderIssuesList(listEl);
  }

  function renderIssuesList(listEl) {
    if (!listEl) return;
    clear(listEl);
    const f = state.issuesFilter;
    let items = state.issuesList.filter((issue) => {
      const fm = issue.frontmatter || {};
      if (f.status && fm.status !== f.status) return false;
      if (f.type && fm.type !== f.type) return false;
      if (f.area && fm.area !== f.area) return false;
      return true;
    });
    items = items.slice().sort((a, b) => {
      const fa = a.frontmatter || {}, fb = b.frontmatter || {};
      if (state.issuesSort === 'updated') return (fb.updated || '').localeCompare(fa.updated || '');
      if (state.issuesSort === 'status') return (fa.status || '').localeCompare(fb.status || '');
      return (b.num || 0) - (a.num || 0);
    });

    if (items.length === 0) {
      listEl.appendChild(h('div', { class: 'empty-note' }, 'No issues match.'));
      return;
    }

    for (const issue of items) {
      const fm = issue.frontmatter || {};
      const row = h('div', { class: 'issue-row' + (state.currentIssue && state.currentIssue.id === issue.id ? ' is-active' : '') });
      row.appendChild(h('div', { class: 'issue-row-top' }, [
        h('span', { class: 'issue-row-id' }, issue.id),
        h('span', { class: 'issue-row-title' }, fm.title || '(untitled)'),
      ]));
      const statusClass = 'status-' + String(fm.status || '').toLowerCase();
      row.appendChild(h('div', { class: 'issue-row-meta' }, [
        h('span', { class: `status-pill ${statusClass}` }, fm.status || '—'),
        h('span', { class: 'status-pill' }, fm.type || '—'),
        h('span', { class: 'status-pill' }, fm.area || '—'),
      ]));
      if (issue.excerpt) row.appendChild(h('div', { class: 'issue-row-excerpt' }, issue.excerpt));
      row.addEventListener('click', () => loadIssue(issue.id));
      listEl.appendChild(row);
    }
  }

  async function loadIssue(issueId) {
    urlState.issue = issueId;
    syncUrl();
    const pane = document.getElementById('issue-editor-pane');
    if (pane) {
      clear(pane);
      pane.appendChild(h('div', { class: 'issue-editor-empty' }, 'Loading…'));
    }
    let data;
    try {
      data = await api(apiUrl(state.project.id, `/issues/${encodeURIComponent(issueId)}`));
    } catch (e) {
      reportUnexpected(e, 'Could not load issue');
      return;
    }
    state.currentIssue = data.issue;
    state.issueDraft = cloneIssueForEdit(data.issue);
    markIssueDraftPristine();
    state.issueSaveError = null;
    state.sectionPreview = new Set();
    const listEl = tabPanels.issues.querySelector('.issues-list');
    if (listEl) renderIssuesList(listEl);
    renderIssueEditorPane();
  }

  function cloneIssueForEdit(issue) {
    const fm = issue.frontmatter || {};
    return {
      id: issue.id,
      title: fm.title || '',
      status: fm.status || '',
      type: fm.type || '',
      area: fm.area || '',
      created: fm.created || '',
      updated: fm.updated || '',
      related: Array.isArray(fm.related) ? fm.related.slice() : [],
      extraFrontmatter: fm, // preserved verbatim, minus the fields above, on save
      sections: (issue.sections || []).map((s) => ({ heading: s.heading, body: s.body })),
    };
  }

  function startNewIssue() {
    const schema = state.issuesSchema || {};
    state.currentIssue = null;
    state.issueDraft = {
      id: null,
      title: '',
      status: (schema.statuses && schema.statuses[0]) || 'open',
      type: (schema.types && schema.types[0]) || '',
      area: (schema.areas && schema.areas[0]) || '',
      created: '',
      updated: '',
      related: [],
      extraFrontmatter: {},
      sections: (schema.sections || []).map((heading) => ({ heading, body: '' })),
    };
    // A blank form is not an edit. It becomes one on the first keystroke, and
    // until then a focus-refresh is welcome to take it away.
    markIssueDraftPristine();
    state.issueSaveError = null;
    state.sectionPreview = new Set();
    urlState.issue = null;
    syncUrl();
    renderIssueEditorPane();
  }

  function renderIssueEditorPane() {
    const pane = document.getElementById('issue-editor-pane');
    if (!pane) return;
    clear(pane);
    const draft = state.issueDraft;
    if (!draft) {
      pane.appendChild(h('div', { class: 'issue-editor-empty' }, 'Select an issue from the list, or create a new one.'));
      return;
    }

    const schema = state.issuesSchema || {};
    const editor = h('div', { class: 'issue-editor' });

    // ---- front matter ----
    const grid = h('div', { class: 'frontmatter-grid' });

    function field(labelText, control, span2) {
      return h('div', { class: 'ff-field' + (span2 ? ' span-2' : '') }, [
        h('label', { class: 'ff-label' }, labelText),
        control,
      ]);
    }

    const titleInput = h('input', { class: 'ff-input', type: 'text', value: draft.title });
    titleInput.addEventListener('input', () => { draft.title = titleInput.value; });
    grid.appendChild(field('Title', titleInput, true));

    function selectField(labelText, key, options) {
      const opts = options && options.length ? options : (draft[key] ? [draft[key]] : []);
      const sel = h('select', { class: 'ff-select' }, opts.map((o) => h('option', { value: o }, o)));
      if (draft[key] && !opts.includes(draft[key])) sel.appendChild(h('option', { value: draft[key] }, draft[key]));
      sel.value = draft[key] || '';
      sel.addEventListener('change', () => { draft[key] = sel.value; });
      return field(labelText, sel);
    }
    grid.appendChild(selectField('Status', 'status', schema.statuses));
    grid.appendChild(selectField('Type', 'type', schema.types));
    grid.appendChild(selectField('Area', 'area', schema.areas));

    grid.appendChild(field('Created', h('div', { class: 'ff-readonly' }, draft.created || '(on save)')));
    grid.appendChild(field('Updated', h('div', { class: 'ff-readonly' }, draft.updated || '(on save)')));

    // related: token input
    const relatedRow = h('div', { class: 'related-input-row' });
    function renderRelatedChips() {
      clear(relatedRow);
      for (const id of draft.related) {
        const chip = h('span', { class: 'related-chip' }, [
          h('span', {}, id),
        ]);
        const removeBtn = h('button', { class: 'related-chip-remove', type: 'button' }, '×');
        removeBtn.addEventListener('click', () => {
          draft.related = draft.related.filter((r) => r !== id);
          renderRelatedChips();
        });
        chip.appendChild(removeBtn);
        relatedRow.appendChild(chip);
      }
      const input = h('input', { class: 'related-new-input', type: 'text', placeholder: draft.related.length ? 'add another…' : 'e.g. ISSUE-020' });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ',') {
          e.preventDefault();
          addRelatedToken(input);
        }
      });
      input.addEventListener('blur', () => addRelatedToken(input));
      relatedRow.appendChild(input);
    }
    function addRelatedToken(input) {
      let val = input.value.trim().replace(/,$/, '');
      if (!val) return;
      // Normalise a bare number to the canonical ISSUE-NNN token.
      const prefix = (schema.id_prefix || 'ISSUE');
      if (/^\d+$/.test(val)) val = `${prefix}-${val.padStart(3, '0')}`;
      if (!draft.related.includes(val)) draft.related.push(val);
      input.value = '';
      renderRelatedChips();
    }
    renderRelatedChips();
    grid.appendChild(field('Related', relatedRow, true));

    editor.appendChild(grid);

    // ---- sections ----
    const sectionsEl = h('div', { class: 'issue-sections' });
    draft.sections.forEach((section, idx) => sectionsEl.appendChild(buildSectionEditor(section, idx)));
    editor.appendChild(sectionsEl);

    // add-section control, offering schema sections not already present
    const existingHeadings = new Set(draft.sections.map((s) => s.heading));
    const available = (schema.sections || []).filter((s) => !existingHeadings.has(s));
    const addRow = h('div', { class: 'add-section-row' });
    const addSelect = h('select', {}, available.map((s) => h('option', { value: s }, s)));
    const addCustom = h('input', { type: 'text', placeholder: 'or a custom heading' });
    const addBtn = h('button', { type: 'button' }, '+ Add section');
    addBtn.addEventListener('click', () => {
      const heading = addCustom.value.trim() || addSelect.value;
      if (!heading) return;
      draft.sections.push({ heading, body: '' });
      addCustom.value = '';
      renderIssueEditorPane();
    });
    addRow.appendChild(addSelect);
    addRow.appendChild(addCustom);
    addRow.appendChild(addBtn);
    if (available.length || true) editor.appendChild(addRow);

    // ---- footer ----
    const footer = h('div', { class: 'issue-editor-footer' });
    const saveBtn = h('button', { class: 'issue-save-btn', type: 'button' }, draft.id ? 'Save' : 'Create issue');
    saveBtn.disabled = state.issueSaving;
    saveBtn.addEventListener('click', () => saveIssue(saveBtn));
    footer.appendChild(saveBtn);
    const statusEl = h('span', { class: 'issue-save-status' + (state.issueSaveError ? ' is-error' : '') },
      state.issueSaveError || '');
    footer.appendChild(statusEl);
    editor.appendChild(footer);

    pane.appendChild(editor);
  }

  function buildSectionEditor(section, idx) {
    const box = h('div', { class: 'issue-section' });
    const header = h('div', { class: 'section-header' });
    const titleInput = h('input', { class: 'section-title-input', type: 'text', value: section.heading });
    titleInput.addEventListener('input', () => { section.heading = titleInput.value; });
    header.appendChild(titleInput);
    const removeBtn = h('button', { class: 'remove-section-btn', type: 'button', title: 'Remove section' }, 'Remove');
    removeBtn.addEventListener('click', () => {
      state.issueDraft.sections.splice(idx, 1);
      renderIssueEditorPane();
    });
    header.appendChild(removeBtn);
    box.appendChild(header);

    const toolbar = h('div', { class: 'md-toolbar' });
    const textarea = h('textarea', { class: 'section-textarea' });
    textarea.value = section.body;
    textarea.addEventListener('input', () => { section.body = textarea.value; });

    function wrapSelection(before, after) {
      const start = textarea.selectionStart, end = textarea.selectionEnd;
      const val = textarea.value;
      const selected = val.slice(start, end);
      const next = val.slice(0, start) + before + selected + (after == null ? before : after) + val.slice(end);
      textarea.value = next;
      section.body = next;
      const cursor = start + before.length + selected.length + (after == null ? before.length : after.length);
      textarea.focus();
      textarea.setSelectionRange(start + before.length, start + before.length + selected.length || cursor);
    }
    function prefixLines(prefix) {
      const start = textarea.selectionStart, end = textarea.selectionEnd;
      const val = textarea.value;
      const lineStart = val.lastIndexOf('\n', start - 1) + 1;
      const before = val.slice(0, lineStart);
      const target = val.slice(lineStart, end);
      const replaced = target.split('\n').map((line) => prefix + line).join('\n');
      const next = before + replaced + val.slice(end);
      textarea.value = next;
      section.body = next;
      textarea.focus();
    }

    const btnBold = h('button', { type: 'button', title: 'Bold' }, 'B');
    btnBold.addEventListener('click', () => wrapSelection('**'));
    const btnItalic = h('button', { type: 'button', title: 'Italic' }, 'I');
    btnItalic.addEventListener('click', () => wrapSelection('*'));
    const btnCode = h('button', { type: 'button', title: 'Inline code' }, '`');
    btnCode.addEventListener('click', () => wrapSelection('`'));
    const btnLink = h('button', { type: 'button', title: 'Link' }, 'Link');
    btnLink.addEventListener('click', () => wrapSelection('[', '](url)'));
    const btnList = h('button', { type: 'button', title: 'Bullet list' }, '• List');
    btnList.addEventListener('click', () => prefixLines('- '));
    const btnHeading = h('button', { type: 'button', title: 'Heading' }, 'H');
    btnHeading.addEventListener('click', () => prefixLines('## '));
    for (const b of [btnBold, btnItalic, btnCode, btnLink, btnList, btnHeading]) toolbar.appendChild(b);

    const previewToggle = h('button', { class: 'preview-toggle' + (state.sectionPreview.has(idx) ? ' is-active' : ''), type: 'button' },
      state.sectionPreview.has(idx) ? 'Editing' : 'Preview');
    previewToggle.addEventListener('click', () => {
      if (state.sectionPreview.has(idx)) state.sectionPreview.delete(idx);
      else state.sectionPreview.add(idx);
      renderIssueEditorPane();
    });
    toolbar.appendChild(previewToggle);

    box.appendChild(toolbar);
    if (state.sectionPreview.has(idx)) {
      const preview = h('div', { class: 'section-preview' });
      preview.appendChild(renderMarkdown(section.body));
      box.appendChild(preview);
    } else {
      box.appendChild(textarea);
    }
    return box;
  }

  async function saveIssue(btn) {
    const draft = state.issueDraft;
    if (!draft.title.trim()) {
      state.issueSaveError = 'A title is required.';
      renderIssueEditorPane();
      return;
    }
    state.issueSaving = true;
    state.issueSaveError = null;
    btn.disabled = true;

    const frontmatter = Object.assign({}, draft.extraFrontmatter, {
      title: draft.title,
      status: draft.status,
      type: draft.type,
      area: draft.area,
      related: draft.related,
    });
    const sections = draft.sections.map((s) => ({ heading: s.heading, body: s.body }));

    try {
      let data;
      if (draft.id) {
        data = await api(apiUrl(state.project.id, `/issues/${encodeURIComponent(draft.id)}`), {
          method: 'PUT', jsonBody: { frontmatter, sections },
        });
      } else {
        data = await api(apiUrl(state.project.id, '/issues'), {
          method: 'POST', jsonBody: { frontmatter, sections },
        });
      }
      state.currentIssue = data.issue;
      state.issueDraft = cloneIssueForEdit(data.issue);
      markIssueDraftPristine();
      urlState.issue = data.issue.id;
      syncUrl();
      // Refresh the list so the new/edited issue shows up with current data.
      const listData = await api(apiUrl(state.project.id, '/issues'));
      state.issuesList = listData.issues || [];
      state.issuesSchema = listData.schema || state.issuesSchema;
      const listEl = tabPanels.issues.querySelector('.issues-list');
      if (listEl) renderIssuesList(listEl);
      notify('Saved.', 'info');
    } catch (e) {
      state.issueSaveError = e.detail || e.message || 'Could not save.';
    } finally {
      state.issueSaving = false;
      btn.disabled = false;
      renderIssueEditorPane();
    }
  }

  // ---- markdown-lite renderer for issue body preview -------------------
  // Escapes HTML first — an issue body is a file on disk, never trusted as
  // markup — then builds up block and inline structure over the escaped
  // text. Covers headings, bold/italic/inline code, fenced code blocks,
  // bullet/numbered lists (one level of nesting via leading indentation),
  // links, blockquotes and simple pipe tables.

  function renderInlineMd(text) {
    let s = escapeHtml(text);
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const safe = /^(https?:|mailto:|#|\/)/i.test(url) ? url : '#';
      return `<a href="${escapeHtml(safe)}" rel="noopener noreferrer">${label}</a>`;
    });
    return s;
  }

  function renderMarkdown(text) {
    const root = h('div', { class: 'md-body' });
    const lines = String(text == null ? '' : text).replace(/\r\n/g, '\n').split('\n');
    const total = lines.length;
    let i = 0;
    let html = '';

    function isListLine(line) {
      return /^(\s*)([-*])\s+/.test(line) || /^(\s*)(\d+)\.\s+/.test(line);
    }

    while (i < total) {
      const line = lines[i];

      if (/^```/.test(line)) {
        i++;
        const codeLines = [];
        while (i < total && !/^```/.test(lines[i])) { codeLines.push(lines[i]); i++; }
        if (i < total) i++;
        html += `<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`;
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        const level = Math.min(heading[1].length, 6);
        html += `<h${level}>${renderInlineMd(heading[2])}</h${level}>`;
        i++;
        continue;
      }

      if (/^\s*>\s?/.test(line)) {
        const quoteLines = [];
        while (i < total && /^\s*>\s?/.test(lines[i])) { quoteLines.push(lines[i].replace(/^\s*>\s?/, '')); i++; }
        html += `<blockquote>${renderInlineMd(quoteLines.join(' '))}</blockquote>`;
        continue;
      }

      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < total && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
        const headerCells = line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
        i += 2;
        const rows = [];
        while (i < total && /^\s*\|.*\|\s*$/.test(lines[i])) {
          rows.push(lines[i].trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
          i++;
        }
        html += '<table><thead><tr>' + headerCells.map((c) => `<th>${renderInlineMd(c)}</th>`).join('') + '</tr></thead>';
        html += '<tbody>' + rows.map((r) => '<tr>' + r.map((c) => `<td>${renderInlineMd(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>';
        continue;
      }

      if (isListLine(line)) {
        // Consume a run of list lines, tracking indentation for one level
        // of nesting. Ordered vs. unordered is decided per top-level run.
        const ordered = /^(\s*)(\d+)\.\s+/.test(line);
        const tag = ordered ? 'ol' : 'ul';
        let out = `<${tag}>`;
        let openNested = false;
        while (i < total && isListLine(lines[i])) {
          const m = lines[i].match(/^(\s*)(?:[-*]|\d+\.)\s+(.*)$/);
          const indent = m[1].length;
          const content = renderInlineMd(m[2]);
          if (indent >= 2 && !openNested) {
            out += `<${tag}>`;
            openNested = true;
          } else if (indent < 2 && openNested) {
            out += `</${tag}>`;
            openNested = false;
          }
          out += `<li>${content}</li>`;
          i++;
        }
        if (openNested) out += `</${tag}>`;
        out += `</${tag}>`;
        html += out;
        continue;
      }

      if (line.trim() === '') { i++; continue; }

      // Paragraph: consume until a blank line or a new block starts.
      const paraLines = [line];
      i++;
      while (i < total && lines[i].trim() !== '' && !isListLine(lines[i]) && !/^```/.test(lines[i]) &&
             !/^#{1,6}\s+/.test(lines[i]) && !/^\s*>\s?/.test(lines[i])) {
        paraLines.push(lines[i]);
        i++;
      }
      html += `<p>${renderInlineMd(paraLines.join('\n'))}</p>`;
    }

    root.innerHTML = html;
    return root;
  }

  // =======================================================================
  // GIT TAB
  // =======================================================================

  async function renderGitTab() {
    const panel = tabPanels.git;
    clear(panel);
    panel.appendChild(h('div', { class: 'empty-note' }, 'Loading…'));

    let git;
    try {
      git = await api(apiUrl(state.project.id, '/git'));
    } catch (e) {
      clear(panel);
      panel.appendChild(h('div', { class: 'empty-note' }, `Could not load git status: ${e.detail || e.message}`));
      return;
    }
    state.git = git;
    state.gitChecked = new Set();
    state.gitError = null;

    if (!git.is_repo) {
      renderGitNotRepo(panel);
      return;
    }
    renderGitPanel(panel);
  }

  function renderGitNotRepo(panel) {
    clear(panel);
    const box = h('div', { class: 'git-panel' });
    box.appendChild(h('p', { style: 'margin-bottom:.8rem;color:var(--text-muted);' },
      'This folder is not a git repository yet.'));
    const initBtn = h('button', { class: 'git-init-btn', type: 'button' }, 'git init');
    initBtn.addEventListener('click', async () => {
      initBtn.disabled = true;
      try {
        await api(apiUrl(state.project.id, '/git/init'), { method: 'POST' });
        renderGitTab();
      } catch (e) {
        reportUnexpected(e, 'git init failed');
        initBtn.disabled = false;
      }
    });
    box.appendChild(initBtn);
    panel.appendChild(box);
  }

  function renderGitPanel(panel) {
    clear(panel);
    const git = state.git;
    const box = h('div', { class: 'git-panel' });

    const summary = h('div', { class: 'git-summary-row' });
    function summaryItem(label, value) {
      return h('div', { class: 'git-summary-item' }, [
        h('span', { class: 'git-summary-label' }, label),
        h('span', { class: 'git-summary-value' }, value),
      ]);
    }
    summary.appendChild(summaryItem('Branch', git.detached ? `${git.branch} (detached)` : (git.branch || '(unborn)')));
    if (git.upstream) {
      summary.appendChild(summaryItem('Upstream', `${git.upstream} (${git.ahead} ahead / ${git.behind} behind)`));
    }
    if (git.last_commit) {
      summary.appendChild(summaryItem('Last commit', `${git.last_commit.hash.slice(0, 8)} ${git.last_commit.subject}`));
    }
    box.appendChild(summary);

    // ---- changes + commit ----
    const changesSection = h('div', { class: 'git-section' });
    changesSection.appendChild(h('h2', {}, 'Changes'));
    if ((git.changes || []).length === 0) {
      changesSection.appendChild(h('div', { class: 'empty-note' }, 'Working tree clean.'));
    } else {
      const selectAllRow = h('div', { class: 'select-all-row' });
      const selectAllCb = h('input', { type: 'checkbox' });
      selectAllCb.addEventListener('change', () => {
        state.gitChecked = selectAllCb.checked ? new Set(git.changes.map((c) => c.path)) : new Set();
        renderGitPanel(panel);
      });
      selectAllRow.appendChild(selectAllCb);
      selectAllRow.appendChild(h('span', {}, 'select all'));
      changesSection.appendChild(selectAllRow);

      const list = h('div', { class: 'changes-list' });
      for (const c of git.changes) {
        const row = h('div', { class: 'change-row' + (c.untracked ? ' is-untracked' : '') });
        const cb = h('input', { type: 'checkbox' });
        cb.checked = state.gitChecked.has(c.path);
        cb.addEventListener('change', () => {
          if (cb.checked) state.gitChecked.add(c.path);
          else state.gitChecked.delete(c.path);
        });
        row.appendChild(cb);
        row.appendChild(h('span', { class: 'change-status' }, `${c.index}${c.worktree}`));
        row.appendChild(h('span', { class: 'change-path' }, c.path));
        list.appendChild(row);
      }
      changesSection.appendChild(list);
    }

    const msgTextarea = h('textarea', { class: 'commit-message-input', placeholder: 'Commit message' });
    msgTextarea.value = state.commitMessage;
    msgTextarea.addEventListener('input', () => { state.commitMessage = msgTextarea.value; });
    changesSection.appendChild(msgTextarea);

    const commitRow = h('div', { class: 'commit-row' });
    const commitBtn = h('button', { class: 'git-action-btn', type: 'button' }, 'Commit');
    const hasIdentity = !!git.user;
    commitBtn.disabled = !hasIdentity || state.gitBusy;
    commitBtn.addEventListener('click', () => doCommit(panel));
    commitRow.appendChild(commitBtn);
    if (!hasIdentity) {
      commitRow.appendChild(h('span', { class: 'commit-disabled-note' },
        'Commit is disabled: this repository has no commit identity configured (git config user.name / user.email).'));
    }
    changesSection.appendChild(commitRow);

    const allTrackedNote = h('div', { style: 'font-size:.76rem;color:var(--text-faint);margin-top:.4rem;' },
      'Checked paths are committed exactly. With nothing checked, Commit commits all tracked modifications (git commit -a); untracked files must be checked explicitly.');
    changesSection.appendChild(allTrackedNote);

    if (state.gitError) {
      changesSection.appendChild(h('div', { class: 'git-error-box' }, state.gitError));
    }

    box.appendChild(changesSection);

    // ---- tags ----
    const tagSection = h('div', { class: 'git-section' });
    tagSection.appendChild(h('h2', {}, 'Tags'));
    const tagNameInput = h('input', { class: 'tag-name-input', type: 'text', placeholder: 'tag name' });
    const tagMsgInput = h('input', { class: 'tag-message-input', type: 'text', placeholder: 'message (optional — annotated if given)' });
    const tagBtn = h('button', { class: 'git-action-btn', type: 'button' }, 'Create tag');
    tagBtn.addEventListener('click', async () => {
      const name = tagNameInput.value.trim();
      if (!name) return;
      tagBtn.disabled = true;
      try {
        await api(apiUrl(state.project.id, '/git/tag'), {
          method: 'POST', jsonBody: { name, message: tagMsgInput.value.trim() || null },
        });
        tagNameInput.value = '';
        tagMsgInput.value = '';
        renderGitTab();
      } catch (e) {
        reportUnexpected(e, 'Could not create tag');
      } finally {
        tagBtn.disabled = false;
      }
    });
    const tagRow = h('div', { class: 'tag-row' }, [tagNameInput, tagMsgInput, tagBtn]);
    tagSection.appendChild(tagRow);
    if ((git.tags || []).length) {
      tagSection.appendChild(h('div', { class: 'tag-list' }, git.tags.map((t) => h('span', { class: 'tag-pill' }, t))));
    }
    box.appendChild(tagSection);

    // ---- .gitignore ----
    const giSection = h('div', { class: 'git-section' });
    giSection.appendChild(h('h2', {}, '.gitignore'));
    const giTextarea = h('textarea', { class: 'gitignore-textarea' });
    giTextarea.value = '';
    giSection.appendChild(giTextarea);
    const giSaveBtn = h('button', { class: 'git-action-btn', type: 'button' }, 'Save .gitignore');
    giSaveBtn.addEventListener('click', async () => {
      giSaveBtn.disabled = true;
      try {
        await api(apiUrl(state.project.id, '/gitignore'), { method: 'PUT', jsonBody: { content: giTextarea.value } });
        notify('.gitignore saved.', 'info');
      } catch (e) {
        reportUnexpected(e, 'Could not save .gitignore');
      } finally {
        giSaveBtn.disabled = false;
      }
    });
    giSection.appendChild(giSaveBtn);
    box.appendChild(giSection);
    panel.appendChild(box);

    api(apiUrl(state.project.id, '/gitignore')).then((data) => {
      state.gitignore = data;
      giTextarea.value = data.content || '';
    }).catch((e) => reportUnexpected(e, 'Could not load .gitignore'));
  }

  async function doCommit(panel) {
    if (!state.commitMessage.trim()) {
      state.gitError = 'A commit message is required.';
      renderGitPanel(panel);
      return;
    }
    if (state.gitBusy) return;
    state.gitBusy = true;
    state.gitError = null;
    // Re-render before awaiting so the button disables now, not after the
    // request returns: without this a second click during the round trip
    // issued a duplicate commit, the one action here that is not idempotent.
    renderGitPanel(panel);
    const paths = state.gitChecked.size > 0 ? Array.from(state.gitChecked) : null;
    try {
      await api(apiUrl(state.project.id, '/git/commit'), {
        method: 'POST', jsonBody: { message: state.commitMessage, paths },
      });
      state.commitMessage = '';
      state.gitBusy = false;
      renderGitTab();
    } catch (e) {
      state.gitBusy = false;
      if (e.code === 'git_failed') {
        // Contract: show git's own stderr verbatim, not a friendlier lie.
        state.gitError = e.detail;
      } else {
        state.gitError = e.detail || e.message;
      }
      renderGitPanel(panel);
    }
  }

  // =======================================================================
  // SESSIONS TAB
  // =======================================================================

  function renderSessionsTab() {
    const panel = tabPanels.sessions;
    clear(panel);
    const box = h('div', { class: 'sessions-panel' });

    // The action that starts a session sits on the heading of the list it adds
    // to, the way the desktop sidebar puts it on its own Sessions heading.
    const startBtn = h('button', {
      class: 'btn-new-session',
      type: 'button',
      disabled: state.startingSession,
    }, state.startingSession ? 'Starting…' : 'New session');
    startBtn.addEventListener('click', startSession);
    box.appendChild(h('div', { class: 'sessions-heading-row' }, [
      h('h3', { class: 'sessions-heading' }, 'Running now'),
      startBtn,
    ]));
    if (!state.sessions.length) {
      box.appendChild(h('div', { class: 'empty-note' }, 'No sessions running in this project.'));
    }
    for (const s of state.sessions) {
      const card = h('a', { class: 'session-card', href: `/?session=${encodeURIComponent(s.id)}` });
      // The directory earns a line only when it is not the project root, the
      // same rule a history card follows. The status is on the stats line
      // already, so there is nothing else to put here.
      const where = (state.project && s.workdir && s.workdir !== state.project.path)
        ? s.workdir : null;
      const main = h('div', { class: 'session-card-main' }, [
        h('div', { class: 'session-card-name' }, s.name || s.id),
        where ? h('div', { class: 'session-card-sub' }, where) : null,
      ]);
      card.appendChild(main);
      const bits = [];
      if (typeof s.cost_usd === 'number') bits.push(`$${s.cost_usd.toFixed(4)}`);
      bits.push(s.status);
      card.appendChild(h('div', { class: 'session-card-stats' }, bits.join(' · ')));
      box.appendChild(card);
    }

    // Prior conversations from ~/.claude/projects (ISSUE-024). An entry that
    // already has a live session is skipped: it is one of the cards above, and
    // listing it twice would suggest there are two of it.
    const earlier = state.history.filter((entry) => !entry.session_id);
    box.appendChild(h('h3', { class: 'sessions-heading' }, 'Earlier conversations'));
    box.appendChild(h('div', { class: 'sessions-note' },
      'Every claude session run in this folder, including ones started outside Codinian. Resuming replays it and continues where it left off.'));
    if (!earlier.length) {
      box.appendChild(h('div', { class: 'empty-note' }, 'No session history for this folder.'));
    }
    for (const entry of earlier) {
      box.appendChild(historyCard(entry));
    }

    panel.appendChild(box);
  }

  async function startSession() {
    if (state.startingSession) return;
    state.startingSession = true;
    renderSessionsTab();
    let data;
    try {
      // No body: the server starts it in the project root under the repo's own
      // default permission mode, which is what this button promises.
      data = await api(apiUrl(state.project.id, '/sessions'), { method: 'POST', jsonBody: {} });
    } catch (e) {
      state.startingSession = false;
      reportUnexpected(e, 'Could not start a session');
      renderSessionsTab();
      return;
    }
    state.startingSession = false;
    const session = (data && data.session) || {};
    if (!session.id) {
      reportUnexpected(new Error('the server answered without a session'), 'Could not start a session');
      renderSessionsTab();
      return;
    }
    if (embedMode) {
      // Same reason as resuming: the pane cannot navigate back out of the
      // project, so say where the session went instead of leaving for it.
      notify(`Started "${session.name || session.id}". Open it from the sidebar.`, 'info');
      await loadProject(state.project.id);
      return;
    }
    location.href = `/?session=${encodeURIComponent(session.id)}`;
  }

  function historyCard(entry) {
    const card = h('div', { class: 'session-card is-history' });
    const sub = [fmtWhen(entry.mtime)];
    // The recorded directory is worth showing only when it is not the project
    // root -- otherwise it is the same path on every row.
    if (state.project && entry.cwd && entry.cwd !== state.project.path) sub.push(entry.cwd);
    card.appendChild(h('div', { class: 'session-card-main' }, [
      h('div', { class: 'session-card-name' }, entry.title),
      h('div', { class: 'session-card-sub' }, sub.join('  ·  ')),
    ]));

    const busy = state.resumingId === entry.sdk_session_id;
    const btn = h('button', {
      class: 'btn-resume',
      type: 'button',
      disabled: !!state.resumingId,
    }, busy ? 'Resuming…' : 'Resume');
    btn.addEventListener('click', () => resumeSession(entry));
    card.appendChild(btn);
    return card;
  }

  async function resumeSession(entry) {
    if (state.resumingId) return;
    state.resumingId = entry.sdk_session_id;
    renderSessionsTab();
    let data;
    try {
      data = await api(apiUrl(state.project.id, '/sessions'), {
        method: 'POST',
        jsonBody: { resume: entry.sdk_session_id },
      });
    } catch (e) {
      state.resumingId = null;
      reportUnexpected(e, 'Could not resume that session');
      renderSessionsTab();
      return;
    }
    state.resumingId = null;
    const session = (data && data.session) || {};
    if (!session.id) {
      reportUnexpected(new Error('the server answered without a session'), 'Could not resume that session');
      renderSessionsTab();
      return;
    }
    if (embedMode) {
      // The pane has no way back once it navigates away from the project, and
      // the desktop sidebar has the new session anyway, so say where it went
      // rather than replacing the workspace with it.
      notify(`Resumed as "${session.name || session.id}". Open it from the sidebar.`, 'info');
      await loadProject(state.project.id);
      return;
    }
    location.href = `/?session=${encodeURIComponent(session.id)}`;
  }

  // ---------------------------------------------------------------------
  // go
  // ---------------------------------------------------------------------

  boot();
})();
