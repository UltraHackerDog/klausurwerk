import { api } from '../api.js';
import { h, card, meter, mono, LECTURER_CAVEAT } from '../dom.js';

export const title = 'Statistik';

export async function render(root) {
  const s = await api.stats();
  root.append(h('div', { class: 'page-head' }, h('h1', {}, 'Statistik')));

  // Papers per week
  const weeks = [...s.per_week].reverse();
  const max = Math.max(1, ...weeks.map((w) => w.done));
  const total = weeks.reduce((n, w) => n + w.done, 0);
  const stack = h('div', { class: 'stack' });
  root.append(stack);
  stack.append(card({ title: 'Erledigt pro Woche', end: mono(String(total)) },
    weeks.length
      ? h('div', { class: 'weeks', role: 'img', 'aria-label': `Erledigt pro Woche: ${weeks.map((w) => `${w.week}: ${w.done}`).join(', ')}` },
        weeks.map((w) => h('div', { class: 'col', title: w.week }, h('span', { class: 'v' }, String(w.done)),
          h('span', { class: 'b', style: { height: `${Math.round((w.done / max) * 100)}%` } }), h('span', {}, w.week.slice(5)))))
      : h('p', { class: 'empty' }, 'Noch nichts erledigt.'),
    h('div', { class: 'card-foot' }, 'Kalenderwochen nach „Erledigt am“.')));

  // Per module
  const mine = new Set(s.my_subjects);
  const sorted = [...s.per_module].sort((a, b) => (mine.has(b.subject) - mine.has(a.subject)) || a.subject.localeCompare(b.subject, 'de'));
  stack.append(card({ title: 'Fächer', end: mono(String(sorted.length)) },
    h('ul', { class: 'meter-rows' }, sorted.map((m) => meter(
      mine.has(m.subject) ? h('strong', {}, m.subject) : m.subject, m.done, m.total,
      { extra: h('span', { class: 'avg', dataset: { known: m.scored ? '1' : '0' }, title: m.scored ? `Mittel aus ${m.scored} Wertungen` : 'keine Wertung eingetragen' },
        m.scored ? `Ø ${m.avg_score} %` : 'Ø unbekannt') }))),
    h('div', { class: 'card-foot' }, 'Ø: Mittel der eigenen Wertungen. Meine Module zuerst.')));

  stack.append(people(s.per_lecturer, 'Dozenten', `${LECTURER_CAVEAT}.`));
  if (s.per_sidecar_lecturer.items.length) stack.append(people(s.per_sidecar_lecturer, 'Dozenten laut klausurwerk.json', 'Je Eintrag angegeben.'));
  stack.append(people(s.per_examiner, 'Prüfer laut Fachschaft', 'Zuordnung über Fach und Semester.'));
}

function people(block, heading, note) {
  return card({ title: heading, end: mono(String(block.items.length)) },
    block.items.length
      ? h('ul', { class: 'meter-rows' }, block.items.map((r) => meter([r.name, h('span', { class: 'muted' }, r.module)], r.done, r.total)))
      : h('p', { class: 'empty' }, 'Keine Zuordnung möglich.'),
    h('div', { class: 'card-foot' }, `${note} Quelle: ${block.source_file}.`));
}
