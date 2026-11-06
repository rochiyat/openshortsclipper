import React, { useEffect, useState } from 'react';
import { Plus, Trash2, LogIn, Loader2, Pencil, ChevronDown } from 'lucide-react';
import { apiFetch } from '../lib/api';
import { accountTargets, targetKey } from '../lib/browserTargets';

// Where a browser post goes: the YouTube and TikTok accounts saved on this machine and, on
// YouTube, their channels. Each account is a browser profile someone logged
// in to by hand (see docs/architecture/browser-posting.md).

async function send(path, method, body) {
    const res = await apiFetch(path, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
        let detail = await res.text();
        try { detail = JSON.parse(detail).detail || detail; } catch { /* not JSON */ }
        throw new Error(detail);
    }
    return res.json();
}

export default function BrowserAccountsPicker({ accounts, onReload, selected, onSelectedChange, overrides, onOverridesChange, defaultTitle }) {
    const [newLabel, setNewLabel] = useState('');
    const [newPlatform, setNewPlatform] = useState('youtube');
    const [busy, setBusy] = useState(null);
    const [error, setError] = useState(null);
    const [manualFor, setManualFor] = useState(null);
    const [manualId, setManualId] = useState('');
    const [manualName, setManualName] = useState('');
    const [openOverride, setOpenOverride] = useState(null);

    // A login window reports back when the person closes it.
    const loginOpen = accounts.some((a) => a.login?.status === 'open');
    useEffect(() => {
        if (!loginOpen) return undefined;
        const t = setInterval(onReload, 3000);
        return () => clearInterval(t);
    }, [loginOpen, onReload]);

    const run = async (key, fn) => {
        setBusy(key);
        setError(null);
        try {
            await fn();
            await onReload();
        } catch (e) {
            setError(e.message);
        } finally {
            setBusy(null);
        }
    };

    const addAccount = () => run('add', async () => {
        await send('/api/social/browser/accounts', 'POST', { platform: newPlatform, label: newLabel.trim() || undefined });
        setNewLabel('');
    });

    const removeAccount = (a) => {
        if (!window.confirm(`Delete "${a.label}"? Its browser login on this computer is deleted too.`)) return;
        onSelectedChange(selected.filter((t) => t.account_id !== a.id));
        run(`del-${a.id}`, () => send(`/api/social/browser/accounts/${encodeURIComponent(a.id)}`, 'DELETE'));
    };

    const saveChannels = (a, channels) => run(`ch-${a.id}`, () => send(
        `/api/social/browser/accounts/${encodeURIComponent(a.id)}`, 'PATCH', { channels }));

    const addChannel = (a) => saveChannels(a, [...(a.channels || []), { id: manualId.trim(), name: manualName.trim() || undefined }])
        .then(() => { setManualFor(null); setManualId(''); setManualName(''); });

    const renameChannel = (a, c) => {
        const name = window.prompt('Channel name', c.name);
        if (name === null) return;
        saveChannels(a, a.channels.map((x) => (x.id === c.id ? { ...x, name } : x)));
    };

    const removeChannel = (a, c) => {
        onSelectedChange(selected.filter((t) => !(t.account_id === a.id && t.channel_id === c.id)));
        saveChannels(a, a.channels.filter((x) => x.id !== c.id));
    };

    const toggle = (t) => {
        const key = targetKey(t);
        onSelectedChange(selected.some((s) => targetKey(s) === key)
            ? selected.filter((s) => targetKey(s) !== key)
            : [...selected, { account_id: t.account_id, channel_id: t.channel_id }]);
    };

    const setOverride = (key, field, value) => onOverridesChange({ ...overrides, [key]: { ...overrides[key], [field]: value } });

    return (
        <div className="space-y-3">
            {accounts.length === 0 && (
                <p className="text-xs text-muted lowercase">no account yet: add one and log in to it in the window that opens.</p>
            )}

            {accounts.map((a) => (
                <div key={a.id} className="p-3 bg-paper rounded-input border border-rule space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                            <div className="text-sm text-ink2 truncate">{a.label}</div>
                            <div className="text-[11px] text-muted lowercase">
                                {a.platform}
                                {a.login?.status === 'open' && ' · login window open: close it when done'}
                                {a.login?.status !== 'open' && a.login?.message && ` · ${a.login.message}`}
                            </div>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                            <button
                                type="button"
                                title={a.platform === 'youtube'
                                    ? 'open the login window: log in, visit each channel in YouTube Studio, then close it'
                                    : 'open the login window: log in, then close it'}
                                disabled={!!busy || a.login?.status === 'open'}
                                onClick={() => run(`login-${a.id}`, () => send(`/api/social/browser/accounts/${encodeURIComponent(a.id)}/login`, 'POST'))}
                                className="p-1.5 rounded-input text-muted hover:text-brass hover:bg-paper3 disabled:opacity-45"
                            >
                                {a.login?.status === 'open' || busy === `login-${a.id}` ? <Loader2 size={14} className="animate-spin" /> : <LogIn size={14} />}
                            </button>
                            <button
                                type="button"
                                title="delete this account"
                                disabled={!!busy || a.login?.status === 'open'}
                                onClick={() => removeAccount(a)}
                                className="p-1.5 rounded-input text-muted hover:text-danger hover:bg-paper3 disabled:opacity-45"
                            >
                                <Trash2 size={14} />
                            </button>
                        </div>
                    </div>

                    {a.has_profile && accountTargets(a).map((t) => {
                        const key = targetKey(t);
                        const checked = selected.some((s) => targetKey(s) === key);
                        const channel = (a.channels || []).find((c) => c.id === t.channel_id);
                        return (
                            <div key={key}>
                                <div className="flex items-center gap-2">
                                    <label className="flex items-center gap-2 cursor-pointer min-w-0 flex-1">
                                        <input type="checkbox" checked={checked} onChange={() => toggle(t)} className="w-4 h-4 accent-brass cursor-pointer shrink-0" />
                                        <span className="text-sm text-ink2 truncate">{t.label}</span>
                                    </label>
                                    {checked && (
                                        <button type="button" title="own title or caption for this destination" onClick={() => setOpenOverride(openOverride === key ? null : key)} className="p-1 text-muted hover:text-brass">
                                            <ChevronDown size={14} className={openOverride === key ? 'rotate-180' : ''} />
                                        </button>
                                    )}
                                    {channel && (
                                        <>
                                            <button type="button" title="rename" disabled={!!busy} onClick={() => renameChannel(a, channel)} className="p-1 text-muted hover:text-brass disabled:opacity-45"><Pencil size={12} /></button>
                                            <button type="button" title="remove this channel" disabled={!!busy} onClick={() => removeChannel(a, channel)} className="p-1 text-muted hover:text-danger disabled:opacity-45"><Trash2 size={12} /></button>
                                        </>
                                    )}
                                </div>
                                {checked && openOverride === key && (
                                    <div className="mt-2 ml-6 space-y-2">
                                        <input
                                            type="text"
                                            value={overrides[key]?.title || ''}
                                            onChange={(e) => setOverride(key, 'title', e.target.value)}
                                            className="input-field"
                                            placeholder={`title (default: ${defaultTitle || 'the post title'})`}
                                        />
                                        <textarea
                                            value={overrides[key]?.description || ''}
                                            onChange={(e) => setOverride(key, 'description', e.target.value)}
                                            rows={2}
                                            className="input-field resize-none"
                                            placeholder="caption (default: the post caption)"
                                        />
                                    </div>
                                )}
                            </div>
                        );
                    })}

                    {a.platform !== 'youtube' ? null : manualFor === a.id ? (
                        <div className="space-y-2">
                            <input type="text" value={manualId} onChange={(e) => setManualId(e.target.value)} className="input-field" placeholder="channel id (UC…) or its studio / channel url" />
                            <input type="text" value={manualName} onChange={(e) => setManualName(e.target.value)} className="input-field" placeholder="name (optional)" />
                            <div className="flex gap-2">
                                <button type="button" disabled={!manualId.trim() || !!busy} onClick={() => addChannel(a)} className="btn-quiet text-xs px-3 py-1.5 disabled:opacity-45">add channel</button>
                                <button type="button" onClick={() => setManualFor(null)} className="text-xs text-muted hover:text-ink2 lowercase">cancel</button>
                            </div>
                        </div>
                    ) : (
                        <button type="button" onClick={() => setManualFor(a.id)} className="text-xs lowercase text-muted hover:text-brass underline underline-offset-2">
                            add a channel by hand
                        </button>
                    )}
                </div>
            ))}

            <div className="flex gap-2">
                <select value={newPlatform} onChange={(e) => setNewPlatform(e.target.value)} className="input-field w-auto shrink-0" aria-label="platform">
                    <option value="youtube">youtube</option>
                    <option value="tiktok">tiktok</option>
                </select>
                <input type="text" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} className="input-field" placeholder="new account name, e.g. main google account" />
                <button type="button" disabled={!!busy} onClick={addAccount} className="btn-quiet shrink-0 text-xs px-3 disabled:opacity-45">
                    {busy === 'add' ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} add
                </button>
            </div>
            <p className="text-[11px] text-muted leading-snug lowercase">
                {newPlatform === 'youtube'
                    ? 'a chrome window opens: log in, then open youtube studio on each channel you want (avatar → switch account), and close the window. the channels you visited are added here.'
                    : 'a chrome window opens: log in to tiktok, then close the window.'}
            </p>

            {error && <div className="badge-danger">{error}</div>}
        </div>
    );
}
