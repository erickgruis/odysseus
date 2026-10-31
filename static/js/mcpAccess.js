// Per-chat MCP server picker — which servers this chat may offer.
//
// This is a *tool selection* control, not a permission level: the value only
// removes servers from the request's tool schema. The server enforces it in
// routes/chat_routes.py + src/agent_loop.py, and the access level
// (src/tool_access.py) still decides whether a selected tool may run.
//
// The selection lives on the session row. A brand-new chat has no row yet, so
// the first message carries the chosen list and the route persists it, exactly
// like the access level.

import uiModule from './ui.js?v=20260916largetoolscroll1';
import sessionModule from './sessions.js';

const el = (id) => document.getElementById(id);

let _servers = [];
let _disabled = new Set();
let _pendingDisabled = new Set();
let _sessionId = null;
let _menuOpen = false;
let _loaded = false;

function currentSessionId() {
  try {
    return sessionModule.getCurrentSessionId?.() || null;
  } catch (_) {
    return null;
  }
}

function isReadOnly() {
  const level = String(el('access-level-btn')?.dataset.level || '').toLowerCase();
  return level === 'read_only';
}

function enabledCount() {
  return _servers.filter((server) => !_disabled.has(server.id)).length;
}

function renderButton() {
  const button = el('mcp-access-btn');
  const count = el('mcp-access-count');
  const wrap = el('mcp-access-wrap');
  if (!button || !count || !wrap) return;
  const total = _servers.length;
  if (!total) {
    wrap.classList.add('hidden');
    return;
  }
  wrap.classList.remove('hidden');
  const active = enabledCount();
  count.textContent = active === total ? String(total) : `${active}/${total}`;
  button.classList.toggle('is-partial', active < total);
  button.classList.toggle('is-disabled', isReadOnly());
  button.title = isReadOnly()
    ? 'MCP tools are off in Read Only'
    : `MCP servers: ${active} of ${total} enabled for this chat`;
}

function renderMenu() {
  const menu = el('mcp-access-menu');
  if (!menu) return;
  menu.innerHTML = '';
  if (!_servers.length) {
    menu.classList.add('hidden');
    return;
  }

  const head = document.createElement('div');
  head.className = 'mcp-access-head';
  const title = document.createElement('span');
  title.className = 'mcp-access-title';
  title.textContent = 'MCP tools';
  head.appendChild(title);
  const all = document.createElement('button');
  all.type = 'button';
  all.className = 'mcp-access-head-btn';
  all.textContent = 'All';
  all.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    _disabled = new Set();
    commit();
  });
  const none = document.createElement('button');
  none.type = 'button';
  none.className = 'mcp-access-head-btn';
  none.textContent = 'None';
  none.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    _disabled = new Set(_servers.map((server) => server.id));
    commit();
  });
  head.appendChild(all);
  head.appendChild(none);
  menu.appendChild(head);

  _servers.forEach((server) => {
    const row = document.createElement('label');
    row.className = 'mcp-access-option';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !_disabled.has(server.id);
    box.disabled = isReadOnly();
    box.addEventListener('change', () => {
      if (box.checked) _disabled.delete(server.id);
      else _disabled.add(server.id);
      commit();
    });
    const name = document.createElement('span');
    name.className = 'mcp-access-option-name';
    name.textContent = server.name || server.id;
    const count = document.createElement('span');
    count.className = 'mcp-access-option-count';
    count.textContent = server.tool_count ? `${server.tool_count}` : '';
    row.appendChild(box);
    row.appendChild(name);
    row.appendChild(count);
    menu.appendChild(row);
  });

  const hint = document.createElement('div');
  hint.className = 'mcp-access-hint';
  hint.textContent = isReadOnly()
    ? 'Read Only hides every MCP tool, whatever this list says.'
    : 'Unchecked servers are not offered to the agent in this chat. The access level still applies.';
  menu.appendChild(hint);
}

function commit() {
  renderButton();
  if (_menuOpen) renderMenu();
  const sessionId = _sessionId || currentSessionId();
  if (!sessionId) {
    _pendingDisabled = new Set(_disabled);
    return;
  }
  const body = new FormData();
  body.append('disabled', JSON.stringify([..._disabled]));
  fetch(`/api/session/${encodeURIComponent(sessionId)}/mcp-servers`, {
    method: 'POST',
    credentials: 'same-origin',
    body,
  }).catch(() => {
    /* keep the local selection; the next refresh reconciles it */
  });
}

async function loadCatalog(sessionId) {
  if (sessionId) {
    const res = await fetch(
      `/api/session/${encodeURIComponent(sessionId)}/mcp-servers`,
      { credentials: 'same-origin' },
    );
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    _servers = Array.isArray(data.servers) ? data.servers : [];
    const disabled = Array.isArray(data.disabled) ? data.disabled : [];
    _disabled = new Set(disabled);
    _pendingDisabled = new Set();
    return;
  }
  // No session yet: the catalog endpoint already filtered to servers whose
  // tools reach the function schema, and carries no server configuration.
  const res = await fetch('/api/mcp/chat-servers', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(String(res.status));
  const data = await res.json();
  const list = Array.isArray(data) ? data : (data.servers || []);
  _servers = list.map((server) => ({
    id: server.id,
    name: server.name || server.id,
    tool_count: Number(server.tool_count || 0),
  }));
  _disabled = new Set(_pendingDisabled);
}

let _retryCount = 0;

async function refresh() {
  _sessionId = currentSessionId();
  try {
    await loadCatalog(_sessionId);
    _loaded = true;
  } catch (_) {
    _servers = [];
    _disabled = new Set();
  }
  renderButton();
  if (_menuOpen) renderMenu();
  // MCP servers connect a few seconds after boot, so a first refresh can see
  // an empty catalog. Re-check a few times before giving up.
  if (!_servers.length && _retryCount < 3) {
    _retryCount += 1;
    setTimeout(() => void refresh(), 5000);
  } else if (_servers.length) {
    _retryCount = 0;
  }
}

function openMenu() {
  if (!_servers.length) return;
  renderMenu();
  el('mcp-access-menu')?.classList.remove('hidden');
  el('mcp-access-btn')?.setAttribute('aria-expanded', 'true');
  _menuOpen = true;
}

function closeMenu() {
  el('mcp-access-menu')?.classList.add('hidden');
  el('mcp-access-btn')?.setAttribute('aria-expanded', 'false');
  _menuOpen = false;
}

function toggleMenu() {
  if (isReadOnly()) return;
  if (_menuOpen) closeMenu();
  else openMenu();
}

/** Server ids this chat has opted out of (sent with the first message). */
export function disabledServers() {
  const sessionId = _sessionId || currentSessionId();
  if (!sessionId) return [..._pendingDisabled];
  return [..._disabled];
}

export function initMcpAccess() {
  const button = el('mcp-access-btn');
  const wrap = el('mcp-access-wrap');
  if (!button || !wrap) return;

  button.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    toggleMenu();
  });
  button.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openMenu();
    } else if (event.key === 'Escape') {
      closeMenu();
    }
  });
  document.addEventListener('click', (event) => {
    if (!_menuOpen) return;
    if (!wrap.contains(event.target)) closeMenu();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && _menuOpen) closeMenu();
  });
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target && target.closest && target.closest('#access-level-menu')) {
      // Read Only can be chosen from the sibling menu; mirror its dimming.
      setTimeout(renderButton, 0);
    }
  });

  window.addEventListener('odysseus:session-changed', (event) => {
    _sessionId = (event && event.detail && event.detail.sessionId) || null;
    closeMenu();
    void refresh();
  });

  void refresh();
}

export function refreshMcpAccess() {
  if (!_loaded) return;
  void refresh();
}
