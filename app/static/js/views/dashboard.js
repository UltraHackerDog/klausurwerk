import { api } from '../api.js';
import { h, add, clear, card, meter, mono, compactRow, paperRow, rowList, reasonLegend, tabs, subjectCard, LECTURER_CAVEAT } from '../dom.js';

export const title = 'Module';
const IN_CARD = 4;
const RANKED = 25;

export async function render(root) {
  const [data, meta] = await Promise.all([api.dashboard({ limit: RANKED }), api.meta()]);
  const labels = meta.labels;
  if (!data.modules.length) { overview(root, data, meta); return; }
  root.append(h('div', { class: 'page-head' }, h('h1', {}, 'Meine Module')));
  root.append(h('div', { class: 'grid modules' }, data.modules.map((m) => moduleCard(m, labels))));
  root.append(ranking(data, labels));
}

/** No modules configured: every subject as a card with its progress. */
function overview(root, data, meta) {
  root.append(h('div', { class: 'page-head' }, h('h1', {}, 'Alle Fächer'),
    h('span', { class: 'page-sub' }, mono(String(data.subjects_total)))));
  if (!data.subjects.length) {
    root.append(h('p', { class: 'empty' }, meta.indexed_at ? 'Keine Einträge im Index.' : 'Kein Index. Erst den Indexer starten (README).'));
    return;
  }
  root.append(h('div', { class: 'grid' }, data.subjects.map(subjectCard)));
  const notes = [];
  if (data.subjects_total > data.subjects.length) notes.push(h('a', { class: 'link', href: '#/faecher' }, 'Alle Fächer'));
  if (meta.scan && meta.scan.cap_hit) notes.push(`Scan bei ${meta.scan.max_files} Dateien abgebrochen – Katalog unvollständig.`);
  if (meta.scan && meta.scan.sidecars_rejected.length) notes.push(`${meta.scan.sidecars_rejected.length} klausurwerk.json nicht lesbar – siehe Indexer-Ausgabe.`);
  notes.push('Eigene Module: my_modules in config.json.');
  root.append(h('p', { class: 'footnote' }, notes.map((n, i) => [i ? ' ' : null, n])));
}

function moduleCard(m, labels) {
  const t = m.totals;
  const names = m.lecturers.length ? m.lecturers.join(', ') : 'Dozent unbekannt';
  const browse = `#/faecher?subject=${encodeURIComponent(m.subject)}`;
  const c = card({ title: m.name, end: h('span', { title: 'erledigt / Einträge' }, mono(`${t.done}/${t.total}`)) },
    h('div', { class: 'card-body' },
      h('p', { class: 'lecturer-line', title: `Quelle: ${m.lecturers_source}` }, names),
      h('ul', { class: 'meters' },
        meter('Erledigt', t.done, t.total, { live: t.in_progress }),
        meter('Eigener Dozent', t.priority_done, t.priority_total))));

  if (t.total === 0) {
    c.append(h('p', { class: 'empty' }, `Kein Fach „${m.subject}“ im Index.`));
    return c;
  }
  c.append(
    h('div', { class: 'card-section-head' }, h('span', {}, 'Als Nächstes'),
      t.priority_total === 0 ? h('span', {}, 'eigener Dozent: keine Einträge') : null),
    rowList('compact', null, m.papers.slice(0, IN_CARD).map((p) => compactRow(p, labels))),
    h('div', { class: 'card-foot' }, h('a', { class: 'link', href: browse }, 'Alle Einträge')));
  return c;
}

/** The full ranked list, one module at a time. */
function ranking(data, labels) {
  const state = { subject: data.modules[0].subject };
  const bar = h('div', { class: 'tabbar' });
  const body = h('div', { class: 'card-flush', role: 'tabpanel' });
  const foot = h('div', { class: 'card-foot' });

  function draw(focus) {
    const m = data.modules.find((x) => x.subject === state.subject);
    clear(bar).append(tabs(data.modules.map((x) => ({ id: x.subject, label: x.name, count: x.totals.total })), state.subject,
      (id, viaKey) => { state.subject = id; draw(viaKey); }, 'Modul'));
    if (focus) bar.querySelector('[aria-selected="true"]').focus();
    clear(body);
    if (!m.papers.length) body.append(h('p', { class: 'empty' }, 'Keine Einträge.'));
    else {
      body.append(rowList('full', [['Semester'], ['Titel'], ['Art'], ['Lsg.'], ['Bezug'], ['Wert.', 'num'], ['Status']],
        m.papers.map((p) => paperRow(p, { lecturer: 'marks', labels }))));
    }
    add(clear(foot),
      reasonLegend(labels),
      m.totals.total > m.papers.length
        ? h('a', { class: 'link', href: `#/faecher?subject=${encodeURIComponent(m.subject)}` }, 'Alle ', mono(String(m.totals.total)))
        : null);
    add(clear(more), h('summary', {}, 'Herkunft der Angaben'),
      h('p', {}, `Module und Dozenten: ${m.lecturers_source}.`),
      h('p', {}, data.match_rule),
      m.taught_semesters.length ? h('ul', {}, m.taught_semesters.map((s) => h('li', {},
        mono(s.semester_label), h('span', {}, s.names),
        s.is_current ? h('span', {}, 'läuft') : h('span', {}, mono(String(s.entries)), ' Einträge')))) : null,
      m.taught_semesters.length ? h('p', {}, 'Quelle: lecturer_map.csv (Vorlesungsverzeichnis).') : null);
  }

  const more = h('details', { class: 'more' });
  const section = h('section', { 'aria-label': 'Rangliste' },
    h('h2', { class: 'section-title' }, 'Rangliste'),
    h('div', { class: 'card' }, bar, body, foot),
    h('p', { class: 'footnote' }, `${LECTURER_CAVEAT}.`),
    more);
  draw(false);
  return section;
}
