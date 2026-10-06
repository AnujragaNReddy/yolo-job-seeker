// Empty base means same-origin, which the Vite dev proxy forwards to the
// backend. Set VITE_API_URL when the backend lives somewhere else.
const API_BASE = import.meta.env.VITE_API_URL || '';

function backendDownError() {
  const error = new Error(
    'Backend unreachable. Start it with "uvicorn server:app --reload" in the backend folder.'
  );
  error.backendDown = true;
  return error;
}

async function request(path, options = {}) {
  let response;

  const isForm = options.body instanceof FormData;

  try {
    response = await fetch(`${API_BASE}${path}`, {
      // Setting Content-Type on a FormData body breaks it: the browser has to
      // add the multipart boundary itself.
      headers: isForm ? undefined : { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch {
    throw backendDownError();
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);

    // A bodyless gateway status means the backend is down or waking, not that
    // the API rejected anything — without this it shows as a bare 500.
    if (!body && [500, 502, 503, 504].includes(response.status)) {
      throw backendDownError();
    }

    throw new Error(body?.detail || `Request failed (${response.status})`);
  }

  return response.json();
}

export const getHealth = () => request('/api/health');

export const getSources = () => request('/api/sources');

export const getProfile = () => request('/api/profile');

export const uploadResume = (file) => {
  const form = new FormData();
  form.append('file', file);
  return request('/api/resume', { method: 'POST', body: form });
};

export const updateProfile = (values) =>
  request('/api/profile', { method: 'PUT', body: JSON.stringify(values) });

export const deleteResume = () => request('/api/resume', { method: 'DELETE' });

export const getSettings = () => request('/api/settings');

export const updateSettings = (values) =>
  request('/api/settings', { method: 'PUT', body: JSON.stringify(values) });

export const runScan = (prepare = true) =>
  request(`/api/scan?prepare=${prepare}`, { method: 'POST' });

export const getMatches = ({ limit = 40, offset = 0, state, minimum } = {}) => {
  const params = new URLSearchParams({ limit, offset });
  if (state) params.set('state', state);
  if (minimum) params.set('minimum', minimum);
  return request(`/api/matches?${params}`);
};

export const getApplications = (state) =>
  request(`/api/applications${state ? `?state=${state}` : ''}`);

export const getApplication = (jobId) => request(`/api/applications/${jobId}`);

export const prepareApplication = (jobId, regenerate = false) =>
  request(`/api/applications/${jobId}/prepare?regenerate=${regenerate}`, {
    method: 'POST',
  });

export const setApplicationState = (jobId, state, note) =>
  request(`/api/applications/${jobId}/state`, {
    method: 'POST',
    body: JSON.stringify({ state, note }),
  });
