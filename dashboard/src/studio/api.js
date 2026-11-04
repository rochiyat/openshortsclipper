// Studio (self-host saved projects) API client. Backend: studio/router.py.
import { apiJson } from '../lib/api';
import { getApiUrl } from '../config';

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
