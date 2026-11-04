import React, { useEffect, useState } from 'react';
import { Loader2, Plus, Pencil, Trash2, ExternalLink, Megaphone } from 'lucide-react';
import Modal from '../components/ui/Modal';
import {
  listCampaigns, createCampaign, updateCampaign, deleteCampaign, estimatePayout, campaignReport,
  formatMoney, formatDate, formatCount,
} from './api';

const EMPTY = {
  name: '', platform_source: '', brief_url: '', cpm: '', currency: 'USD', min_views: '',
  max_payout: '', starts_at: '', ends_at: '', donation_pct: '', status: 'active', notes: '',
};

const toForm = (c) => Object.fromEntries(Object.keys(EMPTY).map((k) => [k, c[k] ?? '']));

// Blank inputs mean "not set": numbers become null (or 0 where required).
const toBody = (f) => ({
  name: f.name,
  platform_source: f.platform_source || null,
  brief_url: f.brief_url || null,
  cpm: f.cpm === '' ? 0 : Number(f.cpm),
  currency: f.currency || 'USD',
  min_views: f.min_views === '' ? 0 : Math.floor(Number(f.min_views)),
  max_payout: f.max_payout === '' ? null : Number(f.max_payout),
  starts_at: f.starts_at || null,
  ends_at: f.ends_at || null,
  donation_pct: f.donation_pct === '' ? 0 : Number(f.donation_pct),
  status: f.status,
  notes: f.notes || null,
});

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState(null);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null); // null | 'new' | campaign

  const load = () => {
    listCampaigns()
      .then((d) => setCampaigns(d.campaigns || []))
      .catch(() => setError('could not load campaigns.'));
  };
  useEffect(load, []);

  const handleDelete = async (c) => {
    if (!window.confirm(`Delete campaign “${c.name}”? Its projects stay, only the link is removed.`)) return;
    try {
      await deleteCampaign(c.id);
      load();
    } catch (e) {
      setError(e?.detail || 'could not delete the campaign.');
    }
  };

  return (
    <div className="animate-fade">
      <div className="flex items-center justify-between gap-3 mb-5">
        <p className="text-muted text-sm lowercase">
          Clipping programs that pay per 1,000 views. Link projects to a campaign from the project page.
        </p>
        <button onClick={() => setEditing('new')} className="btn-primary px-3 py-2 text-xs shrink-0">
          <Plus size={14} /> new campaign
        </button>
      </div>

      {error && <p className="text-danger text-sm mb-4">{error}</p>}
      {!campaigns && !error && <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>}
      {campaigns && campaigns.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Megaphone size={40} className="mx-auto mb-4 text-muted" />
          <p className="lowercase">no campaigns yet.</p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(campaigns || []).map((c) => (
          <div key={c.id} className="card p-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm text-ink font-medium break-words">{c.name}</p>
                <p className="readout mt-0.5">
                  {c.platform_source && `${c.platform_source} · `}
                  {formatMoney(c.cpm, c.currency)} / 1k views
                  {c.min_views > 0 && ` · min ${c.min_views.toLocaleString()} views`}
                </p>
              </div>
              <span className={c.status === 'active' ? 'badge-ok' : 'badge-brass'}>{c.status}</span>
            </div>
            <p className="readout mt-2">
              {c.starts_at || c.ends_at ? `${formatDate(c.starts_at) || '…'} → ${formatDate(c.ends_at) || '…'}` : 'no dates'}
              {c.max_payout != null && ` · cap ${formatMoney(c.max_payout, c.currency)}`}
              {c.donation_pct > 0 && ` · donate ${c.donation_pct}%`}
            </p>
            <p className="readout mt-1">
              {c.stats.projects} project{c.stats.projects === 1 ? '' : 's'} · {c.stats.clips.draft} draft ·{' '}
              {c.stats.clips.approved} approved · {c.stats.clips.posted} posted
            </p>
            {c.brief_url && (
              <a href={c.brief_url} target="_blank" rel="noopener noreferrer"
                className="readout text-brass hover:text-ink inline-flex items-center gap-1 mt-2">
                <ExternalLink size={12} /> brief
              </a>
            )}
            <CampaignTotals campaign={c} />
            <PayoutCalculator campaign={c} />
            <div className="flex gap-2 mt-3">
              <button onClick={() => setEditing(c)} className="btn-ghost px-3 py-2 text-xs"><Pencil size={14} /> edit</button>
              <button onClick={() => handleDelete(c)} className="btn-danger px-3 py-2 text-xs"><Trash2 size={14} /> delete</button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <CampaignForm
          campaign={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
        />
      )}
    </div>
  );
}

// Real numbers from recorded posts and their latest view snapshots.
function CampaignTotals({ campaign }) {
  const [report, setReport] = useState(null);
  useEffect(() => {
    campaignReport(campaign.id).then(setReport).catch(() => setReport(null));
  }, [campaign]);
  if (!report || report.posts.length === 0) return null;
  const t = report.totals;
  const claimable = report.posts.filter((p) => p.eligible_views > 0).length;
  return (
    <div className="mt-3 pt-3 border-t border-rule grid grid-cols-3 gap-2">
      <div>
        <p className="text-sm text-ink font-medium tabular-nums">{formatCount(t.views)}</p>
        <p className="readout">views · {report.posts.length} post{report.posts.length === 1 ? '' : 's'}</p>
      </div>
      <div>
        <p className="text-sm text-ink font-medium tabular-nums">{formatMoney(t.payout, report.currency)}</p>
        <p className="readout">est. payout · {claimable} claimable</p>
      </div>
      <div>
        <p className="text-sm text-ink font-medium tabular-nums">{formatMoney(t.donation, report.currency)}</p>
        <p className="readout">donation</p>
      </div>
    </div>
  );
}

function PayoutCalculator({ campaign }) {
  const [views, setViews] = useState('');
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (views === '') { setResult(null); return undefined; }
    const t = setTimeout(() => {
      estimatePayout(campaign.id, views).then(setResult).catch(() => setResult(null));
    }, 250);
    return () => clearTimeout(t);
  }, [views, campaign]);

  return (
    <div className="mt-3 pt-3 border-t border-rule">
      <label className="flex items-center gap-2">
        <span className="readout shrink-0">if a post gets</span>
        <input
          type="number" min="0" inputMode="numeric" value={views} placeholder="views"
          onChange={(e) => setViews(e.target.value)}
          className="input-field text-xs py-1 w-28"
        />
        <span className="readout">views</span>
      </label>
      {result && (
        <p className="text-xs text-ink mt-1.5">
          {result.eligible_views === 0 && result.views > 0
            ? `below the ${campaign.min_views.toLocaleString()}-view minimum: ${formatMoney(0, result.currency)}`
            : <>
                payout <b>{formatMoney(result.payout, result.currency)}</b>
                {result.donation > 0 && <> · donation {formatMoney(result.donation, result.currency)} · net {formatMoney(result.net, result.currency)}</>}
              </>}
        </p>
      )}
    </div>
  );
}

function CampaignForm({ campaign, onClose, onSaved }) {
  const [form, setForm] = useState(campaign ? toForm(campaign) : EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      if (campaign) await updateCampaign(campaign.id, toBody(form));
      else await createCampaign(toBody(form));
      onSaved();
    } catch (err) {
      setError(err?.detail || 'could not save the campaign.');
      setSaving(false);
    }
  };

  const input = (k, label, props = {}) => (
    <label className="block">
      <span className="readout block mb-1">{label}</span>
      <input value={form[k]} onChange={set(k)} className="input-field w-full text-sm" {...props} />
    </label>
  );

  return (
    <Modal isOpen onClose={onClose} size="lg" eyebrow="CAMPAIGN" title={campaign ? 'edit campaign' : 'new campaign'}>
      <form onSubmit={submit} className="space-y-3">
        {input('name', 'name', { required: true, maxLength: 160 })}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {input('platform_source', 'program / platform', { maxLength: 120 })}
          {input('brief_url', 'brief url', { type: 'url', placeholder: 'https://…' })}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {input('cpm', 'cpm (per 1k views)', { type: 'number', min: 0, step: 'any' })}
          {input('currency', 'currency', { maxLength: 3, placeholder: 'USD' })}
          {input('min_views', 'min views to claim', { type: 'number', min: 0, step: 1 })}
          {input('max_payout', 'max payout / post', { type: 'number', min: 0, step: 'any' })}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {input('starts_at', 'starts', { type: 'date' })}
          {input('ends_at', 'ends', { type: 'date' })}
          {input('donation_pct', 'donation %', { type: 'number', min: 0, max: 100, step: 'any' })}
          <label className="block">
            <span className="readout block mb-1">status</span>
            <select value={form.status} onChange={set('status')} className="input-field w-full text-sm">
              <option value="active">active</option>
              <option value="ended">ended</option>
              <option value="archived">archived</option>
            </select>
          </label>
        </div>
        <label className="block">
          <span className="readout block mb-1">notes</span>
          <textarea value={form.notes} onChange={set('notes')} rows={3} maxLength={4000} className="input-field w-full text-sm" />
        </label>
        {error && <p className="text-danger text-sm">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="btn-ghost px-4 py-2 text-xs">cancel</button>
          <button type="submit" disabled={saving} className="btn-primary px-4 py-2 text-xs">
            {saving && <Loader2 size={14} className="animate-spin" />} save
          </button>
        </div>
      </form>
    </Modal>
  );
}
