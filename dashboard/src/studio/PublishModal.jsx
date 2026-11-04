import React, { useEffect, useState } from 'react';
import { Loader2, Upload } from 'lucide-react';
import Modal from '../components/ui/Modal';
import { listChannels, publishPreview, publishClip } from './api';

const pad = (n) => String(n).padStart(2, '0');
// <input type="datetime-local"> value for a Date, in the browser's time zone.
const toLocalInput = (d) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

// Publish one clip on one channel, now or later. The job goes through the
// server's queue, so a scheduled post goes out even with the dashboard closed.
export default function PublishModal({ clip, onClose, onQueued }) {
  const [channels, setChannels] = useState(null);
  const [channelId, setChannelId] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [when, setWhen] = useState('now');
  const [at, setAt] = useState(() => toLocalInput(new Date(Date.now() + 60 * 60 * 1000)));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    listChannels().then((d) => {
      const usable = (d.channels || []).filter((c) => c.publisher !== 'manual');
      setChannels(usable);
      if (usable.length === 1) setChannelId(usable[0].id);
    }).catch(() => setChannels([]));
  }, []);

  useEffect(() => {
    if (!channelId) return;
    publishPreview(clip.id, channelId)
      .then((d) => { setTitle(d.title || ''); setDescription(d.description || ''); })
      .catch(() => {});
  }, [clip.id, channelId]);

  const channel = (channels || []).find((c) => c.id === channelId);
  const notReady = channel && channel.publisher === 'youtube_api' && !channel.youtube_connected;

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    let scheduledAt = null;
    if (when === 'later') {
      const d = new Date(at);
      if (Number.isNaN(d.getTime())) { setError('pick a date and time'); return; }
      scheduledAt = d.toISOString();
    }
    setSaving(true);
    const body = { clip_id: clip.id, channel_id: channelId, title, description, scheduled_at: scheduledAt };
    try {
      let res;
      try {
        res = await publishClip(body);
      } catch (err) {
        if (!['not_approved', 'duplicate_on_channel'].includes(err?.code)) throw err;
        if (!window.confirm(`${err.message}\n\nPublish anyway?`)) { setSaving(false); return; }
        res = await publishClip({ ...body, force: true });
      }
      onQueued?.(res.job);
    } catch (err) {
      setError(err?.detail || 'could not queue the publish.');
      setSaving(false);
    }
  };

  return (
    <Modal isOpen onClose={onClose} size="lg" eyebrow="PUBLISH" title="publish clip">
      <form onSubmit={submit} className="space-y-3">
        <p className="readout truncate">{clip.title || `Clip ${clip.clip_index + 1}`}</p>
        <label className="block">
          <span className="readout block mb-1">channel</span>
          {channels === null ? <Loader2 size={16} className="animate-spin text-brass" /> : (
            <select value={channelId} onChange={(e) => setChannelId(e.target.value)} required className="input-field w-full text-sm">
              <option value="" disabled>
                {channels.length ? 'choose a channel' : 'no channel publishes automatically yet (channels tab)'}
              </option>
              {channels.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} · {c.platform} · {c.publisher === 'youtube_api' ? 'youtube direct' : 'upload-post'}
                  {c.publisher === 'youtube_api' && !c.youtube_connected ? ' (not connected)' : ''}
                </option>
              ))}
            </select>
          )}
        </label>
        {notReady && <p className="text-xs text-danger">Connect this channel to YouTube first (channels tab).</p>}

        <label className="block">
          <span className="readout block mb-1">title {channel?.platform === 'youtube' && <>· {title.length}/100</>}</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} className="input-field w-full text-sm" />
        </label>
        <label className="block">
          <span className="readout block mb-1">description / caption</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={5} maxLength={5000}
            className="input-field w-full text-sm" />
        </label>

        <fieldset className="flex flex-col sm:flex-row sm:items-center gap-3">
          <legend className="readout mb-1">when</legend>
          <label className="flex items-center gap-1.5 text-sm text-ink2 cursor-pointer">
            <input type="radio" name="when" checked={when === 'now'} onChange={() => setWhen('now')} /> now
          </label>
          <label className="flex items-center gap-1.5 text-sm text-ink2 cursor-pointer">
            <input type="radio" name="when" checked={when === 'later'} onChange={() => setWhen('later')} /> schedule
          </label>
          {when === 'later' && (
            <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)}
              className="input-field text-sm sm:w-auto" aria-label="Publish at" />
          )}
        </fieldset>

        {error && <p className="text-danger text-sm">{error}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="btn-ghost px-4 py-2 text-xs">cancel</button>
          <button type="submit" disabled={saving || !channelId || notReady} className="btn-primary px-4 py-2 text-xs">
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
            {when === 'now' ? 'publish now' : 'schedule'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
