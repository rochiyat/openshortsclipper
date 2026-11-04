import React, { useCallback, useEffect, useState } from 'react';
import {
  Loader2, Plus, RefreshCw, Download, ExternalLink, Trash2, ChevronDown, ChevronUp, Send, AlertTriangle,
  Clock, RotateCcw, X, Link2,
} from 'lucide-react';
import RecordPostModal from './RecordPostModal';
import ViewsChart from './ViewsChart';
import {
  listPosts, listChannels, listCampaigns, updatePost, deletePost, refreshPost, postSnapshots, addSnapshot,
  snapshotStatus, runSnapshots, exportPostsUrl, CLAIM_STATUSES, formatCount, formatMoney, formatDate,
  listPublishJobs, cancelPublishJob, retryPublishJob,
} from './api';

const fmtWhen = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
}) : '—');

export default function PostsPage() {
  const [data, setData] = useState(null);
  const [channels, setChannels] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [filters, setFilters] = useState({ channelId: '', campaignId: '', status: '' });
  const [sched, setSched] = useState(null);
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    listPosts(filters).then(setData).catch(() => setError('could not load posts.'));
  }, [filters]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    listChannels(true).then((d) => setChannels(d.channels || [])).catch(() => {});
    listCampaigns().then((d) => setCampaigns(d.campaigns || [])).catch(() => {});
    snapshotStatus().then(setSched).catch(() => {});
  }, []);

  // While a full run is going, poll its status and reload when it ends.
  useEffect(() => {
    if (!sched?.running) return undefined;
    const t = setInterval(() => {
      snapshotStatus().then((st) => {
        setSched(st);
        if (!st.running) load();
      }).catch(() => {});
    }, 3000);
    return () => clearInterval(t);
  }, [sched?.running, load]);

  const runAll = async () => {
    try {
      const st = await runSnapshots();
      setSched({ ...st, running: true });
    } catch (e) {
      setError(e?.detail || 'could not start the refresh.');
    }
  };

  const replace = (post) => setData((d) => d && { ...d, posts: d.posts.map((p) => (p.id === post.id ? post : p)) });
  const remove = (id) => setData((d) => d && { ...d, posts: d.posts.filter((p) => p.id !== id), total: d.total - 1 });

  const totals = (data?.posts || []).reduce((acc, p) => {
    acc.views += p.stats.views || 0;
    acc.delta += p.stats.delta_24h || 0;
    return acc;
  }, { views: 0, delta: 0 });

  return (
    <div className="animate-fade">
      <div className="flex flex-col lg:flex-row lg:items-center gap-3 mb-4">
        <div className="text-sm text-muted min-w-0">
          {sched && (sched.enabled
            ? <>Views are read every day at <b className="text-ink">{sched.run_at}</b> ({sched.time_zone}) · last {fmtWhen(sched.last_run)}</>
            : <>Daily view reads are off (STUDIO_SNAPSHOTS=0). Use “refresh all”.</>)}
        </div>
        <div className="flex flex-wrap gap-2 lg:ml-auto shrink-0">
          <button onClick={runAll} disabled={sched?.running} className="btn-ghost px-3 py-2 text-xs"
            title="Read the views of every live post now">
            <RefreshCw size={14} className={sched?.running ? 'animate-spin' : ''} />
            {sched?.running ? 'reading views…' : 'refresh all'}
          </button>
          <a href={exportPostsUrl()} className="btn-ghost px-3 py-2 text-xs" download><Download size={14} /> csv</a>
          <button onClick={() => setRecording(true)} className="btn-primary px-3 py-2 text-xs"><Plus size={14} /> record post</button>
        </div>
      </div>

      {sched?.last_summary && sched.last_summary.failed > 0 && (
        <p className="text-xs text-muted mb-3 flex items-center gap-1.5">
          <AlertTriangle size={14} className="text-brass" />
          last run: {sched.last_summary.ok} read, {sched.last_summary.failed} could not be read
          {sched.last_summary.removed > 0 && `, ${sched.last_summary.removed} removed`}.
        </p>
      )}

      <PublishQueue onPublished={load} />

      <div className="flex flex-wrap gap-2 mb-4">
        <select value={filters.channelId} onChange={(e) => setFilters((f) => ({ ...f, channelId: e.target.value }))}
          className="input-field w-full sm:w-auto text-xs py-2" aria-label="Filter by channel">
          <option value="">all channels</option>
          {channels.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.platform}</option>)}
        </select>
        <select value={filters.campaignId} onChange={(e) => setFilters((f) => ({ ...f, campaignId: e.target.value }))}
          className="input-field w-full sm:w-auto text-xs py-2" aria-label="Filter by campaign">
          <option value="">all campaigns</option>
          <option value="none">no campaign</option>
          {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select value={filters.status} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
          className="input-field w-full sm:w-auto text-xs py-2" aria-label="Filter by status">
          <option value="">any status</option>
          <option value="live">live</option>
          <option value="pending">pending (no url)</option>
          <option value="removed">removed</option>
        </select>
        {data && data.posts.length > 0 && (
          <p className="readout self-center sm:ml-auto">
            {data.total} post{data.total === 1 ? '' : 's'} · {formatCount(totals.views)} views
            {totals.delta > 0 && ` · +${formatCount(totals.delta)} in 24h`}
          </p>
        )}
      </div>

      {error && <p className="text-danger text-sm mb-4">{error}</p>}
      {!data && !error && <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>}
      {data && data.posts.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Send size={40} className="mx-auto mb-4 text-muted" />
          <p className="lowercase">no posts yet. Use “publish” on an approved clip in review, or “record post” for one you uploaded yourself.</p>
        </div>
      )}

      <div className="space-y-2">
        {(data?.posts || []).map((p) => (
          <PostRow key={p.id} post={p} onChange={replace} onDelete={remove} />
        ))}
      </div>

      {recording && (
        <RecordPostModal onClose={() => setRecording(false)} onSaved={() => { setRecording(false); load(); }} />
      )}
    </div>
  );
}

const JOB_BADGE = {
  queued: 'badge-brass', running: 'badge-brass', awaiting: 'badge-brass', failed: 'badge-danger',
};

// Automatic publishes waiting, running or failed. Polls while anything moves.
function PublishQueue({ onPublished }) {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    listPublishJobs('queued,running,awaiting,failed')
      .then((d) => {
        setJobs((prev) => {
          const moving = (list) => list.filter((j) => j.status !== 'failed').length;
          if (moving(prev) > moving(d.jobs)) onPublished?.();
          return d.jobs;
        });
      })
      .catch(() => {});
  }, [onPublished]);

  useEffect(() => { load(); }, [load]);
  const busy = jobs.some((j) => ['queued', 'running', 'awaiting'].includes(j.status));
  useEffect(() => {
    if (!busy) return undefined;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [busy, load]);

  if (jobs.length === 0) return null;
  const act = async (fn) => {
    setError('');
    try { await fn(); load(); } catch (e) { setError(e?.detail || 'that did not work.'); }
  };
  return (
    <div className="card p-3 mb-4">
      <p className="readout mb-2">publish queue</p>
      <div className="space-y-2">
        {jobs.map((j) => (
          <div key={j.id} className="flex flex-col sm:flex-row sm:items-center gap-2 text-sm">
            <span className={`${JOB_BADGE[j.status] || 'badge-brass'} self-start sm:self-auto`}>{j.status}</span>
            <div className="min-w-0 flex-1">
              <p className="text-ink truncate">{j.clip?.title || j.title}</p>
              <p className="readout truncate">
                {j.channel?.name} · {j.channel?.platform}
                {j.status === 'queued' && <> · <Clock size={10} className="inline" /> {new Date(j.next_attempt_at || j.scheduled_at).toLocaleString()}</>}
                {j.attempts > 0 && ` · attempt ${j.attempts}`}
              </p>
              {j.last_error && <p className="text-xs text-muted truncate" title={j.last_error}>{j.last_error}</p>}
            </div>
            <div className="flex gap-2 shrink-0">
              {j.status === 'failed' && (
                <button onClick={() => act(() => retryPublishJob(j.id))} className="btn-ghost px-3 py-1.5 text-xs">
                  <RotateCcw size={12} /> retry
                </button>
              )}
              {['queued', 'failed'].includes(j.status) && (
                <button onClick={() => act(() => cancelPublishJob(j.id))} className="btn-ghost px-3 py-1.5 text-xs">
                  <X size={12} /> {j.status === 'failed' ? 'dismiss' : 'cancel'}
                </button>
              )}
              {['running', 'awaiting'].includes(j.status) && <Loader2 size={14} className="animate-spin text-brass" />}
            </div>
          </div>
        ))}
      </div>
      {error && <p className="text-danger text-xs mt-2">{error}</p>}
    </div>
  );
}

function AttachUrl({ post, onChange }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  return (
    <form
      className="flex flex-col sm:flex-row gap-2 mt-2"
      onSubmit={async (e) => {
        e.preventDefault();
        setSaving(true);
        setError('');
        try {
          onChange((await updatePost(post.id, { url })).post);
        } catch (err) {
          setError(err?.detail || 'could not attach the URL.');
        } finally {
          setSaving(false);
        }
      }}
    >
      <input type="url" required value={url} onChange={(e) => setUrl(e.target.value)}
        placeholder={`paste the ${post.platform} post url once it is public`} className="input-field text-xs py-1.5 flex-1" />
      <button type="submit" disabled={saving} className="btn-ghost px-3 py-1.5 text-xs"><Link2 size={12} /> attach</button>
      {error && <p className="text-danger text-xs sm:self-center">{error}</p>}
    </form>
  );
}

function PostRow({ post, onChange, onDelete }) {
  const [open, setOpen] = useState(false);
  const [series, setSeries] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [manual, setManual] = useState('');

  useEffect(() => {
    if (!open) return;
    postSnapshots(post.id).then((d) => setSeries(d.snapshots)).catch(() => setSeries([]));
  }, [open, post.id, post.stats.captured_at]);

  const act = async (label, fn) => {
    setBusy(label);
    setError('');
    try {
      await fn();
    } catch (e) {
      setError(e?.detail || 'that did not work.');
    } finally {
      setBusy('');
    }
  };

  const s = post.stats;
  const est = post.estimate;
  return (
    <div className={`card ${post.status === 'removed' ? 'opacity-70' : ''}`}>
      <div className="p-3 flex flex-col md:flex-row md:items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink font-medium truncate" title={post.clip?.title || ''}>
            {post.clip?.title || <span className="text-muted">(clip deleted)</span>}
          </p>
          <p className="readout truncate">
            {post.channel?.name} · {post.platform} · {formatDate(post.posted_at)}
            {post.campaign && ` · ${post.campaign.name}`}
            {post.status !== 'live' && ` · ${post.status}`}
          </p>
          {post.status === 'pending' && <AttachUrl post={post} onChange={onChange} />}
          {post.last_error && post.status === 'live' && (
            <p className="text-xs text-muted mt-0.5 truncate" title={post.last_error}>
              <AlertTriangle size={12} className="inline text-brass mr-1" />last read failed: {post.last_error}
            </p>
          )}
        </div>
        <div className="flex items-center gap-4 shrink-0">
          <div className="text-right">
            <p className="text-sm text-ink font-medium tabular-nums">{formatCount(s.views)}</p>
            <p className="readout">{s.delta_24h != null ? `+${formatCount(s.delta_24h)} / 24h` : 'views'}</p>
          </div>
          {est && (
            <div className="text-right">
              <p className="text-sm text-ink tabular-nums">{formatMoney(est.payout, est.currency)}</p>
              <p className="readout">{est.eligible_views === 0 ? 'below min' : 'est. payout'}</p>
            </div>
          )}
          <button onClick={() => setOpen((o) => !o)} className="btn-ghost px-2 py-2 text-xs" aria-expanded={open}
            title={open ? 'Hide details' : 'Show details'}>
            {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {open && (
        <div className="border-t border-rule p-3 space-y-3">
          {series === null ? <Loader2 size={16} className="animate-spin text-brass" /> : <ViewsChart snapshots={series} />}
          <p className="readout">
            last read {fmtWhen(s.captured_at)}{s.source && ` via ${s.source}`}
            {s.likes != null && ` · ${formatCount(s.likes)} likes`}
            {s.comments != null && ` · ${formatCount(s.comments)} comments`}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {post.url && (
              <a href={post.url} target="_blank" rel="noopener noreferrer" className="btn-ghost px-3 py-2 text-xs">
                <ExternalLink size={14} /> open
              </a>
            )}
            {post.status === 'live' && (
              <button onClick={() => act('refresh', async () => onChange((await refreshPost(post.id)).post))}
                disabled={!!busy} className="btn-ghost px-3 py-2 text-xs">
                <RefreshCw size={14} className={busy === 'refresh' ? 'animate-spin' : ''} /> read views now
              </button>
            )}
            {post.campaign && (
              <label className="flex items-center gap-1.5 text-xs text-muted lowercase">
                claim
                <select value={post.claim_status} disabled={!!busy}
                  onChange={(e) => act('claim', async () => onChange((await updatePost(post.id, { claim_status: e.target.value })).post))}
                  className="input-field text-xs py-1">
                  {CLAIM_STATUSES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
            )}
            {post.status === 'removed' && (
              <button onClick={() => act('live', async () => onChange((await updatePost(post.id, { status: 'live' })).post))}
                disabled={!!busy} className="btn-ghost px-3 py-2 text-xs">mark live again</button>
            )}
            <form
              className="flex items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                if (manual === '') return;
                act('manual', async () => {
                  onChange((await addSnapshot(post.id, { views: Math.floor(Number(manual)) })).post);
                  setManual('');
                });
              }}
            >
              <input type="number" min="0" value={manual} onChange={(e) => setManual(e.target.value)} placeholder="views"
                className="input-field text-xs py-1 w-24" aria-label="Enter views by hand" />
              <button type="submit" disabled={!!busy || manual === ''} className="btn-ghost px-3 py-2 text-xs">add</button>
            </form>
            <button
              onClick={() => {
                if (!window.confirm('Delete this post and its view history?')) return;
                act('delete', async () => { await deletePost(post.id); onDelete(post.id); });
              }}
              disabled={!!busy} className="btn-danger px-3 py-2 text-xs md:ml-auto">
              <Trash2 size={14} /> delete
            </button>
          </div>
          {error && <p className="text-danger text-xs">{error}</p>}
        </div>
      )}
    </div>
  );
}
