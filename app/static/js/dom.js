// Minimal DOM helpers. Text always goes through text nodes — never innerHTML.
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v); // CSSOM: allowed under the CSP
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'selected' || k === 'disabled' || k === 'hidden') el[k] = Boolean(v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

/** Null-safe append: like h()'s children, skips null/false (Element.append would print "null"). */
export function add(el, ...children) { append(el, children); return el; }

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

// Line icons in the kit's style: 24-unit box, 1.6 stroke, inherits the text colour.
const SVG_NS = 'http://www.w3.org/2000/svg'; // an XML namespace name, not a request
export function icon(paths, size = 14) {
  const el = document.createElementNS(SVG_NS, 'svg');
  const set = { viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
    'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' };
  for (const [k, v] of Object.entries(set)) el.setAttribute(k, String(v));
  for (const d of paths) {
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', d);
    el.append(p);
  }
  return el;
}
export const ICON = {
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  dash: ['M8 12h8'],
  chevron: ['M7 10l5 5 5-5'],
  out: ['M14 4h6v6', 'M20 4l-9 9', 'M18 14v6H4V6h6'],
};

export const KIND_LABEL = { exam: 'Klausur', exercise: 'Übung', material: 'Material' };
export const STATUS_LABEL = { offen: 'offen', in_arbeit: 'in Arbeit', erledigt: 'erledigt' };
export const VERDICT_LABEL = {
  text: 'Text', mixed: 'Text und Scan', scan: 'Scan', no_text: 'ohne Text', broken: 'beschädigt',
};
// How a catalogue value was obtained (entries.*_from). Missing = could not be derived.
export const FROM_LABEL = {
  manifest: 'aus dem Verzeichnis', folder: 'aus dem Ordnernamen', filename: 'aus dem Dateinamen',
  sidecar: 'aus klausurwerk.json', default: 'nicht erkannt', computed: 'beim Einlesen berechnet',
};
// The catalogue names who lectured. Who set the paper is not recorded anywhere.
export const LECTURER_CAVEAT = 'Dozent laut Vorlesungsverzeichnis, nicht zwingend Prüfer';

/** A number that stands alone: the figure face. */
export function mono(text, attrs = {}) {
  return h('span', { ...attrs, class: `mono ${attrs.class || ''}`.trim() }, text);
}

export function tag(text) { return h('span', { class: 'tag' }, text); }

/** Semester in the figure face. Unknown stays a word; `short` is for a column already headed "Semester". */
export function semester(item, { short = false } = {}) {
  if (item.semester_known) return h('span', { class: 'sem mono', dataset: { known: '1' } }, item.semester_label);
  return h('span', { class: 'sem', dataset: { known: '0' }, title: item.semester_label },
    short ? [h('span', { class: 'sr-only' }, 'Semester '), 'unbekannt'] : item.semester_label);
}

/** Dot plus word. `wordless` keeps the word for screen readers and the tooltip only. */
export function statusMark(status, { wordless = false, label = null } = {}) {
  const word = label || STATUS_LABEL[status] || STATUS_LABEL.offen;
  return h('span', { class: 'status', dataset: { status: status || 'offen' }, title: wordless ? word : null },
    wordless ? h('span', { class: 'sr-only' }, word) : word);
}

export function solutionMark(has) {
  const word = has ? 'mit Lösung' : 'ohne Lösung';
  return h('span', { class: 'sol', dataset: { has: has ? '1' : '0' }, title: word },
    icon(has ? ICON.check : ICON.dash), h('span', { class: 'sr-only' }, word));
}

/** Lecturer-match markers. `labels` comes from /api/meta; the letter is only a handle for the label. */
export function reasonLetter(reason, labels) {
  if (labels && reason === labels.same_lecturer) return 'D';
  if (labels && reason === labels.named_examiner) return 'P';
  if (labels && reason === labels.sidecar_lecturer) return 'S';
  return '·';
}
export function reasonMarks(reasons, labels) {
  return h('span', { class: 'marks' }, (reasons || []).map((r) =>
    h('span', { class: 'markbox', title: r }, h('span', { 'aria-hidden': 'true' }, reasonLetter(r, labels)), h('span', { class: 'sr-only' }, r))));
}
export function reasonLegend(labels) {
  return h('span', { class: 'legend' },
    h('span', {}, h('span', { class: 'markbox', 'aria-hidden': 'true' }, 'D'), labels.same_lecturer),
    h('span', {}, h('span', { class: 'markbox', 'aria-hidden': 'true' }, 'P'), labels.named_examiner),
    labels.sidecar_lecturer ? h('span', {}, h('span', { class: 'markbox', 'aria-hidden': 'true' }, 'S'), labels.sidecar_lecturer) : null);
}

/** One meter: label, done/total in the figure face, a track. `live` adds the in-progress share. */
export function meter(label, done, total, { live = 0, extra = null } = {}) {
  const pct = (n) => (total > 0 ? `${Math.min(100, (n / total) * 100)}%` : '0%');
  return h('li', { class: 'meter' },
    h('span', { class: 'meter-label' }, label),
    h('span', { class: 'meter-value' }, extra, h('span', { class: 'frac' }, String(done), h('span', { class: 'of' }, `/${total}`))),
    h('span', { class: 'meter-track', role: 'img', 'aria-label': `${done} von ${total} erledigt${live ? `, ${live} in Arbeit` : ''}` },
      h('b', { style: { width: pct(done) } }),
      live ? h('b', { class: 'live', style: { width: pct(live) } }) : null));
}

export function card({ title, end = null, label = null, cls = '' }, ...children) {
  return h('section', { class: `card ${cls}`.trim(), 'aria-label': label || (typeof title === 'string' ? title : null) },
    title ? h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, title), end ? h('div', { class: 'card-end' }, end) : null) : null,
    children);
}

/** One subject as a linked card: progress meter and what it holds. */
export function subjectCard(s) {
  return h('a', { class: 'card', href: `#/faecher?subject=${encodeURIComponent(s.subject)}` },
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, s.subject),
      h('div', { class: 'card-end', title: 'erledigt / Einträge' }, mono(`${s.done}/${s.total}`))),
    h('div', { class: 'card-body' },
      h('ul', { class: 'meters' }, meter('Erledigt', s.done, s.total, { live: s.in_progress || 0 })),
      h('p', { class: 'muted' }, mono(String(s.exams)), ' Klausuren · ', mono(String(s.exercises)), ' Übungen',
        s.material ? [' · ', mono(String(s.material)), ' Material'] : null)));
}

export function field(id, label, control, hint = null) {
  return h('div', { class: 'field' }, h('label', { for: id }, label), control, hint ? h('span', { class: 'hint' }, hint) : null);
}

export function select(attrs, options) {
  return h('span', { class: 'select-wrap' }, h('select', { ...attrs, class: 'select' }, options), icon(ICON.chevron));
}

export function fileUrl(relPath, { ocr = false, page = null } = {}) {
  const path = relPath.split('/').map(encodeURIComponent).join('/');
  return `/files/${path}${ocr ? '?ocr=1' : ''}${page ? `#page=${Number(page)}` : ''}`;
}

export function entryHref(id, extra = {}) {
  const q = new URLSearchParams(extra).toString();
  return `#/eintrag/${encodeURIComponent(id)}${q ? `?${q}` : ''}`;
}

export function fmtDate(iso) {
  if (!iso) return 'unbekannt';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

/** A date in the figure face; an unknown date stays a word. */
export function dateNode(iso) { return iso ? mono(fmtDate(iso)) : 'unbekannt'; }

export function fmtDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  const p = (n) => String(n).padStart(2, '0');
  return hh > 0 ? `${hh}:${p(mm)}:${p(ss)}` : `${p(mm)}:${p(ss)}`;
}

/** A row list in the kit's Table look. `kind` picks the column set; `head` entries are [text, class?]. */
export function rowList(kind, head, rows) {
  return h('div', { class: `rows ${kind}` },
    head ? h('div', { class: 'rows-head', 'aria-hidden': 'true' }, head.map(([text, cls]) => h('span', { class: cls || null }, text))) : null,
    h('ul', {}, rows));
}

/**
 * A full paper row: semester, title, type, solution, lecturer, score, status — one link.
 * `lecturer` is 'marks' (dashboard reasons) or 'names' (who lectured, from the catalogue).
 */
export function paperRow(item, { showSubject = false, lecturer = 'names', labels = null } = {}) {
  const verdict = item.verdict && item.verdict !== 'text' ? VERDICT_LABEL[item.verdict] || item.verdict : null;
  const doz = lecturer === 'marks'
    ? reasonMarks(item.reasons, labels)
    : h('span', { class: 'doz', title: item.lecturers ? `${item.lecturers} – ${item.lecturers_source === 'sidecar' ? 'laut klausurwerk.json' : LECTURER_CAVEAT}` : 'Dozent unbekannt' }, item.lecturers || 'unbekannt');
  return h('li', {}, h('a', { class: 'row', href: entryHref(item.id) },
    semester(item, { short: true }),
    h('span', { class: 'title', title: item.title }, item.title,
      showSubject ? h('span', { class: 'sub' }, item.subject) : null,
      verdict ? h('span', { class: 'sub' }, verdict) : null),
    h('span', { class: 'row-meta' },
      tag(KIND_LABEL[item.kind] || item.kind),
      solutionMark(item.has_solution),
      doz,
      h('span', { class: 'num mono' }, item.score !== null && item.score !== undefined ? `${item.score} %` : '')),
    statusMark(item.status)));
}

/** The compact row inside a module card: semester, title, markers, status dot. */
export function compactRow(item, labels) {
  return h('li', {}, h('a', { class: 'row', href: entryHref(item.id) },
    semester(item, { short: true }),
    h('span', { class: 'title', title: item.title }, item.title),
    reasonMarks(item.reasons, labels),
    statusMark(item.status, { wordless: true })));
}

export function pager(total, limit, offset, onGo) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1, pages = Math.ceil(total / limit);
  return h('nav', { class: 'pager', 'aria-label': 'Seiten' },
    h('span', {}, 'Seite ', mono(`${page}/${pages}`)),
    h('span', { class: 'pager-nav' },
      h('button', { type: 'button', class: 'btn sm', disabled: offset <= 0, onclick: () => onGo(Math.max(0, offset - limit)) }, 'Zurück'),
      h('button', { type: 'button', class: 'btn sm', disabled: offset + limit >= total, onclick: () => onGo(offset + limit) }, 'Weiter')));
}

/** Tabs per the kit: roving tabindex, Left/Right wrap, Home/End jump. `items`: [{ id, label, count? }]. */
export function tabs(items, selected, onPick, label) {
  const list = h('div', { class: 'tabs', role: 'tablist', 'aria-label': label });
  items.forEach((it, i) => {
    const on = it.id === selected;
    list.append(h('button', {
      type: 'button', class: 'tab', role: 'tab', 'aria-selected': String(on), tabindex: on ? '0' : '-1',
      onclick: () => onPick(it.id),
      onkeydown: (e) => {
        const n = items.length;
        const to = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i - 1 + n) % n
          : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
        if (to < 0) return;
        e.preventDefault();
        onPick(items[to].id, true);
      },
    }, it.label, it.count !== undefined && it.count !== null ? mono(String(it.count)) : null));
  });
  return list;
}
