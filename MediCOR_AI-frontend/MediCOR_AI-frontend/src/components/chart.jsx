/**
 * Chart primitives — SVG, no library.
 *
 * Analytics.jsx already argued the dependency case and settled it: ~100KB
 * of bundle for four bar charts, in a project with four runtime
 * dependencies. That decision stands. What did not stand is what the
 * hand-rolled div-bars left out — no axis labels, no units, no tooltips,
 * and no way for a screen reader or a keyboard user to reach a value.
 *
 * The rule that makes these accessible is `<ChartData>`: every chart
 * renders a visually-hidden <table> of the same numbers. A chart conveys
 * its shape to people who can see it and nothing at all to anyone who
 * cannot, so the table is not a fallback — it is the accessible copy of
 * the same fact, and it is why the marks themselves can stay aria-hidden.
 *
 * One rendering rule inherited from the API and repeated here because it
 * is easy to lose: a null value is NOT zero. `resolution_rate_pct` is null
 * when no call connected, and a 0% bar tells a clinic that had a quiet
 * week that its AI failed every call. Nulls render as a gap.
 */

import { Tooltip } from "./menu";

/** The visually-hidden table that carries the real numbers. */
function ChartData({ caption, labelHeading, valueHeading, rows, format }) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">{labelHeading}</th>
          <th scope="col">{valueHeading}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <th scope="row">{r.label}</th>
            <td>{r.value === null || r.value === undefined ? "No data" : format(r.value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Vertical bars.
 *
 * @param {{label: string, value: number|null}[]} rows
 * @param {(v:number)=>string} format  used in the tooltip and the table
 * @param {string} unit                named on the axis, e.g. "calls"
 */
export function BarChart({
  rows,
  format = (v) => String(v),
  unit = "",
  caption,
  height = 192,
  ticks = 3,
  // How many bars between visible x labels. 0 hides the axis entirely.
  // Twenty-four hourly bars cannot carry twenty-four labels, but they must
  // carry some: without any, a bar's position means nothing until you
  // hover it, and a reader who cannot hover gets nothing at all.
  xEvery = 0,
  className = "",
}) {
  const values = rows.map((r) => r.value).filter((v) => typeof v === "number");
  // Floor of 1 so an all-zero range still draws a baseline rather than
  // dividing by zero.
  const max = Math.max(1, ...values);

  // Ticks are computed from the same `max` that scales the bars, so every
  // label names a value the chart actually reaches.
  // De-duplicated: with a max of 1 and three ticks, rounding gave "1, 1, 0".
  const tickValues = [
    ...new Set(Array.from({ length: ticks }, (_, i) => Math.round((max / (ticks - 1)) * (ticks - 1 - i)))),
  ];

  // A 90-day range is 90 bars; at the normal 6px gap the gaps alone are
  // 534px and the row overflows a half-width card. Past a month, 1px.
  // Bars and labels share it so the labels stay under their bars.
  const gap = rows.length > 31 ? "gap-px" : "gap-1.5";

  return (
    <div className={className}>
      <ChartData
        caption={caption}
        labelHeading="Period"
        valueHeading={unit || "Value"}
        rows={rows}
        format={format}
      />
      <div className="flex gap-2" style={{ height }} aria-hidden="true">
        <div className="flex w-8 shrink-0 flex-col justify-between text-right text-xs leading-none text-on-surface-variant">
          {tickValues.map((t, i) => (
            <span key={i} className="tabular-nums">{t}</span>
          ))}
        </div>
        <div className={`flex min-w-0 flex-1 items-end ${gap} border-l border-outline-variant pl-2`}>
          {rows.map((r, i) => {
            if (r.value === null || r.value === undefined) {
              return <div key={i} className="flex-1" title={`${r.label}: no data`} />;
            }
            const pct = (r.value / max) * 100;
            return (
              // `h-full` is load-bearing, not spacing. The bar's height is a
              // percentage, and a percentage height only resolves against a
              // parent with a definite height. This Tooltip wrapper sits
              // between the bar and the sized track, so without it every bar
              // resolves against `auto` and collapses to nothing — a chart
              // that draws a correct axis over a flat row of stubs.
              <Tooltip key={i} content={`${r.label}: ${format(r.value)}`} className="h-full flex-1 items-end">
                {/* Not focusable. The bars sit inside aria-hidden, so a
                    tab stop here was one a screen reader could not
                    announce -- 24 on the Dashboard, ~120 on a 90-day
                    Analytics page. The hidden table carries the values. */}
                <div
                  className="w-full rounded-t-sm bg-accent"
                  style={{
                    // A zero-value bar still gets 2px so the column reads
                    // as "measured zero" rather than "no bar drawn here".
                    height: r.value === 0 ? "2px" : `${Math.max(pct, 2)}%`,
                    opacity: 0.55 + (r.value / max) * 0.45,
                  }}
                />
              </Tooltip>
            );
          })}
        </div>
      </div>
      {xEvery > 0 && (
        <div className="mt-1.5 flex gap-2" aria-hidden="true">
          <div className="w-8 shrink-0" />
          <div className={`flex min-w-0 flex-1 ${gap} pl-2`}>
            {rows.map((r, i) => (
              // Every bar keeps a slot so the labels stay aligned with the
              // bars above them; only every nth slot carries text.
              <span
                key={i}
                className="min-w-0 flex-1 text-center text-xs leading-none text-on-surface-variant"
              >
                {i % xEvery === 0 ? <span className="whitespace-nowrap">{r.label}</span> : null}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** A single-series trend line, for a KPI tile. */
export function Sparkline({ rows, format = (v) => String(v), caption, width = 120, height = 32, className = "" }) {
  const values = rows.map((r) => (typeof r.value === "number" ? r.value : 0));
  const max = Math.max(1, ...values);
  const step = rows.length > 1 ? width / (rows.length - 1) : width;
  const points = values.map((v, i) => `${i * step},${height - (v / max) * (height - 2) - 1}`).join(" ");

  return (
    <div className={className}>
      <ChartData caption={caption} labelHeading="Period" valueHeading="Value" rows={rows} format={format} />
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
        className="overflow-visible"
      >
        <polyline
          points={points}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

/**
 * No data yet — not a blank axis frame.
 *
 * An empty chart and a broken chart look identical, which sends a clinic
 * hunting a fault that does not exist.
 */
export function ChartEmpty({ message = "No data for this period", className = "" }) {
  return (
    <div
      className={`flex items-center justify-center rounded-lg border border-dashed border-outline-variant px-4 py-10 text-center text-sm text-on-surface-variant ${className}`}
    >
      {message}
    </div>
  );
}

/** Reserves the chart's real height, so the swap causes no layout shift. */
export function ChartSkeleton({ bars = 12, height = 192, className = "" }) {
  return (
    <div role="status" aria-live="polite" className={className}>
      <span className="sr-only">Loading chart…</span>
      <div className="flex gap-2" style={{ height }} aria-hidden="true">
        <div className="w-8 shrink-0" />
        <div className="flex flex-1 items-end gap-1.5 border-l border-outline-variant pl-2">
          {Array.from({ length: bars }).map((_, i) => (
            <div
              key={i}
              className="flex-1 animate-pulse rounded-t-sm bg-surface-container"
              // Varied heights so it reads as a chart loading rather than
              // a solid block.
              style={{ height: `${25 + ((i * 37) % 55)}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
