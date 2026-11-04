import React, { useEffect, useState } from 'react';
import { Loader2, Plus, Pencil, Archive, ArchiveRestore, Users } from 'lucide-react';
import Modal from '../components/ui/Modal';
import { listChannels, createChannel, updateChannel, PLATFORMS } from './api';

const EMPTY = {
  name: '', platform: 'tiktok', handle: '', language: '', watermark_text: '',
  default_hashtags: '', caption_suffix: '',
};

const toForm = (c) => ({
  name: c.name || '', platform: c.platform, handle: c.handle || '', language: c.language || '',
  watermark_text: c.watermark_text || '',
  default_hashtags: c.caption_preset?.default_hashtags || '',
  caption_suffix: c.caption_preset?.caption_suffix || '',
});

const toBody = (f) => ({
  name: f.name,
  platform: f.platform,
  handle: f.handle || null,
  language: f.language || null,
  watermark_text: f.watermark_text || null,
  caption_preset: { default_hashtags: f.default_hashtags, caption_suffix: f.caption_suffix },
});

// The user's own accounts. Posting (phase 3) records which channel a clip
// went to; the preset is the default copy added when posting there.
export default function ChannelsPage() {
  const [channels, setChannels] = useState(null);
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(null);

  const load = () => {
    listChannels(showArchived)
      .then((d) => setChannels(d.channels || []))
      .catch(() => setError('could not load channels.'));
  };
  useEffect(load, [showArchived]);

  const toggleArchive = async (c) => {
    try {
      await updateChannel(c.id, { archived: !c.archived });
      load();
    } catch (e) {
      setError(e?.detail || 'could not update the channel.');
    }
  };

  return (
    <div className="animate-fade">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
        <p className="text-muted text-sm lowercase">Your own accounts, with the defaults used when you post to them.</p>
        <div className="flex gap-2 shrink-0">
          <label className="readout flex items-center gap-1 cursor-pointer select-none">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            archived
          </label>
          <button onClick={() => setEditing('new')} className="btn-primary px-3 py-2 text-xs">
            <Plus size={14} /> new channel
          </button>
        </div>
      </div>

      {error && <p className="text-danger text-sm mb-4">{error}</p>}
      {!channels && !error && <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>}
      {channels && channels.length === 0 && (
        <div className="text-center py-20 text-muted">
          <Users size={40} className="mx-auto mb-4 text-muted" />
          <p className="lowercase">no channels yet. Add the accounts you post clips to.</p>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {(channels || []).map((c) => (
          <div key={c.id} className={`card p-4 ${c.archived ? 'opacity-60' : ''}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm text-ink font-medium break-words">{c.name}</p>
                <p className="readout mt-0.5">
                  {c.platform}{c.handle && ` · @${c.handle}`}{c.language && ` · ${c.language}`}
                </p>
              </div>
              <span className="badge-brass">{c.publisher}</span>
            </div>
            {c.caption_preset?.default_hashtags && <p className="text-xs text-ink2 mt-2 break-words">{c.caption_preset.default_hashtags}</p>}
            {c.caption_preset?.caption_suffix && <p className="text-xs text-muted mt-1 break-words line-clamp-2">{c.caption_preset.caption_suffix}</p>}
            {c.watermark_text && <p className="readout mt-1">watermark: {c.watermark_text}</p>}
            <div className="flex gap-2 mt-3">
              <button onClick={() => setEditing(c)} className="btn-ghost px-3 py-2 text-xs"><Pencil size={14} /> edit</button>
              <button onClick={() => toggleArchive(c)} className="btn-ghost px-3 py-2 text-xs">
                {c.archived ? <><ArchiveRestore size={14} /> restore</> : <><Archive size={14} /> archive</>}
              </button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <ChannelForm
          channel={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); load(); }}
        />
      )}
    </div>
  );
}

function ChannelForm({ channel, onClose, onSaved }) {
  const [form, setForm] = useState(channel ? toForm(channel) : EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      if (channel) await updateChannel(channel.id, toBody(form));
      else await createChannel(toBody(form));
      onSaved();
    } catch (err) {
      setError(err?.detail || 'could not save the channel.');
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
    <Modal isOpen onClose={onClose} size="lg" eyebrow="CHANNEL" title={channel ? 'edit channel' : 'new channel'}>
      <form onSubmit={submit} className="space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {input('name', 'name', { required: true, maxLength: 120 })}
          <label className="block">
            <span className="readout block mb-1">platform</span>
            <select value={form.platform} onChange={set('platform')} className="input-field w-full text-sm">
              {PLATFORMS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {input('handle', 'handle', { maxLength: 120, placeholder: '@account' })}
          {input('language', 'language', { maxLength: 16, placeholder: 'id, en…' })}
          {input('watermark_text', 'watermark text', { maxLength: 120 })}
        </div>
        {input('default_hashtags', 'default hashtags', { maxLength: 2000, placeholder: '#fyp #belajar' })}
        <label className="block">
          <span className="readout block mb-1">caption suffix (cta)</span>
          <textarea value={form.caption_suffix} onChange={set('caption_suffix')} rows={2} maxLength={2000}
            className="input-field w-full text-sm" />
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
