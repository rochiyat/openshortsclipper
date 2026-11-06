import React, { useEffect, useState } from 'react';
import { Loader2, TrendingUp, AlertTriangle, Lightbulb } from 'lucide-react';
import { revenueInsights, formatMoney, formatCount } from './api';

// Earnings per campaign and what to do next (studio/revenue.py). Plain
// arithmetic on recorded posts and their latest view counts.
const PRIORITY = { 1: 'badge-danger', 2: 'badge-brass', 3: 'badge-ok' };
const PRIORITY_LABEL = { 1: 'now', 2: 'soon', 3: 'idea' };

export default function RevenuePage({ onGo }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    revenueInsights().then(setData).catch((e) => setError(e?.detail || 'could not load earnings.'));
  }, []);

  if (error) return <p className="text-danger text-sm">{error}</p>;
  if (!data) return <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>;

  const currencies = Object.entries(data.totals);
  return (
    <div className="animate-fade space-y-6">
      <p className="text-muted text-sm lowercase">
        estimated from recorded posts and their latest view counts, with each campaign&apos;s rate, minimum and cap.
        record posts and refresh views in <button type="button" className="underline" onClick={() => onGo?.('posts')}>posts</button> to keep it true.
      </p>

      {currencies.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {currencies.map(([cur, t]) => (
            <React.Fragment key={cur}>
              <div className="card p-4">
                <p className="text-lg text-ink font-medium tabular-nums">{formatMoney(t.payout, cur)}</p>
                <p className="readout">est. payout, active campaigns</p>
              </div>
              <div className="card p-4">
                <p className="text-lg text-ink font-medium tabular-nums">{formatMoney(t.net, cur)}</p>
                <p className="readout">net after donation</p>
              </div>
              <div className="card p-4">
                <p className={`text-lg font-medium tabular-nums ${t.unclaimed > 0 ? 'text-brass' : 'text-ink'}`}>{formatMoney(t.unclaimed, cur)}</p>
                <p className="readout">eligible but not submitted</p>
              </div>
            </React.Fragment>
          ))}
        </div>
      )}

      <section>
        <p className="eyebrow mb-2 flex items-center gap-1.5"><Lightbulb size={12} /> WHAT TO DO NEXT</p>
        {data.recommendations.length === 0 && <p className="readout">nothing to act on yet: record posts in a campaign to get advice.</p>}
        <div className="space-y-2">
          {data.recommendations.map((r, i) => (
            <div key={`${r.kind}-${r.campaign_id || ''}-${i}`} className="card p-3 flex items-start gap-3">
              <span className={`${PRIORITY[r.priority]} shrink-0`}>{PRIORITY_LABEL[r.priority]}</span>
              <p className="text-sm text-ink2">{r.message}</p>
            </div>
          ))}
        </div>
      </section>

      <section>
        <p className="eyebrow mb-2 flex items-center gap-1.5"><TrendingUp size={12} /> CAMPAIGNS</p>
        {data.campaigns.length === 0 ? <p className="readout">no campaign yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left readout">
                  <th className="py-1.5 pr-3">campaign</th><th className="pr-3">posts</th><th className="pr-3">views</th>
                  <th className="pr-3">payout</th><th className="pr-3">per post</th><th className="pr-3">not submitted</th><th>days left</th>
                </tr>
              </thead>
              <tbody>
                {data.campaigns.map((c) => (
                  <tr key={c.campaign_id} className={`border-t border-rule ${c.status !== 'active' ? 'opacity-60' : ''}`}>
                    <td className="py-1.5 pr-3 text-ink">{c.name}</td>
                    <td className="pr-3 tabular-nums">{c.eligible_posts}/{c.posts}</td>
                    <td className="pr-3 tabular-nums">{formatCount(c.views)}</td>
                    <td className="pr-3 tabular-nums">{formatMoney(c.payout, c.currency)}</td>
                    <td className="pr-3 tabular-nums">{c.payout_per_post == null ? '—' : formatMoney(c.payout_per_post, c.currency)}</td>
                    <td className="pr-3 tabular-nums">
                      {c.unsubmitted_posts > 0
                        ? <span className="text-brass inline-flex items-center gap-1"><AlertTriangle size={11} /> {c.unsubmitted_posts} · {formatMoney(c.unclaimed_payout, c.currency)}</span>
                        : '—'}
                    </td>
                    <td className="tabular-nums">{c.days_left == null ? '—' : c.days_left < 0 ? 'ended' : c.days_left}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="readout mt-1">posts: eligible (past the minimum) / all.</p>
          </div>
        )}
      </section>

      {(data.channels.length > 0 || data.lengths.length > 0) && (
        <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p className="eyebrow mb-2">MEDIAN VIEWS PER CHANNEL</p>
            {data.channels.map((c) => (
              <p key={`${c.channel}-${c.platform}`} className="text-xs text-ink2 flex justify-between border-t border-rule py-1.5">
                <span>{c.channel} · {c.platform}</span><span className="tabular-nums">{formatCount(c.median_views)} · {c.posts} posts</span>
              </p>
            ))}
          </div>
          <div>
            <p className="eyebrow mb-2">MEDIAN VIEWS PER CLIP LENGTH</p>
            {data.lengths.map((l) => (
              <p key={l.length} className="text-xs text-ink2 flex justify-between border-t border-rule py-1.5">
                <span>{l.length}</span><span className="tabular-nums">{formatCount(l.median_views)} · {l.posts} posts</span>
              </p>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
