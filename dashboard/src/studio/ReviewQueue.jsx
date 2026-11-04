import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, Check, X, RotateCcw, ChevronLeft, ChevronRight, ShieldAlert, Save, Film, Send } from 'lucide-react';
import RecordPostModal from './RecordPostModal';
import {
  listClips, updateClip, updateProject, listCampaigns, mediaUrl, RIGHTS_OPTIONS,
} from './api';

const STATUSES = ['draft', 'approved', 'rejected', 'posted'];
const FIELDS = ['title', 'hook_text', 'caption', 'hashtags'];

const isTyping = (el) => el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
  || el.tagName === 'SELECT' || el.isContentEditable);

// Every saved clip passes here before posting: fix the copy, then approve or
// reject. Keys: A approve · R reject · ← / → previous / next (not while typing).
export default function ReviewQueue({ initialProjectId = '', onCountsChange }) {
  const [status, setStatus] = useState('draft');
  const [campaignId, setCampaignId] = useState('');
  const [projectId, setProjectId] = useState(initialProjectId);
  const [campaigns, setCampaigns] = useState([]);
  const [data, setData] = useState(null);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [recording, setRecording] = useState(false);

  useEffect(() => {
    listCampaigns().then((d) => setCampaigns(d.campaigns || [])).catch(() => {});
  }, []);

  const load = useCallback(async (keepIndex = 0) => {
    setError('');
    try {
      const d = await listClips({ reviewStatus: status, projectId, campaignId });
      setData(d);
      onCountsChange?.(d.counts);
      setIndex(Math.max(0, Math.min(keepIndex, (d.clips?.length || 1) - 1)));
    } catch (e) {
      setError(e?.detail || 'could not load the review queue.');
    }
  }, [status, projectId, campaignId, onCountsChange]);

  useEffect(() => { setData(null); load(0); }, [load]);

  const clips = useMemo(() => data?.clips || [], [data]);
  const clip = clips[index] || null;

  // Reset the edit form whenever another clip comes into focus.
  useEffect(() => {
    if (!clip) { setDraft({}); return; }
    setDraft(Object.fromEntries(FIELDS.map((f) => [f, clip[f] || ''])));
  }, [clip]);

  const dirty = !!clip && FIELDS.some((f) => (draft[f] || '') !== (clip[f] || ''));
  const changedFields = () => (clip
    ? Object.fromEntries(FIELDS.filter((f) => (draft[f] || '') !== (clip[f] || '')).map((f) => [f, draft[f]]))
    : {});

  const replaceClip = (updated) => {
    setData((d) => d && { ...d, clips: d.clips.map((c) => (c.id === updated.id ? updated : c)) });
  };

  const send = async (patch, label) => {
    if (!clip || busy) return;
    setBusy(label);
    setError('');
    try {
      let res;
      try {
        res = await updateClip(clip.id, patch);
      } catch (e) {
        if (e?.code !== 'rights_unknown') throw e;
        if (!window.confirm(`${e.message}\n\nApprove anyway?`)) return;
        res = await updateClip(clip.id, { ...patch, force: true });
      }
      const leavesView = patch.review_status && patch.review_status !== status;
      if (leavesView) {
        await load(index);  // the clip drops out of this filter; stay on the same slot
      } else {
        replaceClip(res.clip);
      }
    } catch (e) {
      setError(e?.detail || 'could not save the clip.');
    } finally {
      setBusy('');
    }
  };

  const setReview = (reviewStatus) => send({ ...changedFields(), review_status: reviewStatus }, reviewStatus);
  const save = () => send(changedFields(), 'save');
  const go = (delta) => setIndex((i) => Math.max(0, Math.min(clips.length - 1, i + delta)));

  const setRights = async (rights) => {
    if (!clip) return;
    setBusy('rights');
    try {
      await updateProject(clip.project.id, { rights });
      setData((d) => d && {
        ...d,
        clips: d.clips.map((c) => (c.project.id === clip.project.id
          ? { ...c, project: { ...c.project, rights } } : c)),
      });
    } catch (e) {
      setError(e?.detail || 'could not update the rights.');
    } finally {
      setBusy('');
    }
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(document.activeElement)) return;
      const k = e.key.toLowerCase();
      if (k === 'a' && clip && clip.review_status !== 'posted') { e.preventDefault(); setReview('approved'); }
      else if (k === 'r' && clip && clip.review_status !== 'posted') { e.preventDefault(); setReview('rejected'); }
      else if (e.key === 'ArrowRight' || k === 'j') { e.preventDefault(); go(1); }
      else if (e.key === 'ArrowLeft' || k === 'k') { e.preventDefault(); go(-1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const counts = data?.counts || {};
  const posted = clip?.review_status === 'posted';

  return (
    <div className="animate-fade">
      <div className="flex flex-col lg:flex-row lg:items-center gap-3 mb-5">
        <div className="flex flex-wrap gap-2">
          {STATUSES.map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`${status === s ? 'btn-primary' : 'btn-ghost'} px-3 py-2 text-xs`}
            >
              {s} <span className="opacity-70">{counts[s] ?? 0}</span>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2 lg:ml-auto">
          {projectId && (
            <button onClick={() => setProjectId('')} className="btn-ghost px-3 py-2 text-xs" title="Show every project">
              one project <X size={12} />
            </button>
          )}
          <select
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            className="input-field text-xs py-2"
            aria-label="Filter by campaign"
          >
            <option value="">all campaigns</option>
            <option value="none">no campaign</option>
            {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      </div>

      {error && <p className="text-danger text-sm mb-4">{error}</p>}
      {!data && !error && <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>}

      {data && clips.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Film size={40} className="mx-auto mb-4 text-muted" />
          <p className="lowercase">
            {status === 'draft' ? 'nothing left to review. 🎉' : `no ${status} clips.`}
          </p>
        </div>
      )}

      {clip && (
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)] gap-5">
          <div>
            <div className="aspect-[9/16] bg-black rounded-card overflow-hidden max-w-[320px] mx-auto lg:mx-0">
              <video key={clip.id} src={mediaUrl(clip.video_url)} controls preload="metadata" className="w-full h-full object-contain" />
            </div>
            <div className="flex items-center justify-between mt-3 max-w-[320px] mx-auto lg:mx-0">
              <button onClick={() => go(-1)} disabled={index === 0} className="btn-ghost px-3 py-2 text-xs" title="Previous (←)">
                <ChevronLeft size={14} />
              </button>
              <span className="readout">{index + 1} / {clips.length}{data.total > clips.length ? ` of ${data.total}` : ''}</span>
              <button onClick={() => go(1)} disabled={index >= clips.length - 1} className="btn-ghost px-3 py-2 text-xs" title="Next (→)">
                <ChevronRight size={14} />
              </button>
            </div>
          </div>

          <div className="min-w-0">
            <p className="readout mb-1">
              {clip.project.title} · clip #{clip.clip_index + 1}
              {clip.duration ? ` · ${Math.round(clip.duration)}s` : ''}
              {clip.project.campaign_name && ` · ${clip.project.campaign_name}`}
            </p>
            <span className="badge-brass">{clip.review_status}</span>

            {clip.project.rights === 'unknown' && !posted && (
              <div className="mt-3 px-3 py-2.5 rounded-input bg-paper3 text-sm flex flex-col sm:flex-row sm:items-center gap-2">
                <span className="flex items-center gap-2 text-ink">
                  <ShieldAlert size={16} className="text-brass shrink-0" />
                  Rights for this source are not recorded.
                </span>
                <select
                  value="unknown"
                  onChange={(e) => setRights(e.target.value)}
                  disabled={busy === 'rights'}
                  className="input-field text-xs py-1 sm:ml-auto"
                  aria-label="Set rights for this project"
                >
                  {RIGHTS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            )}

            <div className="space-y-3 mt-4">
              <Field label="title" value={draft.title} maxLength={300} disabled={posted}
                onChange={(v) => setDraft((d) => ({ ...d, title: v }))} />
              <Field label="hook" value={draft.hook_text} maxLength={300} disabled={posted}
                onChange={(v) => setDraft((d) => ({ ...d, hook_text: v }))} />
              <Field label="caption" value={draft.caption} maxLength={5000} rows={4} disabled={posted}
                onChange={(v) => setDraft((d) => ({ ...d, caption: v }))} />
              <Field label="hashtags" value={draft.hashtags} maxLength={1000} disabled={posted}
                placeholder="#belajar #fyp" onChange={(v) => setDraft((d) => ({ ...d, hashtags: v }))} />
            </div>

            {!posted && (
              <div className="flex flex-wrap gap-2 mt-5">
                <button onClick={() => setReview('approved')} disabled={!!busy} className="btn-primary px-4 py-2 text-xs" title="Approve (A)">
                  {busy === 'approved' ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} approve
                  <kbd className="readout ml-1 opacity-70">A</kbd>
                </button>
                <button onClick={() => setReview('rejected')} disabled={!!busy} className="btn-danger px-4 py-2 text-xs" title="Reject (R)">
                  {busy === 'rejected' ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />} reject
                  <kbd className="readout ml-1 opacity-70">R</kbd>
                </button>
                {clip.review_status !== 'draft' && (
                  <button onClick={() => setReview('draft')} disabled={!!busy} className="btn-ghost px-4 py-2 text-xs">
                    <RotateCcw size={14} /> back to draft
                  </button>
                )}
                {clip.review_status === 'approved' && (
                  <button onClick={() => setRecording(true)} disabled={!!busy} className="btn-ghost px-4 py-2 text-xs"
                    title="You published this clip: paste its URL to track views">
                    <Send size={14} /> record post
                  </button>
                )}
                <button onClick={save} disabled={!dirty || !!busy} className="btn-ghost px-4 py-2 text-xs sm:ml-auto">
                  {busy === 'save' ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} save edits
                </button>
              </div>
            )}
            <p className="readout mt-3">
              {posted ? 'posted clips are read-only. See them in posts.' : 'approve / reject also saves your edits · ← → to move'}
            </p>
          </div>
        </div>
      )}
      {recording && clip && (
        <RecordPostModal
          clip={clip}
          onClose={() => setRecording(false)}
          onSaved={() => { setRecording(false); load(index); }}
        />
      )}
    </div>
  );
}

function Field({ label, value, onChange, rows = 1, maxLength, placeholder, disabled }) {
  const common = {
    value: value || '',
    onChange: (e) => onChange(e.target.value),
    maxLength,
    placeholder,
    disabled,
    className: 'input-field w-full text-sm',
  };
  return (
    <label className="block">
      <span className="readout block mb-1">{label}</span>
      {rows > 1 ? <textarea rows={rows} {...common} /> : <input {...common} />}
    </label>
  );
}
