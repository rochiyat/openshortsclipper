import React, { useMemo, useRef, useState } from 'react';

const W = 560;
const H = 180;
const PAD = { top: 16, right: 16, bottom: 26, left: 52 };

const compact = (n) => new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n);
const fmtDay = (iso) => new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const fmtFull = (iso) => new Date(iso).toLocaleString(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
});

// Views over time for one post: a single 2px line in the accent colour, a
// recessive baseline, first/last labels only, crosshair + tooltip on hover.
export default function ViewsChart({ snapshots }) {
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);

  const geo = useMemo(() => {
    const pts = (snapshots || []).map((s) => ({ ...s, t: new Date(s.captured_at).getTime() }));
    if (pts.length === 0) return null;
    const t0 = pts[0].t;
    const t1 = pts[pts.length - 1].t;
    const vMax = Math.max(...pts.map((p) => p.views), 1);
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const x = (t) => PAD.left + (t1 === t0 ? innerW / 2 : ((t - t0) / (t1 - t0)) * innerW);
    const y = (v) => PAD.top + innerH - (v / vMax) * innerH;
    const placed = pts.map((p) => ({ ...p, x: x(p.t), y: y(p.views) }));
    const d = placed.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    return { placed, d, vMax, baseY: PAD.top + innerH };
  }, [snapshots]);

  if (!geo) return <p className="readout py-6 text-center">no view snapshots yet.</p>;

  const onMove = (e) => {
    const rect = svgRef.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    let best = geo.placed[0];
    for (const p of geo.placed) if (Math.abs(p.x - px) < Math.abs(best.x - px)) best = p;
    setHover(best);
  };

  const first = geo.placed[0];
  const last = geo.placed[geo.placed.length - 1];

  return (
    <figure className="relative max-w-2xl">
      <figcaption className="readout mb-1">views over time</figcaption>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto text-ink"
        role="img"
        aria-label={`Views from ${compact(first.views)} to ${compact(last.views)}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {[0, 0.5, 1].map((f) => {
          const yy = PAD.top + (1 - f) * (geo.baseY - PAD.top);
          return (
            <g key={f}>
              <line x1={PAD.left} x2={W - PAD.right} y1={yy} y2={yy}
                stroke="var(--color-rule)" strokeWidth="1" strokeDasharray={f === 0 ? '' : '2 4'} />
              <text x={PAD.left - 8} y={yy + 3} textAnchor="end" className="fill-current" opacity="0.55" fontSize="10">
                {compact(geo.vMax * f)}
              </text>
            </g>
          );
        })}
        <text x={first.x} y={H - 6} textAnchor="start" className="fill-current" opacity="0.55" fontSize="10">{fmtDay(first.captured_at)}</text>
        {geo.placed.length > 1 && (
          <text x={last.x} y={H - 6} textAnchor="end" className="fill-current" opacity="0.55" fontSize="10">{fmtDay(last.captured_at)}</text>
        )}
        <path d={geo.d} fill="none" stroke="var(--color-accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={last.x} cy={last.y} r="4" fill="var(--color-accent)" stroke="var(--color-paper-2)" strokeWidth="2" />
        {hover && (
          <g pointerEvents="none">
            <line x1={hover.x} x2={hover.x} y1={PAD.top} y2={geo.baseY} stroke="currentColor" opacity="0.3" strokeWidth="1" />
            <circle cx={hover.x} cy={hover.y} r="5" fill="var(--color-accent)" stroke="var(--color-paper-2)" strokeWidth="2" />
          </g>
        )}
        {/* full-height hit area so the hover works anywhere over the plot */}
        <rect x={PAD.left} y={PAD.top} width={W - PAD.left - PAD.right} height={geo.baseY - PAD.top} fill="transparent" />
      </svg>
      {hover && (
        <div
          className="absolute card px-2.5 py-1.5 text-xs pointer-events-none whitespace-nowrap"
          style={{
            left: `${(hover.x / W) * 100}%`,
            top: 18,
            transform: hover.x > W * 0.6 ? 'translateX(calc(-100% - 10px))' : 'translateX(10px)',
          }}
        >
          <p className="text-ink font-medium">{hover.views.toLocaleString()} views</p>
          <p className="readout">{fmtFull(hover.captured_at)} · {hover.source}</p>
        </div>
      )}
    </figure>
  );
}
