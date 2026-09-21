import { api } from '../api.js';
import { h, card, field, select, mono, paperRow, rowList, pager, subjectCard, VERDICT_LABEL, LECTURER_CAVEAT } from '../dom.js';

export const title = 'Fächer';
const LIMIT = 50;
const SOURCE_KIND = { lecturer_map: 'hat gelesen', professor_map: 'Prüfer laut Fachschaft', sidecar: 'laut klausurwerk.json' };
const FIELDS = ['subject', 'kind', 'has_solution', 'dozent', 'sem_from', 'sem_to', 'verdict', 'status', 'sort', 'offset'];

export async function render(root, { query }) {
  const state = Object.fromEntries(FIELDS.map((f) => [f, query.get(f) || '']));
  const [subjects, lecturers, sems] = await Promise.all([api.subjects(), api.lecturers(), api.semesters(state.subject || null)]);

  const go = (patch) => {
    const next = { ...state, offset: '', ...patch };
    const p = new URLSearchParams();
    for (const f of FIELDS) if (next[f]) p.set(f, next[f]);
    location.hash = `#/faecher${p.toString() ? `?${p}` : ''}`;
  };

  const filtered = FIELDS.some((f) => f !== 'sort' && f !== 'offset' && state[f]);
  root.append(h('div', { class: 'page-head' },
    h('h1', {}, state.subject || 'Fächer'),
    !filtered ? h('span', { class: 'page-sub' }, mono(String(subjects.items.length))) : null,
    filtered ? h('div', { class: 'page-end' }, h('a', { class: 'btn ghost sm', href: '#/faecher' }, 'Zurücksetzen')) : null));

  const knownSems = sems.items.filter((s) => s.semester_known);
  const semOptions = (selected) => [h('option', { value: '' }, 'egal'),
    ...knownSems.map((s) => h('option', { value: s.semester, selected: s.semester === selected }, s.semester_label))];
  const pick = (id, label, options, key, hint) => field(id, label, select({ id, onchange: (e) => go({ [key]: e.target.value }) }, options), hint);
  const opt = (value, label, key) => h('option', { value, selected: state[key] === value }, label);

  root.append(h('form', { class: 'filters', 'aria-label': 'Filter', onsubmit: (e) => e.preventDefault() },
    pick('f-subject', 'Fach', [opt('', 'alle', 'subject'), ...subjects.items.map((s) => opt(s.subject, `${s.subject} (${s.total})`, 'subject'))], 'subject'),
    pick('f-kind', 'Art', [opt('', 'alle', 'kind'), opt('exam', 'Klausur', 'kind'), opt('exercise', 'Übung', 'kind'), opt('material', 'Material', 'kind')], 'kind'),
    pick('f-doz', 'Dozent', [opt('', 'alle', 'dozent'), ...lecturers.items.map((l) => opt(l.name, `${l.name} – ${SOURCE_KIND[l.source_kind] || l.source_kind}`, 'dozent'))], 'dozent'),
    pick('f-from', 'Von', semOptions(state.sem_from), 'sem_from'),
    pick('f-to', 'Bis', semOptions(state.sem_to), 'sem_to'),
    pick('f-verdict', 'Lesbarkeit', [opt('', 'alle', 'verdict'), ...['text', 'mixed', 'scan', 'no_text'].map((v) => opt(v, VERDICT_LABEL[v], 'verdict'))], 'verdict'),
    pick('f-status', 'Status', [opt('', 'alle', 'status'), opt('offen', 'offen', 'status'), opt('in_arbeit', 'in Arbeit', 'status'), opt('erledigt', 'erledigt', 'status')], 'status'),
    pick('f-sort', 'Sortierung', [opt('', 'neueste zuerst', 'sort'), opt('semester_asc', 'älteste zuerst', 'sort'), opt('title', 'Titel', 'sort'), opt('status', 'Status', 'sort')], 'sort'),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: state.has_solution === 'true', onchange: (e) => go({ has_solution: e.target.checked ? 'true' : '' }) }), 'mit Lösung')));

  if (!filtered) {
    root.append(h('div', { class: 'grid' }, subjects.items.map(subjectCard)));
    return;
  }

  const offset = Number(state.offset) || 0;
  const res = await api.entries({ ...state, sort: state.sort || 'semester_desc', limit: LIMIT, offset });
  const list = card({ title: 'Einträge', end: mono(String(res.total)), cls: 'list-card' });
  list.setAttribute('aria-live', 'polite');
  if (!res.items.length) list.append(h('p', { class: 'empty' }, 'Keine Einträge.'));
  else {
    list.append(rowList('full by-name', [['Semester'], ['Titel'], ['Art'], ['Lsg.'], ['Dozent'], ['Wert.', 'num'], ['Status']],
      res.items.map((item) => paperRow(item, { showSubject: !state.subject }))));
  }
  const pg = pager(res.total, res.limit, res.offset, (o) => go({ offset: o ? String(o) : '' }));
  if (pg) list.append(pg);
  root.append(list, h('p', { class: 'footnote' },
    `${LECTURER_CAVEAT}.`,
    state.sem_from || state.sem_to ? ' Ein Semesterbereich blendet „Semester unbekannt“ aus.' : null));
}
