import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import Modal from '../components/ui/Modal';
import { createPost, listChannels, listClips, listCampaigns } from './api';

// Paste the URL of a clip you published by hand. The server recognises the
// platform, refuses a URL already recorded, and asks before recording an
// unapproved clip or the same file twice on one channel.
export default function RecordPostModal({ clip: presetClip, onClose, onSaved }) {
  const [clips, setClips] = useState(presetClip ? [presetClip] : null);
  const [channels, setChannels] = useState(null);
  const [campaigns, setCampaigns] = useState([]);
  const [form, setForm] = useState({
    clip_id: presetClip?.id || '',
    channel_id: '',
    url: '',
    posted_at: new Date().toISOString().slice(0, 10),
    campaign_id: '__project__',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    listChannels().then((d) => {
      setChannels(d.channels || []);
      if (d.channels?.length === 1) setForm((f) => ({ ...f, channel_id: d.channels[0].id }));
    }).catch(() => setChannels([]));
    listCampaigns('active').then((d) => setCampaigns(d.campaigns || [])).catch(() => {});
    if (!presetClip) {
      Promise.all([
        listClips({ reviewStatus: 'approved', limit: 500 }),
        listClips({ reviewStatus: 'posted', limit: 500 }),
      ]).then(([a, p]) => setClips([...(a.clips || []), ...(p.clips || [])]))
        .catch(() => setClips([]));
    }
  }, [presetClip]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    const body = {
      clip_id: form.clip_id,
      channel_id: form.channel_id,
      url: form.url,
      posted_at: form.posted_at || null,
    };
    if (form.campaign_id !== '__project__') body.campaign_id = form.campaign_id || null;
    try {
      let res;
      try {
        res = await createPost(body);
      } catch (err) {
        if (!['not_approved', 'duplicate_on_channel'].includes(err?.code)) throw err;
        if (!window.confirm(`${err.message}\n\nRecord it anyway?`)) { setSaving(false); return; }
        res = await createPost({ ...body, force: true });
      }
      onSaved?.(res.post);
    } catch (err) {
      setError(err?.detail || 'could not record the post.');
      setSaving(false);
    }
  };

  const selectedClip = (clips || []).find((c) => c.id === form.clip_id);

  return (
    <Modal isOpen onClose={onClose} size="lg" eyebrow="POST" title="record a post">
      <form onSubmit={submit} className="space-y-3">
        <label className="block">
          <span className="readout block mb-1">clip</span>
          {clips === null ? <Loader2 size={16} className="animate-spin text-brass" /> : (
            <select value={form.clip_id} onChange={set('clip_id')} required disabled={!!presetClip}
              className="input-field w-full text-sm">
              <option value="" disabled>{clips.length ? 'choose an approved clip' : 'no approved clips yet'}</option>
              {clips.map((c) => (
                <option key={c.id} value={c.id}>
                  {(c.title || `Clip ${c.clip_index + 1}`).slice(0, 70)}{c.project ? ` — ${c.project.title.slice(0, 30)}` : ''}
                  {c.review_status === 'posted' ? ' (posted)' : ''}
                </option>
              ))}
            </select>
          )}
        </label>

        <label className="block">
          <span className="readout block mb-1">channel</span>
          {channels === null ? <Loader2 size={16} className="animate-spin text-brass" /> : (
            <select value={form.channel_id} onChange={set('channel_id')} required className="input-field w-full text-sm">
              <option value="" disabled>{channels.length ? 'choose a channel' : 'add a channel first (channels tab)'}</option>
              {channels.map((c) => (
                <option key={c.id} value={c.id}>{c.name} · {c.platform}{c.handle ? ` · @${c.handle}` : ''}</option>
              ))}
            </select>
          )}
        </label>

        <label className="block">
          <span className="readout block mb-1">post url</span>
          <input type="url" value={form.url} onChange={set('url')} required placeholder="https://www.youtube.com/shorts/…"
            className="input-field w-full text-sm" />
        </label>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="block">
            <span className="readout block mb-1">posted on</span>
            <input type="date" value={form.posted_at} onChange={set('posted_at')} className="input-field w-full text-sm" />
          </label>
          <label className="block">
            <span className="readout block mb-1">campaign</span>
            <select value={form.campaign_id} onChange={set('campaign_id')} className="input-field w-full text-sm">
              <option value="__project__">
                {selectedClip?.project?.campaign_name ? `project's: ${selectedClip.project.campaign_name}` : "project's (none)"}
              </option>
              <option value="">none</option>
              {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        </div>

        {error && <p className="text-danger text-sm">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="btn-ghost px-4 py-2 text-xs">cancel</button>
          <button type="submit" disabled={saving || !form.clip_id || !form.channel_id} className="btn-primary px-4 py-2 text-xs">
            {saving && <Loader2 size={14} className="animate-spin" />} record post
          </button>
        </div>
      </form>
    </Modal>
  );
}
