import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Loader2, Film, FolderOpen, Download, ArrowLeft, Search, Archive, ArchiveRestore,
  Trash2, Pencil, Check, X, Copy, ExternalLink, ListChecks, Tag, CheckCheck, Ban,
  TrendingUp, Calendar, FileText, Scissors, Crosshair, Wand2, Type, Languages, Share2,
  Image as ImageIcon,
} from 'lucide-react';
import {
  listProjects, getProject, updateProject, deleteProject, reopenProject, listCampaigns, listClips,
  listTags, bulkReview, mediaUrl, RIGHTS_OPTIONS, formatBytes, formatDate,
} from './api';
import OverviewPage from './OverviewPage';
import ReviewQueue from './ReviewQueue';
import CampaignsPage from './CampaignsPage';
import ChannelsPage from './ChannelsPage';
import PostsPage from './PostsPage';
import { apiFetch } from '../lib/api';
import { QUIET_BTN } from '../components/ResultCard';
import Modal from '../components/ui/Modal';

const SECTIONS = [
  { id: 'overview', label: 'overview' },
  { id: 'projects', label: 'projects' },
  { id: 'review', label: 'review' },
  { id: 'posts', label: 'posts' },
  { id: 'campaigns', label: 'campaigns' },
  { id: 'channels', label: 'channels' },
];

// Self-host workspace (studio/). Saved projects live outside the job dirs, so
// they outlast JOB_RETENTION_SECONDS; "reopen" copies one back into the Clip
// Generator. Review, campaigns and channels prepare clips for posting.
export default function StudioTab({ onReopened, geminiKey }) {
  const [section, setSection] = useState('overview');
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

      {section === 'overview' && <OverviewPage onGo={openSection} />}
      {section === 'projects' && (view.name === 'detail'
        ? <ProjectDetail
            id={view.id}
            onBack={() => setView({ name: 'list' })}
            onReopened={onReopened}
            onReview={(pid) => { setReviewProject(pid); setSection('review'); }}
          />
        : <ProjectList onOpen={(id) => setView({ name: 'detail', id })} />)}
      {section === 'review' && (
        <ReviewQueue key={reviewProject} initialProjectId={reviewProject} onCountsChange={setCounts}
          geminiKey={geminiKey} />
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
  const [tag, setTag] = useState('');
  const [tags, setTags] = useState([]);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    listTags().then((d) => setTags(d.tags || [])).catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let alive = true;
    setError('');
    listProjects({ q: query, status, tag, limit: 200 })
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setError('could not load your projects.'); });
    return () => { alive = false; };
  }, [query, status, tag]);

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

      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 -mt-3 mb-6" role="group" aria-label="Filter by tag">
          {tags.map((t) => (
            <button key={t.tag} onClick={() => setTag((cur) => (cur === t.tag ? '' : t.tag))}
              className={`px-2.5 py-1 rounded-full border text-xs transition-colors ${tag === t.tag
                ? 'border-brass text-ink bg-paper3' : 'border-rule text-muted hover:text-ink'}`}>
              #{t.tag} <span className="opacity-60">{t.count}</span>
            </button>
          ))}
        </div>
      )}

      {error && <p className="text-danger text-sm">{error}</p>}
      {!data && !error && <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>}

      {data && data.projects.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Film size={40} className="mx-auto mb-4 text-muted" />
          <p className="lowercase">
            {query || tag || status === 'archived'
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
                  {p.tags?.length > 0 && (
                    <p className="text-xs text-muted mt-1 truncate">{p.tags.map((t) => `#${t}`).join(' ')}</p>
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

  // Best score first, ties in transcript order: same order as the Clip Generator.
  const rankedClips = useMemo(() => {
    const score = (c) => (Number.isFinite(c.predicted_score) ? c.predicted_score : -1);
    return [...(project?.clips || [])].sort((a, b) => score(b) - score(a) || a.clip_index - b.clip_index);
  }, [project]);

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

  const bulk = async (reviewStatus) => {
    const ids = project.clips.filter((c) => c.review_status === 'draft').map((c) => c.id);
    if (!ids.length) return;
    setBusy(`bulk-${reviewStatus}`);
    setError('');
    try {
      try {
        await bulkReview(ids, reviewStatus);
      } catch (e) {
        if (e?.code !== 'rights_unknown') throw e;
        if (!window.confirm(`${e.message}\n\nApprove all anyway?`)) return;
        await bulkReview(ids, reviewStatus, true);
      }
      load();
    } catch (e) {
      setError(e?.detail || 'could not update the clips.');
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

  // Every editor works on a live job, so a tool picked here reopens the project
  // and lands in the Clip Generator with that tool open. Throws so the clip card
  // can show the failure next to the button that was pressed.
  const openTool = async (action) => {
    setBusy('tool');
    try {
      const data = await reopenProject(id);
      await onReopened?.(data, action);
    } catch (e) {
      setBusy('');
      throw e;
    }
  };

  const handleSchedule = async () => {
    setError('');
    try {
      await openTool({ tool: 'schedule' });
    } catch (e) {
      setError(e?.detail || 'could not reopen this project.');
    }
  };

  // The zip endpoint reads the job dir, which the retention sweep may have
  // removed: reopen restores it without leaving this page.
  const handleDownloadAll = async () => {
    setBusy('zip');
    setError('');
    try {
      await reopenProject(id);
      const res = await apiFetch(`/api/jobs/${project.job_id}/download-all`);
      if (!res.ok) throw new Error('zip failed');
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = `openshorts_clips_${project.job_id.slice(0, 8)}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e?.detail || 'could not download the clips.');
    } finally {
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
          <TagEditor tags={project.tags || []} disabled={busy === 'tags'}
            onSave={(tags) => patch({ tags }, 'tags')} />
        </div>

        <div className="flex flex-wrap gap-2 shrink-0">
          {onReview && (
            <button onClick={() => onReview(project.id)} className="btn-ghost px-3 py-2 text-xs"
              title="Review this project's clips">
              <ListChecks size={14} /> review{project.review_counts?.draft > 0 && ` (${project.review_counts.draft})`}
            </button>
          )}
          <button onClick={handleReopen} disabled={!!busy} className="btn-ghost px-3 py-2 text-xs"
            title="Open this project in the Clip Generator to keep editing">
            {busy === 'reopen' ? <><Loader2 size={14} className="animate-spin" /> reopening…</> : <><FolderOpen size={14} /> reopen</>}
          </button>
          <button onClick={handleDownloadAll} disabled={!!busy} className="btn-ghost px-3 py-2 text-xs"
            title="Download all clips as a ZIP">
            {busy === 'zip' ? <><Loader2 size={14} className="animate-spin" /> zipping…</> : <><Download size={14} /> download all</>}
          </button>
          {project.clip_count > 1 && (
            <button onClick={handleSchedule} disabled={!!busy} className="btn-primary px-4 py-2 text-xs">
              <Calendar size={14} /> schedule week
            </button>
          )}
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

      {project.review_counts?.draft > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <span className="readout">{project.review_counts.draft} draft clip{project.review_counts.draft === 1 ? '' : 's'}:</span>
          <button onClick={() => bulk('approved')} disabled={!!busy} className="btn-ghost px-3 py-1.5 text-xs">
            {busy === 'bulk-approved' ? <Loader2 size={12} className="animate-spin" /> : <CheckCheck size={12} />} approve all
          </button>
          <button onClick={() => bulk('rejected')} disabled={!!busy} className="btn-ghost px-3 py-1.5 text-xs">
            {busy === 'bulk-rejected' ? <Loader2 size={12} className="animate-spin" /> : <Ban size={12} />} reject all
          </button>
        </div>
      )}

      <div className="grid gap-4 pb-10 grid-cols-[repeat(auto-fill,minmax(min(100%,600px),1fr))]">
        {rankedClips.map((c) => <ClipCard key={c.id} clip={c} disabled={!!busy} onTool={openTool} />)}
      </div>
    </div>
  );
}

function CopyButton({ label, value }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked: the text is still selectable */ }
  };
  return (
    <button onClick={copy} aria-label={`copy ${label}`} className="p-1 rounded-full text-muted hover:text-brass transition-colors shrink-0">
      {copied ? <Check size={14} className="text-ok" /> : <Copy size={14} />}
    </button>
  );
}

function CopyRow({ label, value }) {
  if (!value) return null;
  return (
    <div className="bg-paper rounded-input px-3 py-2 border border-rule flex items-center gap-2 min-w-0 overflow-hidden">
      <span className="eyebrow shrink-0">{label}</span>
      <p className="text-xs text-ink2 truncate flex-1 min-w-0">{value}</p>
      <CopyButton label={label} value={value} />
    </div>
  );
}

function CopyBlock({ label, value }) {
  if (!value) return null;
  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="eyebrow">{label}</span>
        <CopyButton label={label} value={value} />
      </div>
      <p className="text-sm text-ink2 select-all break-words bg-paper rounded-input p-3 border border-rule whitespace-pre-wrap">{value}</p>
    </div>
  );
}

const CLIP_TOOLS = [
  { tool: 'edit', label: 'edit clip', Icon: Scissors },
  { tool: 'reframe', label: 'reframing', Icon: Crosshair },
  { tool: 'auto', label: 'auto edit', Icon: Wand2 },
  { tool: 'subtitles', label: 'subtitles', Icon: Type },
  { tool: 'hook', label: 'edit hook', Icon: Wand2 },
  { tool: 'dub', label: 'dub voice', Icon: Languages },
  { tool: 'thumbnail', label: 'thumbnail', Icon: ImageIcon },
];

function formatClipDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  const s = Math.floor(seconds);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

// Same card as the Clip Generator's ResultCard, for a saved clip with no live
// job behind it: the tool buttons go through onTool, which reopens the project.
function ClipCard({ clip, disabled, onTool }) {
  const [showDescriptions, setShowDescriptions] = useState(false);
  const [resolution, setResolution] = useState(null);
  const [pendingTool, setPendingTool] = useState(null);
  const [toolError, setToolError] = useState('');
  const src = mediaUrl(clip.video_url);
  const number = clip.clip_index + 1;
  const duration = formatClipDuration(clip.duration);

  const runTool = async (tool) => {
    setPendingTool(tool);
    setToolError('');
    try {
      await onTool({ tool, index: clip.clip_index });
    } catch (e) {
      setToolError(e?.detail || 'could not reopen this project.');
    } finally {
      setPendingTool(null);
    }
  };

  return (
    <div className="card overflow-hidden flex flex-col md:flex-row group hover:border-rule2 transition-colors md:min-h-[420px]">
      <div className="w-full max-w-[calc(64vh*0.5625)] md:max-w-none mx-auto md:mx-0 md:w-[236px] bg-black relative shrink-0 aspect-[9/16] md:aspect-auto">
        <video
          src={src}
          controls
          preload="metadata"
          playsInline
          className="w-full h-full object-contain"
          onLoadedMetadata={(e) => {
            if (e.target.videoWidth) setResolution(`${e.target.videoWidth}×${e.target.videoHeight}`);
          }}
        />
        <div className="absolute top-3 left-3 flex gap-2">
          <span className="bg-black/70 text-ink font-mono text-micro uppercase px-2 py-1 rounded-full">Clip {number}</span>
          {Number.isFinite(clip.predicted_score) && (
            <span
              className="bg-black/70 font-mono text-micro uppercase px-2 py-1 rounded-full flex items-center gap-1"
              title="openshorts' prediction of how well this clip will perform, from 0 to 100"
            >
              <TrendingUp size={11} className="shrink-0 text-muted" />
              <span className="text-muted">viral</span>
              <b className={
                clip.predicted_score >= 80 ? 'text-ok'
                  : clip.predicted_score >= 65 ? 'text-brass'
                    : 'text-ink2'
              }>
                {clip.predicted_score}
              </b>
              <span className="text-muted">/100</span>
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 p-4 md:p-5 flex flex-col overflow-hidden min-w-0">
        <div className="mb-4">
          <h3 className="text-base font-medium text-ink leading-tight line-clamp-2 mb-2 break-words" title={clip.title || ''}>
            {clip.title || `Clip ${number}`}
          </h3>
          {clip.why && <p className="text-xs text-muted leading-snug break-words mb-2">{clip.why}</p>}
          <div className="flex flex-wrap gap-1.5">
            {duration && <span className="readout bg-paper3 px-2 py-0.5 rounded-full shrink-0">{duration}</span>}
            {resolution && <span className="readout bg-paper3 px-2 py-0.5 rounded-full shrink-0">{resolution}</span>}
            <span className="readout bg-paper3 px-2 py-0.5 rounded-full shrink-0">{clip.review_status}</span>
          </div>
        </div>

        <div className="flex-1 min-h-0 space-y-2 mb-4">
          <CopyRow label="YOUTUBE" value={clip.title} />
          <CopyRow label="TIKTOK · IG" value={clip.caption} />
          <button
            onClick={() => setShowDescriptions(true)}
            className="w-full flex items-center justify-center gap-2 py-2 rounded-input border border-dashed border-rule text-xs lowercase text-muted hover:text-brass hover:border-rule2 transition-colors"
          >
            <FileText size={14} /> view descriptions
          </button>
        </div>

        {toolError && <p className="mb-3 text-xs text-danger break-words">{toolError}</p>}

        <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-2 mt-auto pt-4 border-t border-rule">
          {CLIP_TOOLS.map((t) => (
            <button key={t.tool} onClick={() => runTool(t.tool)} disabled={disabled} className={QUIET_BTN} title="Reopens this project to edit it">
              {pendingTool === t.tool
                ? <Loader2 size={16} className="animate-spin text-brass shrink-0" />
                : <t.Icon size={16} className="text-muted group-hover:text-brass transition-colors shrink-0" />}
              {t.label}
            </button>
          ))}
          <button
            onClick={() => runTool('post')}
            disabled={disabled}
            className="btn-primary flex-col gap-1 py-2.5 sm:py-2 px-1 text-[11px] leading-none rounded-input whitespace-nowrap"
            title="Reopens this project to post it"
          >
            {pendingTool === 'post' ? <Loader2 size={16} className="animate-spin shrink-0" /> : <Share2 size={16} className="shrink-0" />} post
          </button>
          <a href={src} download={clip.file_name} className={`${QUIET_BTN} col-span-2`}>
            <Download size={16} className="text-muted group-hover:text-brass transition-colors shrink-0" />
            download
          </a>
        </div>
      </div>

      <Modal
        isOpen={showDescriptions}
        onClose={() => setShowDescriptions(false)}
        eyebrow="GENERATED COPY"
        title="descriptions"
        size="md"
      >
        <div className="space-y-4">
          <CopyBlock label="YOUTUBE TITLE" value={clip.title} />
          <CopyBlock label="TIKTOK · IG CAPTION" value={clip.caption} />
          <CopyBlock label="HOOK" value={clip.hook_text} />
        </div>
      </Modal>
    </div>
  );
}

// Comma-separated tags, saved on enter/blur. The server lowercases and dedupes.
function TagEditor({ tags, onSave, disabled }) {
  const [value, setValue] = useState(tags.join(', '));
  useEffect(() => { setValue(tags.join(', ')); }, [tags]);
  const save = () => {
    const next = value.split(',').map((t) => t.trim()).filter(Boolean);
    if (next.join(',') !== tags.join(',')) onSave(next);
  };
  return (
    <label className="flex items-center gap-2 mt-2 text-xs text-muted lowercase">
      <Tag size={12} className="shrink-0" />
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={save}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); save(); } }}
        disabled={disabled}
        placeholder="tags, comma separated"
        maxLength={400}
        className="input-field text-xs py-1 flex-1 min-w-0"
        aria-label="Project tags"
      />
    </label>
  );
}
