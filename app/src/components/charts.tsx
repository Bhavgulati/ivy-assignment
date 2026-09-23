// Charts as plain SVG. No library, for the same reason there is no UI kit: the
// three plots below exist to make three specific findings visible, and a
// general-purpose charting dependency is a lot of surface area for that.
//
// Each one is the picture of an argument made in submission.json. The scatter
// in particular is the plot that caught a wrong answer of mine — read as
// rupees, the project price field puts every project in the same place; plotted
// against what its own listings actually cost, it splits into two clusters two
// orders of magnitude apart.

const AXIS = "#3a4450";
const GRID = "#222831";
const TEXT = "#8b97a6";

function niceTicks(lo: number, hi: number, n = 5) {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}

// ---------------------------------------------------------------------------

export function Histogram({
  series,
  bins = 34,
  xLabel,
  height = 230,
  format = (v: number) => String(Math.round(v)),
}: {
  series: { label: string; values: number[]; color: string }[];
  bins?: number;
  xLabel: string;
  height?: number;
  format?: (v: number) => string;
}) {
  const all = series.flatMap((s) => s.values).filter((v) => isFinite(v));
  if (all.length < 2) return null;
  const sorted = [...all].sort((a, b) => a - b);
  // clip the top percentile so one extreme value does not flatten the shape
  const lo = sorted[0];
  const hi = sorted[Math.floor(sorted.length * 0.99)];
  const w = 720, padL = 46, padR = 14, padB = 40, padT = 10;
  const iw = w - padL - padR, ih = height - padB - padT;
  const width = (hi - lo) / bins;

  const counted = series.map((s) => {
    const c = new Array(bins).fill(0);
    for (const v of s.values) {
      if (v < lo || v > hi) continue;
      c[Math.min(bins - 1, Math.floor((v - lo) / width))] += 1;
    }
    return { ...s, counts: c };
  });
  const maxC = Math.max(...counted.flatMap((s) => s.counts), 1);

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${w} ${height}`} role="img" aria-label={xLabel}>
        {niceTicks(0, maxC, 4).map((t) => {
          const y = padT + ih - (t / maxC) * ih;
          return (
            <g key={`y${t}`}>
              <line x1={padL} x2={w - padR} y1={y} y2={y} stroke={GRID} />
              <text x={padL - 7} y={y + 4} fill={TEXT} fontSize="10" textAnchor="end">
                {t}
              </text>
            </g>
          );
        })}
        {counted.map((s) =>
          s.counts.map((c, i) => {
            if (!c) return null;
            const bw = iw / bins;
            const h = (c / maxC) * ih;
            return (
              <rect
                key={`${s.label}${i}`}
                x={padL + i * bw}
                y={padT + ih - h}
                width={Math.max(1, bw - 1)}
                height={h}
                fill={s.color}
                opacity={0.62}
              />
            );
          })
        )}
        <line x1={padL} x2={w - padR} y1={padT + ih} y2={padT + ih} stroke={AXIS} />
        {niceTicks(lo, hi, 6).map((t) => {
          const x = padL + ((t - lo) / (hi - lo)) * iw;
          return (
            <text key={`x${t}`} x={x} y={height - 20} fill={TEXT} fontSize="10" textAnchor="middle">
              {format(t)}
            </text>
          );
        })}
        <text x={padL + iw / 2} y={height - 4} fill={TEXT} fontSize="11" textAnchor="middle">
          {xLabel}
        </text>
      </svg>
      <figcaption className="legend">
        {series.map((s) => (
          <span key={s.label}>
            <i style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------------------

export function LogScatter({
  points,
  xLabel,
  yLabel,
  height = 300,
}: {
  points: { x: number; y: number; group: string; color: string; id?: string }[];
  xLabel: string;
  yLabel: string;
  height?: number;
}) {
  const pts = points.filter((p) => p.x > 0 && p.y > 0 && isFinite(p.x) && isFinite(p.y));
  if (pts.length < 2) return null;
  const lx = pts.map((p) => Math.log10(p.x));
  const ly = pts.map((p) => Math.log10(p.y));
  const x0 = Math.floor(Math.min(...lx)), x1 = Math.ceil(Math.max(...lx));
  const y0 = Math.floor(Math.min(...ly)), y1 = Math.ceil(Math.max(...ly));
  const w = 720, padL = 58, padR = 14, padB = 44, padT = 12;
  const iw = w - padL - padR, ih = height - padB - padT;
  const px = (v: number) => padL + ((Math.log10(v) - x0) / (x1 - x0 || 1)) * iw;
  const py = (v: number) => padT + ih - ((Math.log10(v) - y0) / (y1 - y0 || 1)) * ih;

  const groups = Array.from(new Set(pts.map((p) => p.group)));
  const decade = (n: number) =>
    n >= 7 ? `${10 ** (n - 7)} Cr` : n >= 5 ? `${10 ** (n - 5)} L` : `10^${n}`;

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${w} ${height}`} role="img" aria-label={`${yLabel} against ${xLabel}`}>
        {Array.from({ length: y1 - y0 + 1 }, (_, i) => y0 + i).map((n) => (
          <g key={`gy${n}`}>
            <line x1={padL} x2={w - padR} y1={py(10 ** n)} y2={py(10 ** n)} stroke={GRID} />
            <text x={padL - 7} y={py(10 ** n) + 4} fill={TEXT} fontSize="10" textAnchor="end">
              {decade(n)}
            </text>
          </g>
        ))}
        {Array.from({ length: x1 - x0 + 1 }, (_, i) => x0 + i).map((n) => (
          <g key={`gx${n}`}>
            <line x1={px(10 ** n)} x2={px(10 ** n)} y1={padT} y2={padT + ih} stroke={GRID} />
            <text x={px(10 ** n)} y={height - 22} fill={TEXT} fontSize="10" textAnchor="middle">
              {`10^${n}`}
            </text>
          </g>
        ))}
        {pts.map((p, i) => (
          <circle key={i} cx={px(p.x)} cy={py(p.y)} r={3.1} fill={p.color} opacity={0.75}>
            <title>{`${p.id ?? ""} — stored ${p.x}, listings peak ${Math.round(p.y).toLocaleString("en-IN")}`}</title>
          </circle>
        ))}
        <line x1={padL} x2={w - padR} y1={padT + ih} y2={padT + ih} stroke={AXIS} />
        <line x1={padL} x2={padL} y1={padT} y2={padT + ih} stroke={AXIS} />
        <text x={padL + iw / 2} y={height - 5} fill={TEXT} fontSize="11" textAnchor="middle">
          {xLabel}
        </text>
        <text x={13} y={padT + ih / 2} fill={TEXT} fontSize="11" textAnchor="middle"
              transform={`rotate(-90 13 ${padT + ih / 2})`}>
          {yLabel}
        </text>
      </svg>
      <figcaption className="legend">
        {groups.map((g) => (
          <span key={g}>
            <i style={{ background: pts.find((p) => p.group === g)!.color }} />
            {g}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

// ---------------------------------------------------------------------------

export function Scatter2D({
  points,
  xLabel,
  yLabel,
  height = 300,
}: {
  points: { x: number; y: number; r?: number; group: string; color: string; id?: string }[];
  xLabel: string;
  yLabel: string;
  height?: number;
}) {
  const pts = points.filter((p) => isFinite(p.x) && isFinite(p.y));
  if (pts.length < 2) return null;
  const x0 = 0, x1 = 1;
  const y0 = 0;
  const y1 = Math.max(...pts.map((p) => p.y)) * 1.05;
  const w = 720, padL = 52, padR = 14, padB = 44, padT = 12;
  const iw = w - padL - padR, ih = height - padB - padT;
  const px = (v: number) => padL + ((v - x0) / (x1 - x0)) * iw;
  const py = (v: number) => padT + ih - ((v - y0) / (y1 - y0)) * ih;
  const groups = Array.from(new Set(pts.map((p) => p.group)));

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${w} ${height}`} role="img" aria-label={`${yLabel} against ${xLabel}`}>
        {niceTicks(y0, y1, 5).map((t) => (
          <g key={`y${t}`}>
            <line x1={padL} x2={w - padR} y1={py(t)} y2={py(t)} stroke={GRID} />
            <text x={padL - 7} y={py(t) + 4} fill={TEXT} fontSize="10" textAnchor="end">
              {t.toFixed(1)}
            </text>
          </g>
        ))}
        <line x1={padL} x2={w - padR} y1={py(1)} y2={py(1)} stroke="#4a5563" strokeDasharray="4 4" />
        <text x={w - padR - 4} y={py(1) - 5} fill={TEXT} fontSize="10" textAnchor="end">
          market rate
        </text>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <text key={`x${t}`} x={px(t)} y={height - 22} fill={TEXT} fontSize="10" textAnchor="middle">
            {`${t * 100}%`}
          </text>
        ))}
        {pts.map((p, i) => (
          <circle key={i} cx={px(p.x)} cy={py(p.y)} r={p.r ?? 3.4} fill={p.color} opacity={0.8}>
            <title>{`${p.id ?? ""} — ${(p.x * 100).toFixed(0)}% verified, priced at ${p.y.toFixed(2)} of the local median`}</title>
          </circle>
        ))}
        <line x1={padL} x2={w - padR} y1={padT + ih} y2={padT + ih} stroke={AXIS} />
        <line x1={padL} x2={padL} y1={padT} y2={padT + ih} stroke={AXIS} />
        <text x={padL + iw / 2} y={height - 5} fill={TEXT} fontSize="11" textAnchor="middle">
          {xLabel}
        </text>
        <text x={11} y={padT + ih / 2} fill={TEXT} fontSize="11" textAnchor="middle"
              transform={`rotate(-90 11 ${padT + ih / 2})`}>
          {yLabel}
        </text>
      </svg>
      <figcaption className="legend">
        {groups.map((g) => (
          <span key={g}>
            <i style={{ background: pts.find((p) => p.group === g)!.color }} />
            {g}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
