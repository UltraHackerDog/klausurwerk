import { api } from '../api.js';
import { h, card, select, tag, mono, semester, entryHref, pager, rowList } from '../dom.js';

export const title = 'Suche';
const LIMIT = 20;
// The server brackets each hit with these two control characters (SOH / STX).
const HIT_START = String.fromCharCode(1);
const HIT_END = String.fromCharCode(2);

function snippetNodes(text) {
  // Everything is inserted as text nodes; only <mark> elements are created here.
  const out = [];
  for (const part of text.split(HIT_START)) {
    if (part.includes(HIT_END)) {
      const cut = part.indexOf(HIT_END);
      out.push(h('mark', {}, part.slice(0, cut)));
      out.push(part.slice(cut + 1));
    } else {
      out.push(part);
    }
  }
  return out;
}

export async function render(root, { query }) {
  const q = query.get('q') || '';
  const subject = query.get('subject') || '';
  const sol = query.get('sol') !== '0';
  const offset = Number(query.get('offset')) || 0;
  const subjects = await api.subjects();

  const go = (patch) => {
    const next = { q, subject, sol: sol ? '' : '0', offset: '', ...patch };
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(next)) if (v) p.set(k, v);
    location.hash = `#/suche?${p}`;
  };

  const input = h('input', { id: 's-q', class: 'input lg', type: 'search', value: q, placeholder: 'Suchwörter', 'aria-label': 'Suchwörter', autocomplete: 'off' });
  const subjWrap = select({ id: 's-subject', 'aria-label': 'Fach' }, [h('option', { value: '' }, 'alle Fächer'),
    ...subjects.items.map((s) => h('option', { value: s.subject, selected: s.subject === subject }, s.subject))]);
  const subj = subjWrap.querySelector('select');
  const solBox = h('input', { type: 'checkbox', checked: sol });

  root.append(h('div', { class: 'page-head' }, h('h1', {}, 'Suche')),
    h('form', { role: 'search', onsubmit: (e) => { e.preventDefault(); go({ q: input.value.trim(), subject: subj.value, sol: solBox.checked ? '' : '0' }); } },
      h('div', { class: 'searchbar' }, input, h('button', { type: 'submit', class: 'btn primary lg' }, 'Suchen')),
      h('div', { class: 'quiet-row' }, subjWrap, h('label', { class: 'check' }, solBox, 'auch Lösungen'),
        h('span', { class: 'hint' }, 'Alle Wörter auf einer Seite. Scans nur mit Texterkennung.'))));

  if (!q) { queueMicrotask(() => input.focus()); return; }

  const res = await api.search({ q, subject, include_solutions: sol ? 'true' : 'false', limit: LIMIT, offset });
  const list = card({ title: 'Seiten', end: mono(String(res.total)) });
  list.setAttribute('aria-live', 'polite');
  if (!res.items.length) list.append(h('p', { class: 'empty' }, 'Nichts gefunden.'));
  else {
    list.append(rowList('hits', [['Semester'], ['Titel'], ['Teil'], ['Seite', 'num']],
      res.items.map((r) => h('li', {},
        h('a', { class: 'row', href: entryHref(r.entry_id, { role: r.role, page: r.page_no }) },
          semester(r, { short: true }),
          h('span', { class: 'title', title: r.title }, r.title, h('span', { class: 'sub' }, r.subject)),
          h('span', { class: 'row-meta' }, tag(r.role === 'loesung' ? 'Lösung' : 'Angabe')),
          h('span', { class: 'page num mono', title: `Seite ${r.page_no}` }, `S. ${r.page_no}`),
          h('span', { class: 'snippet' }, snippetNodes(r.snippet)))))));
  }
  const pg = pager(res.total, res.limit, res.offset, (o) => go({ offset: o ? String(o) : '' }));
  if (pg) list.append(pg);
  root.append(list);
}
