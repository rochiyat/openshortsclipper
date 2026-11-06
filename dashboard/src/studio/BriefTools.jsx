import React, { useState } from 'react';
import { Loader2, Sparkles, Check, AlertTriangle, ExternalLink, Plus } from 'lucide-react';
import Modal from '../components/ui/Modal';
import KontenAccount from './KontenAccount';
import {
  analyseBrief, readCampaignList, recommendCampaigns, createCampaign, applyVideoRequirements,
  formatMoney, formatCount,
} from './api';

// Brief → campaign fields, rules and video requirements; campaign lists →
// a ranking for what the creator wants (studio/campaign_ai.py). Everything
// shown here is a suggestion the user saves or not.

function SourceInput({ url, setUrl, text, setText, urlPlaceholder }) {
  return (
    <div className="space-y-2">
      <KontenAccount />
      <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={urlPlaceholder}
        className="input-field w-full text-sm" />
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={5}
        placeholder="…or paste the page's text here (works without the browser)"
        className="input-field w-full text-sm" />
    </div>
  );
}

const List = ({ label, items }) => (items?.length ? (
  <div>
    <p className="readout mb-0.5">{label}</p>
    <ul className="text-xs text-ink2 list-disc ml-4 space-y-0.5">{items.map((x) => <li key={x}>{x}</li>)}</ul>
  </div>
) : null);

export function BriefSummary({ brief, currency }) {
  if (!brief) return null;
  const cur = brief.currency || currency || 'USD';
  return (
    <div className="space-y-2">
      {!brief.is_brief && (
        <p className="badge-danger"><AlertTriangle size={12} /> this does not look like a campaign brief (a login page?)</p>
      )}
      {brief.summary && <p className="text-sm text-ink2">{brief.summary}</p>}
      <p className="readout">
        {brief.cpm != null ? `${formatMoney(brief.cpm, cur)} / 1k views` : 'rate not stated'}
        {brief.min_views != null && ` · min ${formatCount(brief.min_views)} views`}
        {brief.max_payout != null && ` · cap ${formatMoney(brief.max_payout, cur)} / post`}
        {brief.budget != null && ` · budget ${formatMoney(brief.budget, cur)}`}
        {(brief.starts_at || brief.ends_at) && ` · ${brief.starts_at || '…'} → ${brief.ends_at || '…'}`}
      </p>
      <p className="readout">
        {brief.platforms?.length ? brief.platforms.join(', ') : 'platforms not stated'}
        {brief.language && ` · language ${brief.language}`}
        {(brief.min_seconds || brief.max_seconds) && ` · ${brief.min_seconds || 0}-${brief.max_seconds || '∞'} s`}
        {brief.niche && ` · ${brief.niche}`}
      </p>
      <List label="must include" items={brief.must_include} />
      <List label="must avoid" items={brief.must_avoid} />
      <List label="required hashtags / mentions" items={[...(brief.required_hashtags || []), ...(brief.required_mentions || [])]} />
      {brief.required_link && <p className="readout">link: {brief.required_link}</p>}
      {brief.submission && <p className="readout">submit: {brief.submission}</p>}
      <List label="sources to clip" items={brief.source_links} />
      <List label="unclear: ask the campaign" items={brief.unclear} />
    </div>
  );
}

export function VideoRequirementsButton({ spec }) {
  const [done, setDone] = useState(false);
  if (!spec || !Object.values(spec).some(Boolean)) return null;
  return (
    <button type="button" className="btn-ghost px-3 py-2 text-xs"
      title="The next job in the Clip Generator uses these rules, link, CTA and caption text"
      onClick={() => setDone(applyVideoRequirements(spec))}>
      {done ? <Check size={14} /> : <Sparkles size={14} />} {done ? 'set for the next job' : 'use for next job'}
    </button>
  );
}

// Analyse a brief; onUse(result) receives {brief, campaign, video_requirements}.
export function BriefModal({ initialUrl = '', geminiKey, onClose, onUse, useLabel = 'create campaign' }) {
  const [url, setUrl] = useState(initialUrl);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  const run = async () => {
    setBusy(true); setError(''); setResult(null);
    try {
      setResult(await analyseBrief({ url: url.trim(), text: text.trim() }, geminiKey));
    } catch (e) {
      setError(e?.detail || 'could not analyse the brief.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal isOpen onClose={onClose} size="lg" eyebrow="CAMPAIGN BRIEF" title="analyse a brief">
      <div className="space-y-4">
        <SourceInput url={url} setUrl={setUrl} text={text} setText={setText}
          urlPlaceholder="https://konten.com/… (the campaign's page)" />
        <button type="button" disabled={busy || (!url.trim() && !text.trim())} onClick={run} className="btn-primary px-4 py-2 text-xs">
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} analyse
        </button>
        {busy && !text.trim() && <p className="readout">a chrome window opens the page and closes by itself.</p>}
        {error && <p className="text-danger text-sm">{error}</p>}
        {result && (
          <div className="card p-4 space-y-3">
            <BriefSummary brief={result.brief} />
            <p className="readout">read by a model: check the numbers against the brief before you rely on them.</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => onUse(result)} className="btn-primary px-3 py-2 text-xs"><Check size={14} /> {useLabel}</button>
              <VideoRequirementsButton spec={result.video_requirements} />
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function CampaignFinder({ geminiKey, onClose, onAdded }) {
  const [prompt, setPrompt] = useState(() => {
    try { return localStorage.getItem('os_campaign_prompt') || ''; } catch { return ''; }
  });
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const [candidates, setCandidates] = useState([]);
  const [ranked, setRanked] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [added, setAdded] = useState({});

  const readList = async () => {
    setBusy('list'); setError('');
    try {
      const d = await readCampaignList({ url: url.trim(), text: text.trim() }, geminiKey);
      setCandidates(d.campaigns || []);
      if (!d.campaigns?.length) setError('no campaign found on that page.');
    } catch (e) {
      setError(e?.detail || 'could not read the list.');
    } finally {
      setBusy('');
    }
  };

  const recommend = async () => {
    setBusy('rank'); setError('');
    try { localStorage.setItem('os_campaign_prompt', prompt); } catch { /* private mode */ }
    try {
      const d = await recommendCampaigns({ prompt, candidates }, geminiKey);
      setRanked(d.ranked || []);
    } catch (e) {
      setError(e?.detail || 'could not rank the campaigns.');
    } finally {
      setBusy('');
    }
  };

  const add = async (c) => {
    setBusy(`add-${c.id}`); setError('');
    try {
      await createCampaign({
        name: c.name, cpm: c.cpm ?? 0, currency: c.currency || 'USD', min_views: c.min_views ?? 0,
        ends_at: c.ends_at || null, brief_url: c.url || null, platform_source: 'konten.com',
        brief: { is_brief: true, name: c.name, summary: c.summary, cpm: c.cpm, currency: c.currency,
                 min_views: c.min_views, ends_at: c.ends_at, platforms: c.platforms, niche: c.niche },
      });
      setAdded((a) => ({ ...a, [c.id]: true }));
      onAdded?.();
    } catch (e) {
      setError(e?.detail || 'could not add the campaign.');
    } finally {
      setBusy('');
    }
  };

  return (
    <Modal isOpen onClose={onClose} size="lg" eyebrow="CAMPAIGNS" title="find campaigns for you">
      <div className="space-y-4">
        <label className="block">
          <span className="readout block mb-1">about you</span>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
            placeholder="e.g. 2 youtube channels in indonesian: business podcasts and motivation, ~5k views a short"
            className="input-field w-full text-sm" />
        </label>
        <details className="text-sm">
          <summary className="readout cursor-pointer">add campaigns from a list page (optional; your saved active campaigns are always ranked)</summary>
          <div className="mt-2 space-y-2">
            <SourceInput url={url} setUrl={setUrl} text={text} setText={setText}
              urlPlaceholder="https://konten.com/… (the page that lists campaigns)" />
            <button type="button" disabled={!!busy || (!url.trim() && !text.trim())} onClick={readList} className="btn-ghost px-3 py-2 text-xs">
              {busy === 'list' && <Loader2 size={14} className="animate-spin" />} read the list
            </button>
            {candidates.length > 0 && <p className="readout">{candidates.length} campaign{candidates.length === 1 ? '' : 's'} read from the page.</p>}
          </div>
        </details>
        <button type="button" disabled={!!busy || !prompt.trim()} onClick={recommend} className="btn-primary px-4 py-2 text-xs">
          {busy === 'rank' ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} recommend
        </button>
        {error && <p className="text-danger text-sm">{error}</p>}

        {ranked && ranked.length === 0 && <p className="readout">nothing to rank.</p>}
        {ranked && ranked.map((c) => (
          <div key={c.id} className={`card p-3 ${c.ended ? 'opacity-60' : ''}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm text-ink font-medium break-words">{c.name}</p>
                <p className="readout">
                  {c.cpm != null ? `${formatMoney(c.cpm, c.currency || 'USD')} / 1k` : 'rate unknown'}
                  {c.min_views ? ` · min ${formatCount(c.min_views)}` : ''}
                  {c.expected.days_left != null && ` · ${c.expected.days_left < 0 ? 'ended' : `${c.expected.days_left} days left`}`}
                  {c.platforms?.length ? ` · ${c.platforms.join(', ')}` : ''}
                </p>
              </div>
              <span className="badge-brass shrink-0">fit {c.fit}</span>
            </div>
            <p className="text-xs text-ink2 mt-1.5">
              {c.expected.payout_per_post != null
                ? <>a typical post of yours ({formatCount(c.expected.median_views)} views) earns about <b>{formatMoney(c.expected.payout_per_post, c.currency || 'USD')}</b>
                    {c.expected.reaches_minimum === false && ' — under the minimum'}</>
                : 'no view history on its platforms yet: record posts in Studio to estimate earnings.'}
            </p>
            {c.reasons?.length > 0 && <ul className="text-xs text-ink2 list-disc ml-4 mt-1">{c.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
            {c.risks?.length > 0 && <p className="text-xs text-warn mt-1">risks: {c.risks.join('; ')}</p>}
            <div className="flex gap-2 mt-2">
              {c.url && <a href={c.url} target="_blank" rel="noopener noreferrer" className="readout text-brass hover:text-ink inline-flex items-center gap-1"><ExternalLink size={12} /> open</a>}
              {!c.saved && (
                <button type="button" disabled={!!busy || added[c.id]} onClick={() => add(c)} className="btn-ghost px-3 py-1 text-xs">
                  {added[c.id] ? <Check size={12} /> : <Plus size={12} />} {added[c.id] ? 'added' : 'add to studio'}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}
