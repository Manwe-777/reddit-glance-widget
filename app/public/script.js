// Reddit widget frontend.
// Reads from the local server cache (/api/posts) — never hits Reddit directly,
// so refreshing here is cheap and does NOT affect Reddit throttling. The server
// decides when to actually refetch from Reddit (every couple of hours).

const feedEl = document.getElementById('feed');
const loadingEl = document.getElementById('loading');
const errorEl = document.getElementById('error');
const footerEl = document.getElementById('footer');
const updatedEl = document.getElementById('updated');
const refreshBtn = document.getElementById('refresh');

const settingsToggle = document.getElementById('settings-toggle');
const settingsPanel = document.getElementById('settings-panel');
const closeSettingsBtn = document.getElementById('close-settings');
const groupBySubEl = document.getElementById('group-by-sub');
const maxPostsEl = document.getElementById('max-posts');
const subListEl = document.getElementById('sub-list');
const settingsNoteEl = document.getElementById('settings-note');

const SETTINGS_KEY = 'redditWidgetSettings';
const VISITED_KEY = 'redditWidgetVisited';
const REFRESH_MS = 60000; // re-read local cache once a minute

const defaultSettings = { groupBySub: false, maxPosts: 25, hiddenSubs: [] };

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    if (saved) return { ...defaultSettings, ...saved };
  } catch (e) {}
  return { ...defaultSettings };
}
function saveSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) {}
}

function loadVisited() {
  try { return new Set(JSON.parse(localStorage.getItem(VISITED_KEY)) || []); }
  catch (e) { return new Set(); }
}
function markVisited(id) {
  visited.add(id);
  try {
    // Keep the visited set from growing unbounded.
    const arr = [...visited].slice(-500);
    localStorage.setItem(VISITED_KEY, JSON.stringify(arr));
  } catch (e) {}
}

let settings = loadSettings();
let visited = loadVisited();
let lastData = null;

// --- helpers ---------------------------------------------------------------
function escapeHtml(str) {
  if (str == null) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function timeAgo(epochSeconds) {
  if (!epochSeconds) return '';
  let s = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
  const m = Math.floor(s / 60);
  if (m < 1) return 'just now';
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  const d = Math.floor(h / 24);
  if (d < 30) return d + 'd ago';
  const mo = Math.floor(d / 30);
  if (mo < 12) return mo + 'mo ago';
  return Math.floor(d / 365) + 'y ago';
}

function fmtScore(n) {
  n = n || 0;
  if (n >= 10000) return (n / 1000).toFixed(1) + 'k';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

// --- rendering -------------------------------------------------------------
function postHtml(p, showSub) {
  const isVisited = visited.has(p.id) ? ' visited' : '';
  const domain = (!p.is_self && p.domain)
    ? ` <span class="post-domain">(${escapeHtml(p.domain)})</span>` : '';
  const meta = [];
  meta.push(`<span class="score">${fmtScore(p.score)}</span>`);
  meta.push(`<span class="dot">·</span><span>${p.comments} comments</span>`);
  if (showSub) meta.push(`<span class="dot">·</span><span class="sub">r/${escapeHtml(p.subreddit)}</span>`);
  if (p.flair) meta.push(`<span class="flair">${escapeHtml(p.flair)}</span>`);
  const when = timeAgo(p.created_utc);
  if (when) meta.push(`<span class="dot">·</span><span>${when}</span>`);

  return `<a class="post${isVisited}" href="${escapeHtml(p.permalink)}" target="_blank" rel="noopener"
      data-id="${escapeHtml(p.id)}" title="${escapeHtml(p.title)}">
      <span class="post-title">${escapeHtml(p.title)}${domain}</span>
      <span class="post-meta">${meta.join('')}</span>
    </a>`;
}

function render() {
  if (!lastData) return;
  const hidden = new Set(settings.hiddenSubs);
  let posts = (lastData.posts || []).filter(p => !hidden.has(p.subreddit));

  let html = '';
  if (settings.groupBySub) {
    // Group in the server's configured subreddit order.
    const order = (lastData.subreddits || []).filter(s => !hidden.has(s));
    const bySub = {};
    posts.forEach(p => { (bySub[p.subreddit] = bySub[p.subreddit] || []).push(p); });
    let shown = 0;
    for (const sub of order) {
      const group = bySub[sub];
      if (!group || !group.length) continue;
      html += `<div class="group-header">r/${escapeHtml(sub)}</div>`;
      for (const p of group) {
        if (shown >= settings.maxPosts) break;
        html += postHtml(p, false);
        shown++;
      }
      if (shown >= settings.maxPosts) break;
    }
  } else {
    posts = posts.slice(0, settings.maxPosts);
    html = posts.map(p => postHtml(p, true)).join('');
  }

  feedEl.innerHTML = html || '';
  if (!html) {
    feedEl.innerHTML = `<div class="loading">No posts to show.</div>`;
  }

  // Footer: newest fetch time across visible subreddits.
  const meta = lastData.meta || {};
  let newest = 0, anyError = null;
  Object.keys(meta).forEach(s => {
    if (hidden.has(s)) return;
    if (meta[s].updated_at && meta[s].updated_at > newest) newest = meta[s].updated_at;
    if (meta[s].error) anyError = meta[s].error;
  });
  footerEl.classList.remove('hidden');
  updatedEl.textContent = newest
    ? 'updated ' + timeAgo(newest)
    : (anyError ? 'waiting for data…' : '');

  // Wire visited tracking on the freshly rendered links.
  feedEl.querySelectorAll('.post').forEach(a => {
    a.addEventListener('click', () => {
      markVisited(a.dataset.id);
      a.classList.add('visited');
    });
  });
}

function renderSubToggles() {
  const subs = (lastData && lastData.subreddits) || [];
  subListEl.innerHTML = subs.map(s => {
    const checked = settings.hiddenSubs.includes(s) ? '' : 'checked';
    return `<label class="checkbox-label">
      <input type="checkbox" data-sub="${escapeHtml(s)}" ${checked}>
      <span>r/${escapeHtml(s)}</span>
    </label>`;
  }).join('');
  subListEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const sub = cb.dataset.sub;
      if (cb.checked) settings.hiddenSubs = settings.hiddenSubs.filter(s => s !== sub);
      else if (!settings.hiddenSubs.includes(sub)) settings.hiddenSubs.push(sub);
      saveSettings();
      render();
    });
  });
  settingsNoteEl.textContent = subs.length
    ? 'Subreddits are configured on the server (SUBREDDITS).'
    : '';
}

// --- data ------------------------------------------------------------------
async function load(spin) {
  if (spin) refreshBtn.classList.add('spin');
  try {
    const res = await fetch('/api/posts', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    lastData = await res.json();
    loadingEl.classList.add('hidden');
    errorEl.classList.add('hidden');
    renderSubToggles();
    render();
  } catch (err) {
    loadingEl.classList.add('hidden');
    if (!lastData) {
      errorEl.textContent = 'Failed to load posts';
      errorEl.classList.remove('hidden');
    }
  } finally {
    if (spin) setTimeout(() => refreshBtn.classList.remove('spin'), 350);
  }
}

// --- settings wiring -------------------------------------------------------
settingsToggle.addEventListener('click', () => settingsPanel.classList.toggle('hidden'));
closeSettingsBtn.addEventListener('click', () => settingsPanel.classList.add('hidden'));

groupBySubEl.addEventListener('change', () => {
  settings.groupBySub = groupBySubEl.checked;
  saveSettings();
  render();
});
maxPostsEl.addEventListener('change', () => {
  settings.maxPosts = Math.max(1, parseInt(maxPostsEl.value) || 25);
  saveSettings();
  render();
});
refreshBtn.addEventListener('click', () => load(true));

// --- init ------------------------------------------------------------------
function applySettingsToUI() {
  groupBySubEl.checked = settings.groupBySub;
  maxPostsEl.value = settings.maxPosts;
}

applySettingsToUI();
load(false);
setInterval(() => load(false), REFRESH_MS);
