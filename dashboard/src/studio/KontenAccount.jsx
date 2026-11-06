import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, LogIn, Plus } from 'lucide-react';
import { apiFetch } from '../lib/api';

// The konten.com login a brief or a campaign list is read through: a browser
// profile on this machine (browser_accounts.py, platform "konten").
export default function KontenAccount() {
  const [state, setState] = useState(null); // null loading | {enabled, account}
  const [error, setError] = useState('');

  const load = useCallback(() => apiFetch('/api/social/browser/status')
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => setState({
      enabled: !!d?.enabled && !!d?.playwright,
      account: (d?.accounts || []).find((a) => a.platform === 'konten' && a.has_profile) || null,
    }))
    .catch(() => setState({ enabled: false, account: null })), []);
  useEffect(() => { load(); }, [load]);

  const open = state?.account?.login?.status === 'open';
  useEffect(() => {
    if (!open) return undefined;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [open, load]);

  const send = async (path, body) => {
    setError('');
    const r = await apiFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    if (!r.ok) {
      let d = await r.text();
      try { d = JSON.parse(d).detail || d; } catch { /* not JSON */ }
      setError(String(d));
    }
    load();
  };

  if (!state) return null;
  if (!state.enabled) {
    return (
      <p className="readout">
        to read pages from konten.com, run the API on this computer with BROWSER_POST_ENABLED=1 and playwright installed. pasting the text works anywhere.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="readout">
        konten.com: {state.account ? (open ? 'login window open, close it when done' : state.account.label) : 'no account yet'}
      </span>
      {state.account ? (
        <button type="button" disabled={open} onClick={() => send(`/api/social/browser/accounts/${encodeURIComponent(state.account.id)}/login`)}
          className="btn-ghost px-3 py-1 text-xs">
          {open ? <Loader2 size={12} className="animate-spin" /> : <LogIn size={12} />} log in again
        </button>
      ) : (
        <button type="button" onClick={() => send('/api/social/browser/accounts', { platform: 'konten', label: 'konten.com' })}
          className="btn-ghost px-3 py-1 text-xs">
          <Plus size={12} /> add konten.com account
        </button>
      )}
      {error && <span className="text-danger text-xs">{error}</span>}
    </div>
  );
}
