// All requests go to this same origin (127.0.0.1). No external calls.
async function request(path, options = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  let data = null;
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    const err = new Error((data && typeof data.detail === 'string' && data.detail) || `Fehler ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== null && v !== undefined && v !== '') p.set(k, v);
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

export const api = {
  meta: () => request('/api/meta'),
  dashboard: (params) => request(`/api/dashboard${qs(params)}`),
  subjects: () => request('/api/subjects'),
  semesters: (subject) => request(`/api/semesters${qs({ subject })}`),
  lecturers: () => request('/api/lecturers'),
  entries: (params) => request(`/api/entries${qs(params)}`),
  entry: (id) => request(`/api/entries/${encodeURIComponent(id)}`),
  saveProgress: (id, body) => request(`/api/entries/${encodeURIComponent(id)}/progress`, { method: 'PUT', body: JSON.stringify(body) }),
  search: (params) => request(`/api/search${qs(params)}`),
  mockActive: () => request('/api/mock/active'),
  mockStart: (body) => request('/api/mock/start', { method: 'POST', body: JSON.stringify(body) }),
  mockFinish: (id, body) => request(`/api/mock/${Number(id)}/finish`, { method: 'POST', body: JSON.stringify(body) }),
  mockHistory: () => request('/api/mock'),
  stats: () => request('/api/stats'),
  quellen: () => request('/api/quellen'),
};
