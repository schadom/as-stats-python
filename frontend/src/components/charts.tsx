import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { SeriesResponse } from "../api";
import { bps, bytes as fmtBytes } from "../format";
import { useIsDark, type IpFilter } from "../state";

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

/** One stacked layer of a mirrored chart: bytes per bucket, inbound and outbound. */
export interface Layer {
  key: string;
  name: string;
  color: string;
  inn: number[];
  out: number[];
}

/** Seconds covered by each bucket (the newest one may be partial). */
export function bucketSeconds(s: { buckets: number[]; step: number; end: number }): number[] {
  return s.buckets.map((b) => Math.max(1, Math.min(s.step, s.end - b)));
}

function pick(entry: SeriesResponse["series"][number], ip: IpFilter): [number[], number[]] {
  if (ip === "4") return [entry.in_v4, entry.out_v4];
  if (ip === "6") return [entry.in_v6, entry.out_v6];
  return [entry.in_v4.map((v, i) => v + entry.in_v6[i]), entry.out_v4.map((v, i) => v + entry.out_v6[i])];
}

/** Build per-link layers from a series response (optionally only for one AS). */
export function layersByLink(
  s: SeriesResponse,
  ip: IpFilter,
  links: Map<number, { name: string; color: string; sort_order?: number }>,
  asn?: number,
): Layer[] {
  const n = s.buckets.length;
  const byLink = new Map<number, Layer>();
  for (const e of s.series) {
    if (asn !== undefined && e.asn !== asn) continue;
    const meta = links.get(e.link_id);
    let layer = byLink.get(e.link_id);
    if (!layer) {
      layer = {
        key: String(e.link_id),
        name: meta?.name ?? `Link ${e.link_id}`,
        color: meta?.color ?? "#8b97a3",
        inn: new Array(n).fill(0),
        out: new Array(n).fill(0),
      };
      byLink.set(e.link_id, layer);
    }
    const [i, o] = pick(e, ip);
    for (let k = 0; k < n; k++) {
      layer.inn[k] += i[k];
      layer.out[k] += o[k];
    }
  }
  const order = [...links.keys()];
  return [...byLink.entries()].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0])).map(([, l]) => l);
}

/** Layers for IPv4 vs IPv6 of a single AS, summed over links. */
export function layersByFamily(s: SeriesResponse, colors: [string, string]): Layer[] {
  const n = s.buckets.length;
  const v4: Layer = { key: "v4", name: "IPv4", color: colors[0], inn: new Array(n).fill(0), out: new Array(n).fill(0) };
  const v6: Layer = { key: "v6", name: "IPv6", color: colors[1], inn: new Array(n).fill(0), out: new Array(n).fill(0) };
  for (const e of s.series) {
    for (let k = 0; k < n; k++) {
      v4.inn[k] += e.in_v4[k];
      v4.out[k] += e.out_v4[k];
      v6.inn[k] += e.in_v6[k];
      v6.out[k] += e.out_v6[k];
    }
  }
  return [v4, v6];
}

// ---------------------------------------------------------------- mirrored sparkline

/**
 * Compact mirrored area chart: inbound stacks upward from the axis, outbound
 * downward. The axis sits where the two maxima put it, so the split between
 * in and out reads at a glance.
 */
export function MirrorSpark({
  layers,
  seconds,
  height = 52,
  width = 320,
  label,
}: {
  layers: Layer[];
  seconds: number[];
  height?: number;
  width?: number;
  label: string;
}) {
  const shapes = useMemo(() => {
    const n = seconds.length;
    if (!n || !layers.length) return null;
    const cumIn: number[][] = [];
    const cumOut: number[][] = [];
    let accIn = new Array(n).fill(0);
    let accOut = new Array(n).fill(0);
    for (const l of layers) {
      accIn = accIn.map((v, i) => v + (l.inn[i] * 8) / seconds[i]);
      accOut = accOut.map((v, i) => v + (l.out[i] * 8) / seconds[i]);
      cumIn.push(accIn);
      cumOut.push(accOut);
    }
    const maxIn = Math.max(...accIn, 0);
    const maxOut = Math.max(...accOut, 0);
    const total = maxIn + maxOut;
    if (total <= 0) return { paths: [], axis: height / 2 };
    const pad = 2;
    const usable = height - pad * 2;
    const scale = usable / total;
    const axis = pad + maxIn * scale;
    const x = (i: number) => (n === 1 ? width / 2 : (i / (n - 1)) * width);
    const paths: { d: string; color: string }[] = [];
    layers.forEach((l, li) => {
      const topIn = cumIn[li];
      const baseIn = li ? cumIn[li - 1] : new Array(n).fill(0);
      const topOut = cumOut[li];
      const baseOut = li ? cumOut[li - 1] : new Array(n).fill(0);
      const area = (top: number[], base: number[], sign: number) => {
        let d = `M${x(0).toFixed(1)},${(axis - sign * top[0] * scale).toFixed(1)}`;
        for (let i = 1; i < n; i++) d += `L${x(i).toFixed(1)},${(axis - sign * top[i] * scale).toFixed(1)}`;
        for (let i = n - 1; i >= 0; i--) d += `L${x(i).toFixed(1)},${(axis - sign * base[i] * scale).toFixed(1)}`;
        return d + "Z";
      };
      paths.push({ d: area(topIn, baseIn, 1), color: l.color });
      paths.push({ d: area(topOut, baseOut, -1), color: l.color });
    });
    return { paths, axis };
  }, [layers, seconds, height, width]);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="block h-[52px] w-full" role="img" aria-label={label}>
      {shapes?.paths.map((p, i) => (
        <path key={i} d={p.d} fill={p.color} fillOpacity={i % 2 ? 0.55 : 0.85} />
      ))}
      <line x1="0" x2={width} y1={shapes?.axis ?? height / 2} y2={shapes?.axis ?? height / 2} stroke="var(--c-axis)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// ---------------------------------------------------------------- full chart

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function TrafficChart({
  layers,
  buckets,
  seconds,
  height = 320,
}: {
  layers: Layer[];
  buckets: number[];
  seconds: number[];
  height?: number;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const dark = useIsDark();

  useEffect(() => {
    if (!el.current) return;
    chart.current = echarts.init(el.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const ink = cssVar("--c-ink");
    const muted = cssVar("--c-muted");
    const rule = cssVar("--c-rule");
    const axis = cssVar("--c-axis");
    const surface = cssVar("--c-surface");
    const times = buckets.map((b) => b * 1000);
    const series: echarts.EChartsCoreOption[] = [];
    for (const l of layers) {
      const rateIn = l.inn.map((v, i) => [times[i], (v * 8) / seconds[i]]);
      const rateOut = l.out.map((v, i) => [times[i], -(v * 8) / seconds[i]]);
      for (const [dir, data, opacity] of [
        ["in", rateIn, 0.82],
        ["out", rateOut, 0.55],
      ] as const) {
        series.push({
          id: `${l.key}-${dir}`,
          name: l.name,
          type: "line",
          stack: dir,
          data,
          symbol: "none",
          sampling: "lttb",
          lineStyle: { width: 0 },
          areaStyle: { color: l.color, opacity },
          itemStyle: { color: l.color },
          emphasis: { disabled: true },
        });
      }
    }
    c.setOption(
      {
        animation: false,
        grid: { left: 64, right: 16, top: 16, bottom: 36 },
        textStyle: { fontFamily: "Instrument Sans Variable, system-ui, sans-serif", color: muted },
        tooltip: {
          trigger: "axis",
          backgroundColor: surface,
          borderColor: rule,
          textStyle: { color: ink, fontSize: 12.5 },
          axisPointer: { type: "line", lineStyle: { color: muted } },
          formatter: (params: unknown) => {
            const items = params as { seriesId: string; seriesName: string; value: [number, number]; color: string; dataIndex: number }[];
            if (!items.length) return "";
            const ts = items[0].value[0];
            const rows = new Map<string, { name: string; color: string; in: number; out: number }>();
            for (const p of items) {
              const key = p.seriesId.replace(/-(in|out)$/, "");
              const layer = layers.find((l) => l.key === key);
              const r = rows.get(key) ?? { name: p.seriesName, color: layer?.color ?? p.color, in: 0, out: 0 };
              if (p.seriesId.endsWith("-in")) r.in = p.value[1];
              else r.out = -p.value[1];
              rows.set(key, r);
            }
            let tin = 0;
            let tout = 0;
            const lines = [...rows.values()].map((r) => {
              tin += r.in;
              tout += r.out;
              return `<tr><td style="padding:1px 8px 1px 0"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${r.color};margin-right:6px"></span>${r.name}</td><td style="text-align:right;padding:1px 8px;font-variant-numeric:tabular-nums">${bps(r.in)}</td><td style="text-align:right;font-variant-numeric:tabular-nums">${bps(r.out)}</td></tr>`;
            });
            const when = new Date(ts).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
            const total =
              rows.size > 1
                ? `<tr style="border-top:1px solid ${rule}"><td style="padding:3px 8px 1px 0;font-weight:600">Total</td><td style="text-align:right;padding:3px 8px 1px;font-weight:600;font-variant-numeric:tabular-nums">${bps(tin)}</td><td style="text-align:right;padding-top:3px;font-weight:600;font-variant-numeric:tabular-nums">${bps(tout)}</td></tr>`
                : "";
            return `<div style="font-weight:600;margin-bottom:4px">${when}</div><table><tr style="color:${muted}"><td></td><td style="text-align:right;padding:0 8px">In</td><td style="text-align:right">Out</td></tr>${lines.join("")}${total}</table>`;
          },
        },
        xAxis: {
          type: "time",
          axisLine: { lineStyle: { color: rule } },
          axisTick: { show: false },
          axisLabel: { color: muted, hideOverlap: true },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: muted, formatter: (v: number) => bps(Math.abs(v), 0) },
          splitLine: { lineStyle: { color: rule, type: [3, 3] } },
          axisLine: { show: false },
        },
        dataZoom: [{ type: "inside", filterMode: "none" }],
        series: [
          ...series,
          {
            id: "zero",
            type: "line",
            data: times.length ? [[times[0], 0], [times[times.length - 1], 0]] : [],
            symbol: "none",
            silent: true,
            lineStyle: { color: axis, width: 1.2 },
            tooltip: { show: false },
            z: 10,
          },
        ],
      },
      { notMerge: true },
    );
  }, [layers, buckets, seconds, dark]);

  return <div ref={el} style={{ height }} className="w-full" />;
}

/** Totals of a layer set, for legends. */
export function layerTotals(layers: Layer[]): { key: string; name: string; color: string; inn: number; out: number }[] {
  return layers.map((l) => ({
    key: l.key,
    name: l.name,
    color: l.color,
    inn: l.inn.reduce((a, b) => a + b, 0),
    out: l.out.reduce((a, b) => a + b, 0),
  }));
}

export function ChartLegend({ layers, seconds }: { layers: Layer[]; seconds: number }) {
  const totals = layerTotals(layers);
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-[13px]">
      {totals.map((t) => (
        <span key={t.key} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: t.color }} aria-hidden="true" />
          <span className="font-medium">{t.name}</span>
          <span className="text-muted num">
            in {fmtBytes(t.inn)} (avg {bps((t.inn * 8) / seconds)}), out {fmtBytes(t.out)} (avg {bps((t.out * 8) / seconds)})
          </span>
        </span>
      ))}
    </div>
  );
}
