// One function per backend endpoint. JSON in and out; any non-2xx throws ApiError(message).

const BASE = '/demo/api';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function readBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function request(method, path, { json, body, headers } = {}) {
  const init = { method, headers: { Accept: 'application/json', ...(headers || {}) } };
  if (json !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(json);
  } else if (body !== undefined) {
    init.body = body;
  }
  let response;
  try {
    response = await fetch(BASE + path, init);
  } catch {
    throw new ApiError('Could not reach the server. Is it running?', 0);
  }
  const data = await readBody(response);
  if (!response.ok) {
    const message = (data && (data.error || data.detail)) || `The server answered ${response.status}.`;
    throw new ApiError(String(message), response.status);
  }
  return data;
}

const get = (path) => request('GET', path);
const post = (path, json) => request('POST', path, { json });

// campaign board
export const getBoard = () => get('/p0/board');
export const getEmail = (leadId, emailNo) => get(`/p0/leads/${Number(leadId)}/emails/${Number(emailNo)}`);
export const getLead = (leadId) => get(`/p0/leads/${Number(leadId)}`);
export const LEAD_ACTIONS = Object.freeze(['pause', 'resume', 'stop', 'send_now']);
export function leadAction(leadId, action, by) {
  if (!LEAD_ACTIONS.includes(action)) throw new ApiError('Unknown action.', 0);
  return post(`/p0/leads/${Number(leadId)}/${action}`, { by });
}

// campaign Start / Pause (nothing is sent before Start)
export const startCampaign = (by) => post('/p0/campaign/start', { by });
export const pauseCampaign = (by) => post('/p0/campaign/pause', { by });

// templates
export const getTemplates = () => get('/p0/templates');
export const saveTemplate = (template) => post('/p0/templates', template);
export const setTemplateStatus = (id, status, by) => post(`/p0/templates/${Number(id)}/status`, { status, by });
export const previewDraft = (draft) => post('/p0/preview', { draft });

// schedule
export const getSchedule = () => get('/p0/schedule');
export const saveSchedule = (rows, by) => post('/p0/schedule', { rows, by });
export const exampleDates = (rows) => post('/p0/schedule/example', { rows });

// leads in
export const addLead = (lead) => post('/intake', lead);
export const SAMPLE_CSV_URL = `${BASE}/sample.csv`;
/** commit=false: a preview of what the file holds; commit=true: imports it. Body is the raw file. */
export function importLeads(file, { commit }) {
  return request('POST', `/import${commit ? '?commit=1' : ''}`, {
    body: file,
    headers: { 'X-Filename': file.name },
  });
}
