import { api } from './api.js';
import { h, clear, mono } from './dom.js';

const routes = {
  start: () => import('./views/dashboard.js'),
  faecher: () => import('./views/browse.js'),
  eintrag: () => import('./views/entry.js'),
  suche: () => import('./views/search.js'),
  probe: () => import('./views/mock.js'),
  statistik: () => import('./views/stats.js'),
  quellen: () => import('./views/quellen.js'),
};

let cleanup = null;
let token = 0;

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  const segs = pathPart.split('/').filter(Boolean).map(decodeURIComponent);
  return { name: segs[0] || 'start', args: segs.slice(1), query: new URLSearchParams(queryPart) };
}

async function render() {
  const mine = ++token;
  const { name, args, query } = parseHash();
  const main = document.getElementById('inhalt');
  if (typeof cleanup === 'function') { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  const routeName = routes[name] ? name : 'start';
  for (const a of document.querySelectorAll('#nav a')) {
    const current = a.dataset.route === routeName || (routeName === 'eintrag' && a.dataset.route === 'faecher');
    if (current) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  clear(main).append(h('p', { class: 'empty', role: 'status' }, 'Lädt …'));
  try {
    const mod = await routes[routeName]();
    if (mine !== token) return;
    const view = h('div', {});
    const result = await mod.render(view, { args, query });
    if (mine !== token) { if (typeof result === 'function') result(); return; }
    cleanup = typeof result === 'function' ? result : null;
    clear(main).append(view);
    document.title = `${mod.title || 'Klausurwerk'} – Klausurwerk`;
  } catch (err) {
    if (mine !== token) return;
    clear(main).append(h('div', { class: 'note danger', role: 'alert' },
      h('strong', {}, 'Fehler. '), err.message || String(err)));
  }
}

export function navigate(hash) {
  if (location.hash === hash) render(); else location.hash = hash;
}

window.addEventListener('hashchange', render);
render();

// Ground toggle: the kit's two grounds, stored by ground.js, system preference as the fallback.
const ground = window.klausurwerkGround;
const toggle = document.getElementById('ground-toggle');
function labelToggle() {
  const next = ground.current() === 'ink' ? 'Heller Grund' : 'Dunkler Grund';
  toggle.setAttribute('aria-label', next);
  toggle.title = next;
}
toggle.addEventListener('click', () => ground.set(ground.current() === 'ink' ? 'paper' : 'ink'));
window.addEventListener('groundchange', labelToggle);
labelToggle();

api.meta().then((m) => {
  const el = document.getElementById('foot-meta');
  const c = m.counts;
  if (!m.indexed_at) { el.textContent = 'Kein Index. Erst den Indexer starten (README).'; return; }
  el.append('Index ', mono(new Date(m.indexed_at).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' })),
    ' · ', mono(String(c.entries)), ' Einträge · ', mono(String(c.files)), ' Dateien · ', mono(String(c.pages_indexed)), ' Seiten');
}).catch(() => { /* footer stays empty */ });
