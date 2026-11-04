import React, { useEffect, useState } from 'react';
import { Loader2, AlertTriangle, ExternalLink, HardDriveUpload, RefreshCw } from 'lucide-react';
import { studioSummary, backupStatus, runBackup, formatCount, formatMoney } from './api';

const fmtWhen = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
}) : '—');

// The home of the Projects tab: growth, money, and what needs a look.
export default function OverviewPage({ onGo }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    studioSummary().then(setData).catch(() => setError('could not load the overview.'));
  }, []);

  if (error) return <p className="text-danger text-sm">{error}</p>;
  if (!data) return <div className="flex justify-center py-20"><Loader2 className="animate-spin text-brass" /></div>;

  // A percentage over a tiny base (a first week of 1,200 views) says nothing.
  const change = data.views_prev_7d >= 1000
    ? Math.round(((data.views_7d - data.views_prev_7d) / data.views_prev_7d) * 100)
    : null;
  const changeNote = change != null
    ? `${change >= 0 ? '+' : ''}${change}% vs previous 7 days`
    : (data.views_prev_7d > 0 ? `previous 7 days: ${formatCount(data.views_prev_7d)}` : 'no earlier week yet');
  const payoutByCurrency = data.payouts.reduce((acc, p) => {
    acc[p.currency] = (acc[p.currency] || 0) + p.payout;
    return acc;
  }, {});

  return (
    <div className="animate-fade space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="views · last 7 days" value={formatCount(data.views_7d)}
          note={changeNote} />
        <Stat label="views · today" value={formatCount(data.views_today)} note={`${data.time_zone} day`} />
        <Stat
          label="est. payout · active campaigns"
          value={Object.keys(payoutByCurrency).length
            ? Object.entries(payoutByCurrency).map(([cur, v]) => formatMoney(v, cur)).join(' + ')
            : '—'}
          note={`${data.payouts.length} campaign${data.payouts.length === 1 ? '' : 's'} with posts`}
        />
        <Stat label="to review" value={formatCount(data.review.draft)}
          note={`${data.review.approved} approved · ${data.posts.live} live posts`}
          onClick={data.review.draft ? () => onGo?.('review') : undefined} />
      </div>

      {data.attention.length > 0 && (
        <div className="card p-3">
          <p className="readout mb-2">needs attention</p>
          <ul className="space-y-1.5">
            {data.attention.map((a) => (
              <li key={a.kind} className="text-sm text-ink2 flex items-center gap-2">
                <AlertTriangle size={14} className="text-brass shrink-0" />
                <span><b className="text-ink">{a.count}</b> {a.message}</span>
                <button onClick={() => onGo?.(a.kind === 'rights_unknown' ? 'projects' : 'posts')}
                  className="text-micro font-mono uppercase text-brass hover:text-ink ml-auto shrink-0">open</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="card p-4">
        <div className="flex items-baseline justify-between gap-2 mb-2">
          <p className="readout">views gained per day · last {data.daily.length} days</p>
          {data.snapshots?.last_run && <p className="readout">last daily read {fmtWhen(data.snapshots.last_run)}</p>}
        </div>
        <DailyBars days={data.daily} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-4">
          <p className="readout mb-3">top posts · last 7 days</p>
          {data.top_posts.length === 0 ? <p className="text-sm text-muted lowercase">no views recorded yet.</p> : (
            <ol className="space-y-2.5">
              {data.top_posts.map((p, i) => (
                <li key={p.post_id} className="flex items-center gap-3 text-sm">
                  <span className="readout w-4 shrink-0">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-ink truncate">{p.title || '(clip deleted)'}</p>
                    <p className="readout truncate">{p.channel} · {p.platform}</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-ink tabular-nums">+{formatCount(p.gain_7d)}</p>
                    <p className="readout tabular-nums">{formatCount(p.views)} total</p>
                  </div>
                  {p.url && (
                    <a href={p.url} target="_blank" rel="noopener noreferrer" className="text-muted hover:text-ink shrink-0" title="Open post">
                      <ExternalLink size={14} />
                    </a>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="card p-4">
          <p className="readout mb-3">campaigns · estimated payout</p>
          {data.payouts.length === 0 ? <p className="text-sm text-muted lowercase">no campaign posts yet.</p> : (
            <ul className="space-y-2.5">
              {data.payouts.map((c) => (
                <li key={c.campaign_id} className="flex items-center gap-3 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="text-ink truncate">{c.name}</p>
                    <p className="readout">{c.posts} post{c.posts === 1 ? '' : 's'} · {formatCount(c.views)} views</p>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-ink tabular-nums">{formatMoney(c.payout, c.currency)}</p>
                    {c.donation > 0 && <p className="readout tabular-nums">donate {formatMoney(c.donation, c.currency)}</p>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <BackupCard />
    </div>
  );
}

function Stat({ label, value, note, onClick }) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag onClick={onClick} className={`card p-4 text-left ${onClick ? 'card-hover' : ''}`}>
      <p className="readout">{label}</p>
      <p className="font-display text-2xl text-ink mt-1 tabular-nums break-words">{value}</p>
      {note && <p className="readout mt-1">{note}</p>}
    </Tag>
  );
}

const fmtDay = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const compact = (n) => new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n);

// One series of daily totals: thin bars in the accent colour, 2px gaps,
// rounded tops on a shared baseline, a tooltip per bar on hover or focus.
function DailyBars({ days }) {
  const [hover, setHover] = useState(null);
  const max = Math.max(...days.map((d) => d.views), 1);
  const W = 560;
  const H = 150;
  const top = 12;
  const base = H - 22;
  const slot = W / days.length;
  const bw = Math.max(4, slot - 6);
  if (days.every((d) => d.views === 0)) {
    return <p className="text-sm text-muted lowercase py-8 text-center">no daily view growth recorded yet.</p>;
  }
  return (
    <div className="relative max-w-3xl">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto text-ink" role="img"
        aria-label={`Daily view gains, highest ${compact(max)}`} onMouseLeave={() => setHover(null)}>
        <line x1="0" x2={W} y1={base} y2={base} stroke="var(--color-rule)" strokeWidth="1" />
        <text x="0" y={top - 2} className="fill-current" opacity="0.55" fontSize="10">{compact(max)}</text>
        {days.map((d, i) => {
          const h = (d.views / max) * (base - top);
          const x = i * slot + (slot - bw) / 2;
          const r = Math.min(4, bw / 2, h);
          const y = base - h;
          const path = h <= 0 ? '' : `M${x},${base} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${base} Z`;
          return (
            <g key={d.day} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
              aria-label={`${fmtDay(d.day)}: ${d.views.toLocaleString()} views`}>
              <rect x={i * slot} y={top} width={slot} height={base - top + 20} fill="transparent" />
              {path && <path d={path} fill="var(--color-accent)" opacity={hover == null || hover === i ? 1 : 0.55} />}
              {(i === 0 || i === days.length - 1 || i === Math.floor(days.length / 2)) && (
                <text x={i * slot + slot / 2} y={H - 6} textAnchor="middle" className="fill-current" opacity="0.55" fontSize="10">
                  {fmtDay(d.day)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover != null && (
        <div className="absolute card px-2.5 py-1.5 text-xs pointer-events-none whitespace-nowrap"
          style={{
            left: `${((hover + 0.5) / days.length) * 100}%`, top: 0,
            transform: hover > days.length * 0.6 ? 'translateX(calc(-100% - 8px))' : 'translateX(8px)',
          }}>
          <p className="text-ink font-medium">+{days[hover].views.toLocaleString()} views</p>
          <p className="readout">{fmtDay(days[hover].day)}</p>
        </div>
      )}
    </div>
  );
}

function BackupCard() {
  const [st, setSt] = useState(null);
  const [error, setError] = useState('');

  const load = () => backupStatus().then(setSt).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!st?.running) return undefined;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [st?.running]);

  if (!st) return null;
  const last = st.last;
  return (
    <div className="card p-4 flex flex-col sm:flex-row sm:items-center gap-3">
      <HardDriveUpload size={18} className="text-brass shrink-0" />
      <div className="min-w-0 flex-1 text-sm">
        {!st.configured ? (
          <p className="text-muted">
            Backups are off. Set <code className="text-ink">STUDIO_BACKUP_BUCKET</code> and its keys (R2 or S3) to back up the database and clips
            {st.interval_days > 0 ? ` every ${st.interval_days} days` : ''}.
          </p>
        ) : (
          <>
            <p className="text-ink">
              Backup to <span className="font-mono text-xs">{st.bucket}/{st.prefix}</span>
              {st.interval_days > 0 ? ` · every ${st.interval_days} days` : ' · manual only'}
            </p>
            <p className="readout mt-0.5">
              {!last && 'never backed up yet'}
              {last?.ok && `last ${fmtWhen(last.at)} · ${last.summary.uploaded_files} new file${last.summary.uploaded_files === 1 ? '' : 's'}`}
              {last && !last.ok && <span className="text-danger">last attempt {fmtWhen(last.at)} failed: {last.error}</span>}
            </p>
          </>
        )}
        {error && <p className="text-danger text-xs mt-1">{error}</p>}
      </div>
      {st.configured && (
        <button
          onClick={async () => {
            setError('');
            try { setSt(await runBackup()); } catch (e) { setError(e?.detail || 'could not start the backup.'); }
          }}
          disabled={st.running} className="btn-ghost px-3 py-2 text-xs shrink-0">
          <RefreshCw size={14} className={st.running ? 'animate-spin' : ''} /> {st.running ? 'backing up…' : 'back up now'}
        </button>
      )}
    </div>
  );
}
