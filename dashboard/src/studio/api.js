// Studio (self-host saved projects) API client. Backend: studio/router.py.
import { apiFetch } from '../lib/api';
import { getApiUrl } from '../config';

// Like lib/api's apiJson, but keeps structured error details (the review
// queue's {code: 'rights_unknown'} answer) instead of dropping non-strings.
export class StudioError extends Error {
  constructor(status, detail) {
    const message = typeof detail === 'string' ? detail : (detail?.message || `request failed (${status})`);
    super(message);
    this.name = 'StudioError';
    this.status = status;
    this.code = typeof detail === 'object' && detail ? detail.code : undefined;
    // Same field the rest of the dashboard reads for a human message.
    this.detail = message;
  }
}

async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options);
  const text = await res.text().catch(() => '');
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
  if (!res.ok) {
    let detail = body?.detail;
    // FastAPI validation errors: a list of {loc, msg}.
    if (Array.isArray(detail)) detail = detail.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join(', ');
    throw new StudioError(res.status, detail || text || '');
  }
  return body;
}

const json = (body) => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const studioHealth = () => apiJson('/api/studio/health');

export const listProjects = ({ q = '', status = 'active', limit = 50, offset = 0 } = {}) => {
  const params = new URLSearchParams({ q, status, limit: String(limit), offset: String(offset) });
  return apiJson(`/api/studio/projects?${params}`);
};

export const getProject = (id) => apiJson(`/api/studio/projects/${id}`);

export const projectByJob = (jobId) => apiJson(`/api/studio/projects/by-job/${jobId}`);

export const saveProject = (jobId, { title, includeSource = false } = {}) =>
  apiJson('/api/studio/projects', {
    method: 'POST',
    ...json({ job_id: jobId, title: title || null, include_source: includeSource }),
  });

export const updateProject = (id, patch) =>
  apiJson(`/api/studio/projects/${id}`, { method: 'PATCH', ...json(patch) });

export const deleteProject = (id) =>
  apiJson(`/api/studio/projects/${id}`, { method: 'DELETE' });

export const reopenProject = (id) =>
  apiJson(`/api/studio/projects/${id}/reopen`, { method: 'POST' });

export const mediaUrl = (path) => (path ? getApiUrl(path) : '');

export const RIGHTS_OPTIONS = [
  { value: 'unknown', label: 'unknown' },
  { value: 'campaign', label: 'campaign (clipping program)' },
  { value: 'permission', label: 'permission from creator' },
  { value: 'cc', label: 'creative commons' },
  { value: 'own', label: 'own content' },
];

export const formatBytes = (n) => {
  if (!n) return '0 MB';
  const mb = n / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
};

export const formatDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '';

// ---- review queue ----
export const listClips = ({ reviewStatus = 'draft', projectId = '', campaignId = '', limit = 200 } = {}) => {
  const params = new URLSearchParams({
    review_status: reviewStatus, project_id: projectId, campaign_id: campaignId, limit: String(limit),
  });
  return apiJson(`/api/studio/clips?${params}`);
};

export const updateClip = (id, patch) =>
  apiJson(`/api/studio/clips/${id}`, { method: 'PATCH', ...json(patch) });

// ---- channels ----
export const PLATFORMS = ['youtube', 'tiktok', 'instagram', 'threads', 'facebook', 'x'];

export const listChannels = (includeArchived = false) =>
  apiJson(`/api/studio/channels?include_archived=${includeArchived ? 'true' : 'false'}`);
export const createChannel = (body) => apiJson('/api/studio/channels', { method: 'POST', ...json(body) });
export const updateChannel = (id, patch) =>
  apiJson(`/api/studio/channels/${id}`, { method: 'PATCH', ...json(patch) });

// ---- campaigns ----
export const listCampaigns = (status = '') => apiJson(`/api/studio/campaigns?status=${encodeURIComponent(status)}`);
export const createCampaign = (body) => apiJson('/api/studio/campaigns', { method: 'POST', ...json(body) });
export const updateCampaign = (id, patch) =>
  apiJson(`/api/studio/campaigns/${id}`, { method: 'PATCH', ...json(patch) });
export const deleteCampaign = (id) => apiJson(`/api/studio/campaigns/${id}`, { method: 'DELETE' });
export const estimatePayout = (id, views) =>
  apiJson(`/api/studio/campaigns/${id}/estimate?views=${Math.max(0, Math.floor(Number(views) || 0))}`);

export const formatMoney = (amount, currency = 'USD') => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 2 }).format(amount || 0);
  } catch {
    return `${currency} ${(amount || 0).toFixed(2)}`;
  }
};

// ---- posts & views ----
export const listPosts = ({ channelId = '', campaignId = '', status = '', platform = '' } = {}) => {
  const params = new URLSearchParams({
    channel_id: channelId, campaign_id: campaignId, status, platform, limit: '1000',
  });
  return apiJson(`/api/studio/posts?${params}`);
};
export const createPost = (body) => apiJson('/api/studio/posts', { method: 'POST', ...json(body) });
export const updatePost = (id, patch) => apiJson(`/api/studio/posts/${id}`, { method: 'PATCH', ...json(patch) });
export const deletePost = (id) => apiJson(`/api/studio/posts/${id}`, { method: 'DELETE' });
export const refreshPost = (id) => apiJson(`/api/studio/posts/${id}/refresh`, { method: 'POST' });
export const postSnapshots = (id) => apiJson(`/api/studio/posts/${id}/snapshots`);
export const addSnapshot = (id, body) =>
  apiJson(`/api/studio/posts/${id}/snapshots`, { method: 'POST', ...json(body) });
export const snapshotStatus = () => apiJson('/api/studio/snapshots/status');
export const runSnapshots = () => apiJson('/api/studio/snapshots/run', { method: 'POST' });
export const campaignReport = (id) => apiJson(`/api/studio/campaigns/${id}/report`);
export const exportPostsUrl = () => getApiUrl('/api/studio/export/posts.csv');

export const CLAIM_STATUSES = ['none', 'submitted', 'approved', 'paid', 'rejected'];

export const formatCount = (n) => (n == null ? '—' : Number(n).toLocaleString());
