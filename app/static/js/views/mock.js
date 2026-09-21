import { api } from '../api.js';
import { h, add, clear, card, field, select, icon, ICON, mono, semester, solutionMark, statusMark, rowList, fileUrl, fmtDuration, dateNode, entryHref } from '../dom.js';

export const title = 'Probeklausur';
const PAGE = 15;

export async function render(root, { query }) {
  let timerId = null;
  const stop = () => { if (timerId) { clearInterval(timerId); timerId = null; } };
  const active = await api.mockActive();
  if (active.session) await running(root, active, (id) => { timerId = id; }, stop);
  else await picker(root, query.get('entry') || '');
  return stop;
}

// ---- choose an exam ------------------------------------------------------- //
async function picker(root, preselected) {
  root.append(h('div', { class: 'page-head' }, h('h1', {}, 'Probeklausur')));

  const [dash, subjects] = await Promise.all([api.dashboard({ limit: 1 }), api.subjects()]);
  const mine = dash.modules.map((m) => m.subject);
  const state = { subject: mine[0] || '', q: '', offset: 0, chosen: null };
  if (preselected) {
    try { state.chosen = (await api.entry(preselected)).entry; state.subject = state.chosen.subject; } catch { /* stale link */ }
  }

  const minutes = h('input', { id: 'm-min', class: 'input figure', type: 'number', min: '5', max: '360', step: '5', value: '90', inputmode: 'numeric' });
  const chosenBox = h('div', { class: 'chosen', 'aria-live': 'polite' });
  const listBox = h('div', { class: 'card-flush' });
  const count = h('span', {});
  const msg = h('p', { class: 'note danger', role: 'alert', hidden: true });
  const startBtn = h('button', { type: 'button', class: 'btn primary', onclick: start }, 'Starten');

  const subjWrap = select({ id: 'm-subject', onchange: (e) => { state.subject = e.target.value; state.offset = 0; load(); } },
    [h('option', { value: '' }, 'alle'),
      ...subjects.items.map((s) => h('option', { value: s.subject, selected: s.subject === state.subject }, `${s.subject}${mine.includes(s.subject) ? ' · mein Modul' : ''}`))]);
  const search = h('input', { id: 'm-q', class: 'input', type: 'search', oninput: (e) => { state.q = e.target.value; state.offset = 0; load(); } });

  async function start() {
    msg.hidden = true;
    const m = Number(minutes.value);
    if (!state.chosen) { msg.textContent = 'Erst eine Klausur wählen.'; msg.hidden = false; return; }
    if (!Number.isInteger(m) || m < 5 || m > 360) { msg.textContent = 'Zeit: 5 bis 360 Minuten.'; msg.hidden = false; return; }
    try {
      await api.mockStart({ entry_id: state.chosen.id, minutes: m });
      if (location.hash === '#/probe') window.dispatchEvent(new HashChangeEvent('hashchange'));
      else location.hash = '#/probe';
    } catch (err) { msg.textContent = err.message; msg.hidden = false; }
  }

  function drawChosen() {
    clear(chosenBox);
    startBtn.disabled = !state.chosen;
    if (!state.chosen) { chosenBox.append(h('span', { class: 'field-label' }, 'Klausur'), h('div', { class: 'muted' }, 'Keine gewählt.')); return; }
    chosenBox.append(h('span', { class: 'field-label' }, 'Klausur'),
      h('div', { class: 'chosen-title' }, state.chosen.title),
      h('div', { class: 'hint' }, semester(state.chosen), ' · ', state.chosen.has_solution ? 'mit Lösung' : 'ohne Lösung'));
  }

  async function load() {
    const res = await api.entries({ kind: 'exam', subject: state.subject, q: state.q, limit: PAGE, offset: state.offset });
    clear(count).append(mono(String(res.total)));
    clear(listBox);
    if (!res.items.length) { listBox.append(h('p', { class: 'empty' }, 'Keine Klausuren.')); return; }
    add(listBox,
      rowList('pick', [['Semester'], ['Titel'], ['Lsg.'], ['Status'], ['']], res.items.map((it) => h('li', {},
        h('button', { type: 'button', class: 'row', 'aria-pressed': String(state.chosen?.id === it.id),
          onclick: () => { state.chosen = it; drawChosen(); load(); } },
        semester(it, { short: true }), h('span', { class: 'title', title: it.title }, it.title),
        h('span', { class: 'row-meta' }, solutionMark(it.has_solution)), statusMark(it.status),
        h('span', { class: 'pickmark' }, icon(ICON.check)))))),
      res.total > PAGE ? h('div', { class: 'pager' },
        h('span', {}, 'Seite ', mono(`${Math.floor(state.offset / PAGE) + 1}/${Math.ceil(res.total / PAGE)}`)),
        h('span', { class: 'pager-nav' },
          h('button', { type: 'button', class: 'btn sm', disabled: state.offset <= 0, onclick: () => { state.offset = Math.max(0, state.offset - PAGE); load(); } }, 'Zurück'),
          h('button', { type: 'button', class: 'btn sm', disabled: state.offset + PAGE >= res.total, onclick: () => { state.offset += PAGE; load(); } }, 'Weiter'))) : null);
  }

  root.append(
    h('div', { class: 'stack' },
      card({ title: 'Start' },
        h('div', { class: 'card-body' },
          h('div', { class: 'setup' }, chosenBox, field('m-min', 'Minuten', minutes), startBtn),
          h('p', { class: 'hint' }, 'Die Lösung bleibt gesperrt, solange die Uhr läuft.'),
          msg)),
      card({ title: 'Klausuren', end: count },
        h('div', { class: 'card-body filters wide', style: { marginBottom: '0' } }, field('m-subject', 'Fach', subjWrap), field('m-q', 'Titel', search)),
        listBox)));

  drawChosen();
  await load();
  root.append(await history());
}

async function history() {
  const res = await api.mockHistory();
  const box = card({ title: 'Verlauf', end: mono(String(res.items.length)) });
  box.style.marginTop = 'var(--space-4)';
  if (!res.items.length) { box.append(h('p', { class: 'empty' }, 'Noch keine Probeklausur.')); return box; }
  box.append(h('div', { class: 'table-scroll' }, h('table', { class: 'plain' },
    h('thead', {}, h('tr', {}, [['Klausur'], ['Datum'], ['Gebraucht', 'num'], ['Geplant', 'num'], ['Wertung', 'num']].map(([t, c]) => h('th', { class: c || null }, t)))),
    h('tbody', {}, res.items.map((s) => h('tr', {},
      h('td', { class: 'title' }, s.title ? h('a', { href: entryHref(s.entry_id) }, s.title) : `${s.entry_id} (nicht mehr im Index)`),
      h('td', {}, dateNode(s.finished_at)),
      h('td', { class: 'num' }, s.seconds_taken !== null ? mono(fmtDuration(s.seconds_taken)) : 'unbekannt'),
      h('td', { class: 'num' }, mono(`${s.planned_minutes} min`)),
      h('td', { class: 'num' }, s.score !== null ? mono(`${s.score} %`) : '–')))))));
  return box;
}

// ---- a running exam ------------------------------------------------------- //
async function running(root, active, setTimer, stop) {
  const s = active.session;
  // Compare against the server clock once, so a wrong local clock does not shift the countdown.
  const skew = Date.now() - new Date(active.server_now).getTime();
  const startMs = new Date(s.started_at).getTime();
  const endMs = startMs + s.planned_minutes * 60000;

  let angabe = null;
  try { angabe = (await api.entry(s.entry_id)).files.find((f) => f.role === 'angabe' && f.is_pdf && f.on_disk) || null; } catch { /* entry gone */ }

  const clock = h('div', { class: 'timer', role: 'timer', 'aria-live': 'off' }, '–');
  const elapsed = mono('');
  const state = h('span', { class: 'clock-label' });
  const tick = () => {
    const now = Date.now() - skew;
    const left = Math.round((endMs - now) / 1000);
    clock.textContent = fmtDuration(Math.abs(left));
    clock.classList.toggle('over', left < 0);
    elapsed.textContent = fmtDuration((now - startMs) / 1000);
    state.textContent = left < 0 ? 'Überzeit' : 'Restzeit';
  };
  tick();
  setTimer(setInterval(tick, 1000));

  const score = h('input', { id: 'm-score', class: 'input figure', type: 'number', min: '0', max: '100', step: '1', inputmode: 'numeric', placeholder: '–' });
  const msg = h('span', { class: 'form-msg', role: 'status', dataset: { tone: 'danger' } });
  const finishForm = h('form', { class: 'setup', hidden: true, onsubmit: async (e) => {
    e.preventDefault();
    const v = score.value === '' ? null : Number(score.value);
    if (v !== null && (!Number.isInteger(v) || v < 0 || v > 100)) { msg.textContent = 'Wertung: ganze Zahl von 0 bis 100.'; return; }
    try {
      const res = await api.mockFinish(s.id, { score: v });
      stop();
      clear(root).append(h('div', { class: 'page-head' }, h('h1', {}, 'Abgegeben')),
        card({ title: s.title || s.entry_id },
          h('div', { class: 'card-body' },
            h('p', { class: 'figure-line' },
              h('span', {}, 'Gebraucht', mono(fmtDuration(res.seconds_taken))),
              h('span', {}, 'Wertung', res.score !== null ? mono(`${res.score} %`) : mono('–')),
              h('span', {}, statusMark('erledigt'))),
            h('div', { class: 'form-actions' },
              h('a', { class: 'btn primary', href: entryHref(res.entry_id, { role: 'loesung' }) }, 'Lösung ansehen'),
              h('a', { class: 'btn ghost', href: '#/statistik' }, 'Statistik')))));
    } catch (err) { msg.textContent = err.message; }
  } },
  field('m-score', 'Wertung % · freiwillig', score),
  h('button', { type: 'submit', class: 'btn primary' }, 'Endgültig abgeben'), msg);

  const stopBtn = h('button', { type: 'button', class: 'btn primary', onclick: () => { finishForm.hidden = false; stopBtn.hidden = true; score.focus(); } }, 'Abgeben');

  root.append(h('div', { class: 'stack' },
    card({ title: 'Probeklausur läuft', end: statusMark('gesperrt', { label: 'Lösung gesperrt' }) },
      h('div', { class: 'card-body' },
        h('div', { class: 'mock-head' }, h('h1', {}, s.title || s.entry_id), h('div', { class: 'clock' }, state, clock)),
        h('p', { class: 'figure-line' },
          h('span', {}, 'Geplant', mono(`${s.planned_minutes} min`)),
          h('span', {}, 'Bisher', elapsed)),
        h('div', { class: 'form-actions' }, stopBtn),
        finishForm)),
    h('section', { class: 'card', 'aria-label': 'Angabe' },
      angabe
        ? h('iframe', { class: 'viewer', src: fileUrl(angabe.rel_path), title: `Angabe: ${s.title || ''}` })
        : h('p', { class: 'empty' }, 'Keine Angabe als PDF.'))));
}
