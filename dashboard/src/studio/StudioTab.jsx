import React, { useCallback, useEffect, useState } from 'react';
import {
  Loader2, Film, FolderOpen, Download, ArrowLeft, Search, Archive, ArchiveRestore,
  Trash2, Pencil, Check, X, Copy, ExternalLink, ListChecks,
} from 'lucide-react';
import {
  listProjects, getProject, updateProject, deleteProject, reopenProject, listCampaigns, listClips,
  mediaUrl, RIGHTS_OPTIONS, formatBytes, formatDate,
} from './api';
import ReviewQueue from './ReviewQueue';
import CampaignsPage from './CampaignsPage';
import ChannelsPage from './ChannelsPage';
import PostsPage from './PostsPage';

const SECTIONS = [
  { id: 'projects', label: 'projects' },
  { id: 'review', label: 'review' },
  { id: 'posts', label: 'posts' },
  { id: 'campaigns', label: 'campaigns' },
  { id: 'channels', label: 'channels' },
];

// Self-host workspace (studio/). Saved projects live outside the job dirs, so
// they outlast JOB_RETENTION_SECONDS; "reopen" copies one back into the Clip
// Generator. Review, campaigns and channels prepare clips for posting.
export default function StudioTab({ onReopened }) {
  const [section, setSection] = useState('projects');
  const [view, setView] = useState({ name: 'list' });
  const [reviewProject, setReviewProject] = useState('');
  const [counts, setCounts] = useState(null);

  useEffect(() => {
    listClips({ reviewStatus: 'draft', limit: 1 }).then((d) => setCounts(d.counts)).catch(() => {});
  }, []);

  const openSection = (id) => {
    setSection(id);
    if (id !== 'review') setReviewProject('');
    if (id === 'projects') setView({ name: 'list' });
  };

  return (
    <div className="animate-fade">
      <p className="eyebrow mb-1.5">07 · PROJECTS</p>
      <div className="flex flex-wrap gap-1.5 mb-6 border-b border-rule pb-3">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            onClick={() => openSection(s.id)}
            className={`px-3 py-1.5 text-sm lowercase rounded-input transition-colors ${section === s.id
              ? 'bg-paper3 text-ink' : 'text-muted hover:text-ink'}`}
          >
            {s.label}
            {s.id === 'review' && counts?.draft > 0 && (
              <span className="badge-brass ml-1.5">{counts.draft}</span>
            )}
          </button>
        ))}
      </div>

      {section === 'projects' && (view.name === 'detail'
        ? <ProjectDetail
            id={view.id}
            onBack={() => setView({ name: 'list' })}
            onReopened={onReopened}
            onReview={(pid) => { setReviewProject(pid); setSection('review'); }}
          />
        : <ProjectList onOpen={(id) => setView({ name: 'detail', id })} />)}
      {section === 'review' && (
        <ReviewQueue key={reviewProject} initialProjectId={reviewProject} onCountsChange={setCounts} />
      )}
      {section === 'posts' && <PostsPage />}
      {section === 'campaigns' && <CampaignsPage />}
      {section === 'channels' && <ChannelsPage />}
    </div>
  );
}

function ProjectList({ onOpen }) {
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('active');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let alive = true;
    setError('');
    listProjects({ q: query, status, limit: 200 })
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setError('could not load your projects.'); });
    return () => { alive = false; };
  }, [query, status]);

  return (
    <div className="animate-fade">
      <h1 className="font-display lowercase text-2xl text-ink mb-2">Your projects</h1>
      <p className="text-muted text-sm mb-6 lowercase">
        Clips you saved with “save to projects”. They stay here after the job expires. Reopen a project to keep editing it.
      </p>

      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <label className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="search title or source url"
            className="input-field w-full text-sm pl-8"
          />
        </label>
        <div className="flex gap-2">
          {['active', 'archived'].map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`${status === s ? 'btn-primary' : 'btn-ghost'} px-3 py-2 text-xs`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="text-danger text-sm">{error}</p>}
      {!data && !error && <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>}

      {data && data.projects.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Film size={40} className="mx-auto mb-4 text-muted" />
          <p className="lowercase">
            {query || status === 'archived'
              ? 'no projects match.'
              : 'no saved projects yet. Generate clips, then press “save to projects”.'}
          </p>
        </div>
      )}

      {data && data.projects.length > 0 && (
        <>
          <p className="readout mb-3">{data.total} project{data.total === 1 ? '' : 's'}</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
            {data.projects.map((p) => (
              <button
                key={p.id}
                onClick={() => onOpen(p.id)}
                className="card card-hover overflow-hidden text-left group"
              >
                <div className="aspect-[9/16] bg-black">
                  {p.cover_url
                    ? <video src={`${mediaUrl(p.cover_url)}#t=0.5`} preload="metadata" muted className="w-full h-full object-contain pointer-events-none" />
                    : <div className="w-full h-full flex items-center justify-center text-muted"><Film size={28} /></div>}
                </div>
                <div className="p-3">
                  <p className="text-sm text-ink font-medium line-clamp-2 mb-1" title={p.title}>{p.title}</p>
                  <p className="readout">
                    {formatDate(p.created_at)} · {p.clip_count} clip{p.clip_count === 1 ? '' : 's'} · {formatBytes(p.size_bytes)}
                  </p>
                  {(p.campaign_name || p.review_counts?.draft > 0) && (
                    <p className="readout mt-0.5 truncate">
                      {p.review_counts?.draft > 0 && `${p.review_counts.draft} to review`}
                      {p.review_counts?.draft > 0 && p.campaign_name && ' · '}
                      {p.campaign_name}
                    </p>
                  )}
                </div>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ProjectDetail({ id, onBack, onReopened, onReview }) {
  const [project, setProject] = useState(null);
  const [campaigns, setCampaigns] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');

  const load = useCallback(() => {
    setError('');
    getProject(id)
      .then((d) => setProject(d.project))
      .catch(() => setError('could not load this project.'));
  }, [id]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    listCampaigns().then((d) => setCampaigns(d.campaigns || [])).catch(() => {});
  }, []);

  const patch = async (body, label) => {
    setBusy(label);
    setError('');
    try {
      const d = await updateProject(id, body);
      setProject(d.project);
      return true;
    } catch (e) {
      setError(e?.detail || 'could not save the change.');
      return false;
    } finally {
      setBusy('');
    }
  };

  const handleReopen = async () => {
    setBusy('reopen');
    setError('');
    try {
      const data = await reopenProject(id);
      await onReopened?.(data);
    } catch (e) {
      setError(e?.detail || 'could not reopen this project.');
      setBusy('');
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete “${project.title}” and its ${project.clip_count} saved clip(s)? This cannot be undone.`)) return;
    setBusy('delete');
    try {
      await deleteProject(id);
      onBack();
    } catch (e) {
      setError(e?.detail || 'could not delete this project.');
      setBusy('');
    }
  };

  if (!project) {
    return (
      <div>
        <button onClick={onBack} className="btn-ghost px-3 py-2 text-xs mb-6"><ArrowLeft size={14} /> projects</button>
        {error ? <p className="text-danger text-sm">{error}</p>
          : <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>}
      </div>
    );
  }

  const archived = project.status === 'archived';
  return (
    <div className="animate-fade">
      <button onClick={onBack} className="btn-ghost px-3 py-2 text-xs mb-6"><ArrowLeft size={14} /> projects</button>

      <div className="flex flex-col lg:flex-row lg:items-start gap-4 mb-6 pb-4 border-b border-rule">
        <div className="min-w-0 flex-1">
          {editingTitle ? (
            <form
              className="flex items-center gap-2 mb-2"
              onSubmit={async (e) => {
                e.preventDefault();
                if (await patch({ title: titleDraft }, 'title')) setEditingTitle(false);
              }}
            >
              <input
                autoFocus
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                maxLength={200}
                className="input-field flex-1 text-sm"
              />
              <button type="submit" className="btn-ghost px-2 py-2 text-xs" disabled={busy === 'title'} title="Save title"><Check size={14} /></button>
              <button type="button" className="btn-ghost px-2 py-2 text-xs" onClick={() => setEditingTitle(false)} title="Cancel"><X size={14} /></button>
            </form>
          ) : (
            <h1 className="font-display lowercase text-2xl text-ink mb-2 flex items-start gap-2">
              <span className="break-words min-w-0">{project.title}</span>
              <button
                onClick={() => { setTitleDraft(project.title); setEditingTitle(true); }}
                className="text-muted hover:text-ink mt-1.5 shrink-0"
                title="Rename"
              >
                <Pencil size={14} />
              </button>
            </h1>
          )}
          <p className="readout">
            saved {formatDate(project.created_at)}
            {project.updated_at !== project.created_at && <> · updated {formatDate(project.updated_at)}</>}
            {' · '}{project.clip_count} clip{project.clip_count === 1 ? '' : 's'}
            {' · '}{formatBytes(project.size_bytes)}
            {project.has_source && ' · source kept'}
          </p>
          {project.source_url && (
            <a
              href={project.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="readout text-brass hover:text-ink inline-flex items-center gap-1 mt-1 break-all"
            >
              <ExternalLink size={12} className="shrink-0" /> {project.source_url}
            </a>
          )}
          <label className="flex items-center gap-2 mt-3 text-xs text-muted lowercase">
            rights
            <select
              value={project.rights}
              onChange={(e) => patch({ rights: e.target.value }, 'rights')}
              disabled={busy === 'rights'}
              className="input-field text-xs py-1"
            >
              {RIGHTS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 mt-2 text-xs text-muted lowercase">
            campaign
            <select
              value={project.campaign_id || ''}
              onChange={(e) => patch({ campaign_id: e.target.value || null }, 'campaign')}
              disabled={busy === 'campaign'}
              className="input-field text-xs py-1"
            >
              <option value="">none</option>
              {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              {project.campaign_id && !campaigns.some((c) => c.id === project.campaign_id) && (
                <option value={project.campaign_id}>{project.campaign_name || 'current'}</option>
              )}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap gap-2 shrink-0">
          {onReview && (
            <button onClick={() => onReview(project.id)} className="btn-ghost px-3 py-2 text-xs"
              title="Review this project's clips">
              <ListChecks size={14} /> review{project.review_counts?.draft > 0 && ` (${project.review_counts.draft})`}
            </button>
          )}
          <button onClick={handleReopen} disabled={!!busy} className="btn-primary px-3 py-2 text-xs"
            title="Open this project in the Clip Generator to keep editing">
            {busy === 'reopen' ? <><Loader2 size={14} className="animate-spin" /> reopening…</> : <><FolderOpen size={14} /> reopen</>}
          </button>
          <button onClick={() => patch({ status: archived ? 'active' : 'archived' }, 'status')} disabled={!!busy}
            className="btn-ghost px-3 py-2 text-xs">
            {archived ? <><ArchiveRestore size={14} /> unarchive</> : <><Archive size={14} /> archive</>}
          </button>
          <button onClick={handleDelete} disabled={!!busy} className="btn-danger px-3 py-2 text-xs">
            <Trash2 size={14} /> delete
          </button>
        </div>
      </div>

      {error && <p className="text-danger text-sm mb-4">{error}</p>}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
        {project.clips.map((c) => <ClipCard key={c.id} clip={c} />)}
      </div>
    </div>
  );
}

function CopyField({ label, value }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch { /* clipboard blocked: the text is still selectable */ }
  };
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between">
        <span className="readout">{label}</span>
        <button onClick={copy} className="text-micro font-mono uppercase text-brass hover:text-ink flex items-center gap-1" title={`Copy ${label}`}>
          {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? 'copied' : 'copy'}
        </button>
      </div>
      <p className="text-xs text-ink whitespace-pre-wrap break-words line-clamp-4 select-text">{value}</p>
    </div>
  );
}

function ClipCard({ clip }) {
  const src = mediaUrl(clip.video_url);
  const dur = clip.duration ? `${Math.round(clip.duration)}s` : '';
  return (
    <div className="card overflow-hidden">
      <div className="aspect-[9/16] bg-black">
        <video src={src} controls preload="metadata" className="w-full h-full object-contain" />
      </div>
      <div className="p-3">
        <div className="flex items-start justify-between gap-2 mb-1">
          <p className="text-sm text-ink font-medium line-clamp-2" title={clip.title || ''}>
            {clip.title || `Clip ${clip.clip_index + 1}`}
          </p>
          <span className="badge-brass shrink-0">{clip.review_status}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="readout">#{clip.clip_index + 1}{dur && ` · ${dur}`}</span>
          <a href={src} download={clip.file_name}
            className="text-micro font-mono uppercase text-brass hover:text-ink flex items-center gap-1 transition-colors" title="Download">
            <Download size={14} /> Download
          </a>
        </div>
        <CopyField label="hook" value={clip.hook_text} />
        <CopyField label="caption" value={clip.caption} />
      </div>
    </div>
  );
}
