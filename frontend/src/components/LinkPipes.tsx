import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import type { LinkFull, LinkMeta, UsageResponse } from "../api";
import { avgBps, bps, speed } from "../format";
import { Segmented, cx } from "./ui";

/** Colors for the busiest ASes across all links; the same AS keeps its color in every pipe. */
const AS_COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7"];
const OTHER = "other";

type Scale = "capacity" | "relative";

interface Segment {
  key: string; // asn or "other"
  value: number; // bits per second
}

interface Pipe {
  id: number;
  name: string;
  color: string;
  capacity: number | null; // bits per second
  interfaces: string[];
  in: number;
  out: number;
  lanes: { in: Segment[]; out: Segment[] };
}

function buildSegments(top: { asn: number; v: number }[], total: number, colored: Set<string>): Segment[] {
  const segs: Segment[] = [];
  let rest = total;
  for (const t of top) {
    const key = String(t.asn);
    if (!colored.has(key) || t.v <= 0) continue;
    segs.push({ key, value: t.v });
    rest -= t.v;
  }
  if (rest > 0) segs.push({ key: OTHER, value: rest });
  return segs;
}

/**
 * Each link drawn as a pipe: inbound lane on top, outbound below, filled with the ASes using it.
 * Lengths are measured against the link's capacity when one is set, otherwise against the busiest link.
 */
export function LinkPipes({
  usage,
  links,
  linkById,
  visible,
}: {
  usage: UsageResponse;
  links: LinkFull[] | undefined;
  linkById: Map<number, LinkMeta>;
  visible: (id: number) => boolean;
}) {
  const navigate = useNavigate();
  const duration = Math.max(1, usage.end - usage.start);
  const [hover, setHover] = useState<string | null>(null);
  const hasCapacity = usage.links.some((u) => visible(u.link_id) && linkById.get(u.link_id)?.capacity_mbit);
  const [scalePick, setScale] = useState<Scale>("capacity");
  const scale: Scale = hasCapacity ? scalePick : "relative";

  const { pipes, legend, names } = useMemo(() => {
    const names = new Map<string, string>();
    const totals = new Map<string, number>();
    for (const u of usage.links) {
      if (!visible(u.link_id)) continue;
      for (const t of u.top) {
        const key = String(t.asn);
        names.set(key, t.name || t.handle || `AS${t.asn}`);
        totals.set(key, (totals.get(key) ?? 0) + t.in + t.out);
      }
    }
    const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, AS_COLORS.length).map(([k]) => k);
    const colored = new Set(ranked);
    const ifaces = new Map((links ?? []).map((l) => [l.id, l.interfaces.map((i) => `${i.exporter_name} · ${i.if_name || `ifIndex ${i.ifindex}`}`)]));
    const pipes: Pipe[] = usage.links
      .filter((u) => visible(u.link_id))
      .map((u) => {
        const link = linkById.get(u.link_id);
        const tIn = avgBps(u.in, duration);
        const tOut = avgBps(u.out, duration);
        return {
          id: u.link_id,
          name: link?.name ?? `Link ${u.link_id}`,
          color: link?.color ?? "#8b97a3",
          capacity: link?.capacity_mbit ? link.capacity_mbit * 1e6 : null,
          interfaces: ifaces.get(u.link_id) ?? [],
          in: tIn,
          out: tOut,
          lanes: {
            in: buildSegments(u.top.map((t) => ({ asn: t.asn, v: avgBps(t.in, duration) })), tIn, colored),
            out: buildSegments(u.top.map((t) => ({ asn: t.asn, v: avgBps(t.out, duration) })), tOut, colored),
          },
        };
      });
    return { pipes, legend: ranked.map((k, i) => ({ key: k, name: names.get(k)!, color: AS_COLORS[i] })), names };
  }, [usage, links, linkById, duration, visible]);

  const colorOf = useMemo(() => new Map(legend.map((l) => [l.key, l.color])), [legend]);
  const busiest = Math.max(1, ...pipes.filter((p) => scale === "relative" || !p.capacity).map((p) => Math.max(p.in, p.out)));

  const hoverText = (() => {
    if (!hover) return "Hover an AS to follow it through every link. Click it to open the AS.";
    if (hover === OTHER) return "All other ASes on the link.";
    const parts = pipes
      .map((p) => {
        const i = p.lanes.in.find((s) => s.key === hover)?.value ?? 0;
        const o = p.lanes.out.find((s) => s.key === hover)?.value ?? 0;
        return i || o ? `${p.name}: in ${bps(i)}, out ${bps(o)}` : null;
      })
      .filter(Boolean);
    return `${names.get(hover) ?? ""} AS${hover} · ${parts.join(" · ")}`;
  })();

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13px] text-muted">Average bandwidth over the selected range. Inbound on top, outbound below.</p>
        {hasCapacity && (
          <Segmented
            label="Scale"
            value={scale}
            onChange={setScale}
            options={[
              { id: "capacity", label: "Against capacity" },
              { id: "relative", label: "Relative to busiest link" },
            ]}
          />
        )}
      </div>

      <div className="grid gap-5">
        {pipes.map((p) => {
          const full = scale === "capacity" && p.capacity ? p.capacity : busiest;
          const share = (v: number) => {
            if (!p.capacity) return null;
            const pc = Math.round((v / p.capacity) * 100);
            return <span className={cx(pc >= 90 && "font-medium text-warn")}> · {pc}%</span>;
          };
          return (
            <div key={p.id}>
              <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <span className="inline-flex min-w-0 items-baseline gap-2">
                  <span className="font-semibold">{p.name}</span>
                  <span className="text-[12.5px] text-muted">{p.capacity ? `${speed(p.capacity / 1e6)} capacity` : scale === "capacity" ? "no capacity set" : ""}</span>
                </span>
                <span className="text-[13px] text-ink-2 num">
                  in {bps(p.in)}
                  {share(p.in)} <span className="text-faint">·</span> out {bps(p.out)}
                  {share(p.out)}
                </span>
              </div>
              <div className="flex items-stretch">
                <span className="w-1.5 shrink-0 rounded-l-sm" style={{ background: p.color }} aria-hidden="true" />
                <div className="min-w-0 flex-1 border-y border-rule-strong bg-sunken py-[3px]">
                  {(["in", "out"] as const).map((dir) => (
                    <div key={dir} className={cx("relative flex h-4", dir === "out" && "mt-[3px] border-t border-dashed border-rule-strong pt-0")}>
                      {p.lanes[dir].map((s) => (
                        <button
                          key={s.key}
                          type="button"
                          tabIndex={s.key === OTHER ? -1 : 0}
                          aria-label={`${s.key === OTHER ? "Other ASes" : `${names.get(s.key)} AS${s.key}`}, ${dir} ${bps(s.value)}`}
                          title={`${s.key === OTHER ? "Other ASes" : `${names.get(s.key)} AS${s.key}`} · ${dir} ${bps(s.value)}`}
                          onMouseEnter={() => setHover(s.key)}
                          onMouseLeave={() => setHover(null)}
                          onFocus={() => setHover(s.key)}
                          onBlur={() => setHover(null)}
                          onClick={() => s.key !== OTHER && navigate(`/as/${s.key}${window.location.search}`)}
                          className={cx(
                            "h-full transition-opacity",
                            s.key === OTHER ? "cursor-default" : "cursor-pointer",
                            hover && hover !== s.key && "opacity-25",
                          )}
                          style={{ width: `${Math.min(100, (s.value / full) * 100)}%`, background: colorOf.get(s.key) ?? "var(--c-faint)" }}
                        />
                      ))}
                      <span className="pointer-events-none absolute top-0 right-1.5 text-[11px] leading-4 text-muted">{dir}</span>
                    </div>
                  ))}
                </div>
                <span className="w-1.5 shrink-0 rounded-r-sm" style={{ background: p.color }} aria-hidden="true" />
              </div>
              {p.interfaces.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {p.interfaces.map((i) => (
                    <span key={i} className="rounded border border-rule px-1.5 text-[12px] leading-5 text-muted">
                      {i}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="mt-4 min-h-5 text-[13px] text-ink-2" aria-live="polite">
        {hoverText}
      </p>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-[13px] text-muted">
        {legend.map((l) => (
          <span key={l.key} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: l.color }} aria-hidden="true" />
            {l.name}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-[3px] bg-faint" aria-hidden="true" />
          Other ASes
        </span>
      </div>
    </div>
  );
}
