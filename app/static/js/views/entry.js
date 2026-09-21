import { api } from '../api.js';
import { h, add, clear, card, field, select, tag, mono, semester, statusMark, solutionMark, tabs, icon, ICON, fileUrl, dateNode, KIND_LABEL, VERDICT_LABEL, FROM_LABEL, LECTURER_CAVEAT } from '../dom.js';

export const title = 'Eintrag';

export async function render(root, { args, query }) {
  const data = await api.entry(args[0] || '');
  const { entry, files } = data;
  const byRole = Object.fromEntries(files.map((f) => [f.role, f]));
  const wantsSolution = query.get('role') === 'loesung';
  const view = {
    role: byRole.angabe ? 'angabe' : 'loesung',
    ocr: false,
    page: Number(query.get('page')) || null,
    solutionShown: false,
  };
  // A search hit inside a solution asks for it explicitly; that counts as the click.
  if (wantsSolution && byRole.loesung && !data.solution_locked) {
    view.role = 'loesung';
    view.solutionShown = true;
  }

  const headStatus = h('span', {}, statusMark(data.progress.status));
  root.append(
    h('a', { class: 'crumb', href: `#/faecher?subject=${encodeURIComponent(entry.subject)}` }, `← ${entry.subject}`),
    h('div', { class: 'page-head' },
      h('h1', {}, entry.title),
      h('div', { class: 'entry-meta' }, semester(entry), tag(KIND_LABEL[entry.kind] || entry.kind),
        h('span', { class: 'status-slot' }, solutionMark(entry.has_solution), entry.has_solution ? 'Lösung' : 'keine Lösung'),
        headStatus)));

  const tabbar = h('div', { class: 'tabbar' });
  const viewerBox = h('div', { class: 'card-flush', role: 'tabpanel' });
  const viewerFoot = h('div', { class: 'card-foot' });
  const left = h('section', { class: 'card viewer-pane', 'aria-label': 'Dokument' }, tabbar, viewerBox, viewerFoot);
  const onSaved = (status) => clear(headStatus).append(statusMark(status));
  const right = h('div', { class: 'stack' }, trackingCard(entry, data.progress, onSaved), lecturerCard(data), provenanceCard(entry, files, data.provenance));
  root.append(h('div', { class: 'entry-layout' }, left, right));

  function pick(role, viaKey) { view.role = role; view.page = null; draw(viaKey); }

  function state(lead, ...rest) { return h('div', { class: 'viewer-state' }, h('p', { class: 'lead' }, lead), rest); }

  function draw(focus) {
    const roles = [byRole.angabe ? { id: 'angabe', label: 'Angabe' } : null, byRole.loesung ? { id: 'loesung', label: 'Lösung' } : null].filter(Boolean);
    const file = byRole[view.role];
    clear(tabbar).append(tabs(roles, view.role, pick, 'Dokument'));
    if (focus) tabbar.querySelector('[aria-selected="true"]').focus();
    clear(viewerBox);
    clear(viewerFoot);
    viewerFoot.hidden = true;
    if (!file) { viewerBox.append(state('Keine Datei.')); return; }
    if (view.role === 'loesung' && data.solution_locked) {
      viewerBox.append(state('Lösung gesperrt', h('p', {}, 'Eine Probeklausur läuft.'),
        h('a', { class: 'btn', href: '#/probe' }, 'Zur Probeklausur')));
      return;
    }
    if (view.role === 'loesung' && !view.solutionShown) {
      viewerBox.append(state('Lösung verdeckt',
        h('button', { type: 'button', class: 'btn', onclick: () => { view.solutionShown = true; draw(); } }, 'Lösung zeigen')));
      return;
    }
    if (!file.on_disk) {
      viewerBox.append(state('Datei nicht gefunden', h('p', {}, 'Sie steht im Verzeichnis, liegt aber nicht auf der Platte.')));
      return;
    }
    if (!file.is_pdf) {
      viewerBox.append(state('Kein PDF', h('a', { class: 'btn', href: fileUrl(file.rel_path), download: '' }, 'Datei öffnen')));
      return;
    }
    const src = fileUrl(file.rel_path, { ocr: view.ocr, page: view.page });
    if (file.has_ocr_pdf) {
      tabbar.append(h('div', { class: 'tabbar-end' }, h('label', { class: 'check', title: 'Kopie mit Texterkennung statt Original' },
        h('input', { type: 'checkbox', checked: view.ocr, onchange: (e) => { view.ocr = e.target.checked; draw(); } }), 'OCR-Kopie')));
    }
    viewerBox.append(h('iframe', { class: 'viewer', src, title: `${view.role === 'loesung' ? 'Lösung' : 'Angabe'}: ${entry.title}` }));
    viewerFoot.hidden = false;
    viewerFoot.append(readability(file),
      h('a', { class: 'link', href: src, target: '_blank', rel: 'noopener', style: { marginLeft: 'auto' } }, 'Neuer Tab ', icon(ICON.out, 12)));
  }
  draw(false);
}

function readability(file) {
  if (!file.verdict) {
    return h('span', { class: 'legend' }, h('span', {}, 'Lesbarkeit unbekannt'),
      file.idx_pages_with_text === 0 ? h('span', {}, 'nicht durchsuchbar') : null);
  }
  const out = [h('span', {}, VERDICT_LABEL[file.verdict] || file.verdict)];
  if (file.pages !== null) {
    out.push(h('span', {}, mono(String(file.pages)), ' Seiten · ', mono(String(file.text_pages ?? '?')), ' Text · ', mono(String(file.image_only_pages ?? '?')), ' Bild'));
  }
  if (file.idx_pages_with_text === 0) out.push(h('span', {}, 'nicht durchsuchbar'));
  else if (file.idx_text_source) out.push(h('span', {}, `Suchtext: ${file.idx_text_source.startsWith('ocr') ? 'Texterkennung' : 'Original'}`));
  return h('span', { class: 'legend', title: 'Quelle: readability.csv' }, out);
}

function trackingCard(entry, p, onSaved) {
  const statusNames = { offen: 'offen', in_arbeit: 'in Arbeit', erledigt: 'erledigt' };
  const status = select({ id: 't-status' }, Object.keys(statusNames).map((s) => h('option', { value: s, selected: p.status === s }, statusNames[s])));
  const score = h('input', { id: 't-score', class: 'input figure', type: 'number', min: '0', max: '100', step: '1', inputmode: 'numeric', value: p.score ?? '', placeholder: '–' });
  const diff = select({ id: 't-diff' }, [h('option', { value: '' }, '–'),
    ...[1, 2, 3, 4, 5].map((n) => h('option', { value: String(n), selected: p.difficulty === n }, `${n}${n === 1 ? ' leicht' : n === 5 ? ' schwer' : ''}`))]);
  const done = h('input', { id: 't-done', class: 'input figure', type: 'date', value: p.done_on || '' });
  const notes = h('textarea', { id: 't-notes', class: 'input', maxlength: '20000' }, p.notes || '');
  const msg = h('span', { class: 'form-msg', role: 'status', 'aria-live': 'polite' });
  const say = (text, tone) => { msg.textContent = text; msg.dataset.tone = tone; };
  const val = (wrap) => wrap.querySelector('select').value;

  async function save(e) {
    e.preventDefault();
    say('', '');
    const s = score.value === '' ? null : Number(score.value);
    if (s !== null && (!Number.isInteger(s) || s < 0 || s > 100)) { say('Wertung: ganze Zahl von 0 bis 100.', 'danger'); return; }
    try {
      const res = await api.saveProgress(entry.id, {
        status: val(status), score: s, difficulty: val(diff) ? Number(val(diff)) : null,
        notes: notes.value, done_on: done.value || null,
      });
      if (res.done_on && !done.value) done.value = res.done_on;
      onSaved(val(status));
      say('Gespeichert', 'ok');
    } catch (err) { say(`Nicht gespeichert: ${err.message}`, 'danger'); }
  }

  return card({ title: 'Mein Stand' },
    h('form', { class: 'card-body', onsubmit: save },
      h('div', { class: 'form-grid' },
        field('t-status', 'Status', status),
        field('t-score', 'Wertung %', score),
        field('t-diff', 'Schwierigkeit', diff),
        field('t-done', 'Erledigt am', done)),
      field('t-notes', 'Notizen', notes),
      h('div', { class: 'form-actions' },
        h('button', { type: 'submit', class: 'btn primary' }, 'Speichern'),
        entry.kind === 'exam' ? h('a', { class: 'btn ghost', href: `#/probe?entry=${encodeURIComponent(entry.id)}` }, 'Als Probeklausur') : null,
        msg)));
}

function lecturerCard(data) {
  const body = h('div', { class: 'card-body' });
  const c = card({ title: 'Dozent und Prüfer' }, body);
  if (!data.entry.semester_known && !data.lecturers.known) {
    body.append(h('p', {}, 'Unbekannt – ohne Semester keine Zuordnung.'));
    return c;
  }
  const lect = h('dd', {});
  if (data.lecturers.known) {
    for (const r of data.lecturers.rows) {
      add(lect, h('div', {}, r.lecturers || 'unbekannt'),
        r.source === 'sidecar'
          ? h('div', { class: 'hint' }, `laut ${r.source_file || 'klausurwerk.json'}`)
          : h('div', { class: 'hint' }, `${r.source || 'Quelle unbekannt'} · lecturer_map.csv · erfasst `, dateNode(r.recorded_at)),
        r.caveat ? h('div', { class: 'hint' }, r.caveat) : null);
    }
  } else {
    lect.append(h('div', {}, 'unbekannt'), h('div', { class: 'hint' }, 'Kein Eintrag in lecturer_map.csv.'));
  }
  const exam = h('dd', {});
  if (data.examiners.known) {
    exam.append(h('ul', { class: 'plain-list' }, data.examiners.rows.map((r) => h('li', {}, r.professor,
        h('span', { class: 'muted' }, ` · ${r.listed_title || r.exam_type || 'ohne Titel'}`)))),
      h('div', { class: 'hint' }, `${data.examiners.note} Quelle: ${data.examiners.rows[0].source || 'unbekannt'} (professor_map.csv).`));
  } else {
    exam.append(h('div', {}, 'unbekannt'), h('div', { class: 'hint' }, 'Kein Eintrag in professor_map.csv.'));
  }
  body.append(h('dl', { class: 'facts' },
    h('dt', {}, 'Gelesen'), lect,
    h('dt', { title: 'Prüfer laut Fachschaft' }, 'Prüfer'), exam));
  // The file's own caveat, when present, already says this next to the name.
  const fileCaveat = data.lecturers.known && data.lecturers.rows.some((r) => r.caveat);
  const catalogue = data.lecturers.rows.some((r) => r.source !== 'sidecar');
  if (!fileCaveat && (catalogue || !data.lecturers.known)) body.append(h('p', { class: 'hint' }, `${LECTURER_CAVEAT}.`));
  return c;
}

function provenanceCard(entry, files, prov) {
  const link = (url) => (url && /^https?:\/\//.test(url)
    ? h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, url)
    : (url || 'unbekannt'));
  const scanned = entry.origin === 'folder-scan';
  // How the value was obtained, then the indexer's own note (matched text, rule applied).
  const how = (p) => [p && p.from ? h('div', { class: 'hint' }, FROM_LABEL[p.from] || p.from) : null,
    p && p.note ? h('div', { class: 'hint' }, p.note) : null];
  const body = h('div', { class: 'card-body' },
    h('dl', { class: 'facts' },
      h('dt', {}, 'Verzeichnis'), h('dd', {}, scanned ? 'Ordner durchsucht' : entry.origin),
      h('dt', {}, 'Kennung'), h('dd', {}, mono(entry.id)),
      h('dt', {}, 'Fach'), h('dd', {}, entry.subject, how(prov.subject)),
      h('dt', {}, 'Titel'), h('dd', {}, entry.title, how(prov.title)),
      h('dt', {}, 'Semester'), h('dd', {}, entry.semester_known ? mono(entry.semester_label) : entry.semester_label, how(prov.semester)),
      h('dt', {}, 'Art'), h('dd', {}, KIND_LABEL[entry.kind] || entry.kind, how(prov.kind)),
      scanned ? null : h('dt', {}, 'Quellseite'), scanned ? null : h('dd', {}, link(entry.source_page)),
      entry.source_note ? h('dt', {}, 'Vermerk') : null,
      entry.source_note ? h('dd', {}, entry.source_note, h('div', { class: 'hint' }, 'wörtlich aus dem Verzeichnis')) : null,
      scanned ? null : h('dt', {}, 'Abgerufen'), scanned ? null : h('dd', {}, dateNode(entry.retrieved_at))));
  for (const f of files) {
    const computed = f.sha256_from === 'computed';
    body.append(h('h3', { class: 'facts-title' }, f.role === 'loesung' ? 'Datei Lösung' : 'Datei Angabe'),
      h('dl', { class: 'facts' },
        scanned ? h('dt', {}, 'Pfad') : h('dt', {}, 'Adresse'), scanned ? h('dd', {}, f.rel_path) : h('dd', {}, link(f.url)),
        f.role_note ? h('dt', {}, 'Rolle') : null,
        f.role_note ? h('dd', {}, f.role === 'loesung' ? 'Lösung' : 'Angabe', how({ from: f.role_from, note: f.role_note })) : null,
        f.archive_url ? h('dt', {}, 'Archivkopie') : null, f.archive_url ? h('dd', {}, link(f.archive_url)) : null,
        scanned ? null : h('dt', {}, 'Abgerufen'), scanned ? null : h('dd', {}, dateNode(f.retrieved_at)),
        h('dt', {}, 'Größe'), h('dd', {}, f.bytes !== null ? mono(`${Math.round(f.bytes / 1024)} KB`) : 'unbekannt'),
        h('dt', {}, 'SHA-256'), h('dd', { class: 'fingerprint', title: computed ? 'Fingerabdruck beim Einlesen berechnet' : 'Fingerabdruck laut Verzeichnis' }, f.sha256 || 'unbekannt')));
  }
  body.append(h('p', { class: 'hint' }, scanned
    ? 'Aus Ordner- und Dateinamen gelesen. Nichts ergänzt; Fingerabdruck beim Einlesen berechnet.'
    : 'Unverändert aus den Verzeichnisdateien. Der Fingerabdruck wird nicht nachgerechnet.'));
  return card({ title: 'Herkunft' }, body);
}
