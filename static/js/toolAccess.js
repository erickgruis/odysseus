// Interactive tool access level — the composer menu, not the enforcement.
//
// The four levels (Read Only / Ask / Auto / Full Access) are enforced by the
// server in src/tool_access.py. This module only picks one and displays it.
// The stored level lives on the session row; a new chat starts from the app
// default, and the first message carries the chosen level so the server can
// persist it as part of creating that session.

import uiModule from './ui.js?v=20260916largetoolscroll1';
import sessionModule from './sessions.js';

const LOCAL_LEVELS = ['read_only', 'ask', 'auto', 'full_access'];

// Mirrors src.tool_access.PRESETS so the menu still renders if the metadata
// request fails. The server copy wins whenever it is available.
const FALLBACK_PRESETS = [
  {
    id: 'read_only',
    label: 'Read Only',
    short: 'Inspect only',
    description: 'Read files and search. No writes, shell, or MCP tools.',
    risk: false,
  },
  {
    id: 'ask',
    label: 'Ask',
    short: 'Approve each change',
    description: 'Every write, command, or network action waits for your approval.',
    risk: false,
  },
  {
    id: 'auto',
    label: 'Auto',
    short: 'Workspace auto',
    description: 'Runs edits and commands without asking; external actions still ask.',
    risk: false,
  },
  {
    id: 'full_access',
    label: 'Full Access',
    short: 'No prompts',
    description: 'Runs every permitted action without asking. Use only in trusted work.',
    risk: true,
  },
];

const LEVEL_ICONS = {
  read_only:
    '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  ask:
    '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9.5 12h5"/><path d="M12 9.5v5"/>',
  auto:
    '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
  full_access:
    '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M12 8v5"/><path d="M12 16h.01"/>',
};

let _presets = FALLBACK_PRESETS.slice();
let _level = 'ask';
let _defaultLevel = 'ask';
let _admin = true;
let _delegated = false;
let _ready = false;
let _menuOpen = false;
let _sessionId = null;
let _requestToken = 0;
const _sessionCache = new Map();

function el(id) {
  return document.getElementById(id);
}

function currentSessionId() {
  try {
    return sessionModule.getCurrentSessionId ? sessionModule.getCurrentSessionId() : null;
  } catch (_) {
    return null;
  }
}

function presetFor(level) {
  return _presets.find((preset) => preset.id === level) || null;
}

function normalizeLevel(value) {
  const normalized = String(value || '').trim().toLowerCase().replace(/-/g, '_');
  return LOCAL_LEVELS.includes(normalized) ? normalized : '';
}

/** The level a message from this composer should carry. */
export function currentAccessLevel() {
  return normalizeLevel(_level) || _defaultLevel;
}

function canSelect(level) {
  if (level === 'full_access' && (!_admin || _delegated)) return false;
  return true;
}

function renderButton() {
  const button = el('access-level-btn');
  const label = el('access-level-label');
  const icon = el('access-level-icon');
  const preset = presetFor(currentAccessLevel());
  const level = currentAccessLevel();
  if (label) label.textContent = preset ? preset.label : 'Ask';
  if (icon && LEVEL_ICONS[level]) icon.innerHTML = LEVEL_ICONS[level];
  if (button) {
    button.dataset.level = level;
    button.classList.toggle('is-risk', level === 'full_access');
    button.classList.toggle('is-readonly', level === 'read_only');
    button.title = preset
      ? `${preset.label} — ${preset.description}`
      : 'Agent access level';
  }
  document.body.classList.toggle('tool-access-read-only', level === 'read_only');
  document.body.classList.toggle('tool-access-full', level === 'full_access');
}

function renderMenu() {
  const menu = el('access-level-menu');
  if (!menu) return;
  menu.innerHTML = '';
  _presets.forEach((preset) => {
    const selected = preset.id === currentAccessLevel();
    const disabled = !canSelect(preset.id);
    const option = document.createElement('button');
    option.type = 'button';
    option.className = 'access-level-option';
    option.setAttribute('role', 'menuitemradio');
    option.setAttribute('aria-checked', selected ? 'true' : 'false');
    option.dataset.level = preset.id;
    if (selected) option.classList.add('is-selected');
    if (preset.risk) option.classList.add('is-risk');
    if (disabled) {
      option.classList.add('is-disabled');
      option.disabled = true;
      option.title = 'Admins and interactive sessions only.';
    }
    const icon = LEVEL_ICONS[preset.id] || '';
    option.innerHTML = [
      `<span class="access-level-option-icon" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icon}</svg></span>`,
      '<span class="access-level-option-text">',
      `<span class="access-level-option-label">${uiModule.esc(preset.label)}`,
      preset.id === _defaultLevel ? '<span class="access-level-option-tag">Default</span>' : '',
      '</span>',
      `<span class="access-level-option-desc">${uiModule.esc(preset.short || preset.description || '')}</span>`,
      '</span>',
    ].join('');
    option.addEventListener('click', (event) => {
      event.preventDefault();
      option.blur();
      closeMenu();
      void selectLevel(preset.id);
    });
    option.addEventListener('mouseenter', () => updateHint(preset));
    option.addEventListener('focus', () => updateHint(preset));
    menu.appendChild(option);
  });

  const hint = document.createElement('div');
  hint.className = 'access-level-hint';
  hint.id = 'access-level-hint';
  menu.appendChild(hint);
  updateHint(presetFor(currentAccessLevel()));
}

function updateHint(preset) {
  const hint = el('access-level-hint');
  if (hint) hint.textContent = preset ? preset.description || '' : '';
}

function openMenu() {
  const menu = el('access-level-menu');
  const button = el('access-level-btn');
  if (!menu || !button) return;
  renderMenu();
  menu.classList.remove('hidden');
  menuOpenState(true);
  button.setAttribute('aria-expanded', 'true');
}

function closeMenu() {
  const menu = el('access-level-menu');
  const button = el('access-level-btn');
  if (menu) menu.classList.add('hidden');
  menuOpenState(false);
  if (button) button.setAttribute('aria-expanded', 'false');
}

function menuOpenState(open) {
  _menuOpen = !!open;
}

function toggleMenu() {
  if (_menuOpen) closeMenu();
  else openMenu();
}

async function selectLevel(level) {
  const next = normalizeLevel(level);
  if (!next) return;
  if (!canSelect(next)) return;
  const preset = presetFor(next);
  if (preset && preset.risk && next !== _level) {
    const confirmed = await uiModule.styledConfirm(
      'Full Access runs every permitted action without asking, including shell commands, file changes, and network requests. Only enable it for a chat where you trust what the agent will do.',
      {
        title: 'Enable Full Access?',
        confirmText: 'Enable Full Access',
        cancelText: 'Cancel',
        danger: true,
      },
    );
    if (!confirmed) return;
  }

  const previous = _level;
  _level = next;
  renderButton();

  const sessionId = _sessionId || currentSessionId();
  if (!sessionId) {
    // No session row yet. The first message carries this level and the chat
    // route persists it while it creates the session.
    _sessionCache.set(null, next);
    if (previous !== next) {
      uiModule.showToast(`${preset ? preset.label : 'Access level'} for this new chat`, 1600);
    }
    return;
  }

  try {
    const body = new FormData();
    body.append('level', next);
    const res = await fetch(`/api/session/${encodeURIComponent(sessionId)}/access-level`, {
      method: 'POST',
      credentials: 'same-origin',
      body,
    });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    const applied = normalizeLevel(data && data.access_level) || next;
    _level = applied;
    _sessionCache.set(sessionId, applied);
    renderButton();
    if (applied !== next) {
      uiModule.showToast(
        `Full Access is not available for this account; using ${(presetFor(applied) || {}).label || applied}.`,
        3200,
      );
    } else if (previous !== applied) {
      uiModule.showToast(`${(presetFor(applied) || {}).label || applied} access`, 1600);
    }
  } catch (_) {
    _level = previous;
    renderButton();
    uiModule.showToast('Could not change the access level', 2400);
  }
}

async function refreshForSession(sessionId, {force = false} = {}) {
  _requestToken += 1;
  const token = _requestToken;
  if (!sessionId) {
    _level = _sessionCache.get(null) || _defaultLevel;
    renderButton();
    return;
  }
  if (!force && _sessionCache.has(sessionId)) {
    _level = _sessionCache.get(sessionId);
    renderButton();
  }
  try {
    const res = await fetch(
      `/api/session/${encodeURIComponent(sessionId)}/access-level`,
      { credentials: 'same-origin' },
    );
    if (!res.ok) return;
    const data = await res.json();
    if (token !== _requestToken) return;
    const level = normalizeLevel(data && data.access_level);
    if (!level) return;
    _level = level;
    _sessionCache.set(sessionId, level);
    renderButton();
  } catch (_) {
    /* keep the cached/default value */
  }
}

async function loadMetadata() {
  try {
    const res = await fetch('/api/tool-access', { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    if (Array.isArray(data.presets) && data.presets.length) {
      _presets = data.presets.map((preset) => ({
        id: normalizeLevel(preset.id) || preset.id,
        label: preset.label || preset.id,
        short: preset.short || '',
        description: preset.description || '',
        risk: !!preset.risk,
      }));
    }
    _defaultLevel = normalizeLevel(data.default) || _defaultLevel;
    _admin = data.admin !== false;
    _delegated = !!data.delegated;
  } catch (_) {
    /* keep the fallback copy */
  }
}

export async function initToolAccess() {
  const button = el('access-level-btn');
  if (!button || _ready) return;
  _ready = true;

  _level = _defaultLevel;
  renderButton();

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
    const wrap = el('access-level-wrap');
    if (wrap && !wrap.contains(event.target)) closeMenu();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && _menuOpen) closeMenu();
  });

  window.addEventListener('odysseus:session-changed', (event) => {
    _sessionId = (event && event.detail && event.detail.sessionId) || null;
    void refreshForSession(_sessionId);
  });

  await loadMetadata();
  _sessionId = currentSessionId() || null;
  await refreshForSession(_sessionId);
  renderButton();
}
