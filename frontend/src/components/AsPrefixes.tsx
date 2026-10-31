import { useState } from "react";
import { Link } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, type AsPrefixes as AsPrefixesT, type LinkMeta } from "../api";
import { bytes, dateTime, pct } from "../format";
import { filterQuery, type Filters } from "../state";
import { Badge, Button, Empty, ErrorNote, LinkDot, Loading, Panel, Segmented, td, th } from "./ui";

type Sort = "total" | "in" | "out";
const TOP = 10;

function ExternalIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 3h4v4M13 3L7.5 8.5M11 9.5V13H3V5h3.5" />
    </svg>
  );
}

/** Remote prefixes of one AS, from the optional prefix statistics (Settings, General). */
export function AsPrefixes({
  asn,
  filters,
  linkById,
  isAdmin,
  enabled,
}: {
  asn: number;
  filters: Filters;
  linkById: Map<number, LinkMeta>;
  isAdmin: boolean;
  enabled: boolean;
}) {
  const [sort, setSort] = useState<Sort>("total");
  const [all, setAll] = useState(false);
  const q = useQuery({
    queryKey: ["as-prefixes", asn, filters.range, filters.ip, filters.links.join(","), sort, all],
    queryFn: () => get<AsPrefixesT>(`/as/${asn}/prefixes?${filterQuery(filters, { sort, limit: all ? 1000 : TOP })}`),
    placeholderData: keepPreviousData,
  });

  const d = q.data;
  const hasData = !!d && (d.count > 0 || d.other.in + d.other.out > 0);
  // Feature off and nothing stored: only admins get a pointer to the setting.
  if (!enabled && !hasData) {
    if (!isAdmin || q.isLoading) return null;
    return (
      <Panel title="Prefixes" className="mt-5">
        <p className="text-muted">
          Prefix statistics are off. Turn them on under{" "}
          <Link to="/settings/general" className="font-medium text-accent hover:underline">
            General settings
          </Link>{" "}
          to see which prefixes of this AS your network talks to. They need some extra disk space.
        </p>
      </Panel>
    );
  }

  const total = d ? d.total.in + d.total.out : 0;
  const rowTotal = (r: { in: number; out: number }) => (sort === "in" ? r.in : sort === "out" ? r.out : r.in + r.out);
  const max = Math.max(1, ...(d?.prefixes ?? []).map((p) => p.in + p.out));
  const linkDots = (links: Record<string, { in: number; out: number }>) =>
    Object.entries(links)
      .filter(([, v]) => v.in + v.out > 0)
      .sort((a, b) => b[1].in + b[1].out - (a[1].in + a[1].out))
      .map(([id, v]) => {
        const l = linkById.get(Number(id));
        return (
          <span key={id} title={`${l?.name ?? `Link ${id}`}: in ${bytes(v.in)}, out ${bytes(v.out)}`} className="inline-flex">
            <LinkDot color={l?.color ?? "#8b97a3"} />
          </span>
        );
      });
  const partial = d?.first && d.first > d.start;

  return (
    <Panel
      title="Prefixes"
      className="mt-5"
      flush
      actions={
        <Segmented
          label="Sort prefixes by"
          value={sort}
          onChange={setSort}
          options={[
            { id: "total", label: "Total" },
            { id: "in", label: "In" },
            { id: "out", label: "Out" },
          ]}
        />
      }
    >
      {q.error && (
        <div className="p-4">
          <ErrorNote error={q.error} />
        </div>
      )}
      {q.isLoading ? (
        <Loading />
      ) : !hasData ? (
        <Empty title="No prefix data for this range yet">
          {d?.first ? `Prefix statistics are collected since ${dateTime(d.first)}, in hourly steps.` : "Prefix statistics are on. The first data shows up within the hour."}
        </Empty>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead className="border-b border-rule">
                <tr>
                  <th className={th}>Prefix</th>
                  <th className={`${th} text-right`}>In</th>
                  <th className={`${th} text-right`}>Out</th>
                  <th className={`${th} w-[22%]`}>Share</th>
                  <th className={th}>Links</th>
                  <th className={th}>
                    <span className="sr-only">Open on bgp.tools</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {d!.prefixes.map((p) => (
                  <tr key={p.prefix} className="border-b border-rule last:border-b-0">
                    <td className={td}>
                      <span className="font-medium num">{p.prefix}</span>
                      {p.prefix.includes(":") && (
                        <span className="ml-2">
                          <Badge>IPv6</Badge>
                        </span>
                      )}
                    </td>
                    <td className={`${td} text-right num`}>{bytes(p.in)}</td>
                    <td className={`${td} text-right num`}>{bytes(p.out)}</td>
                    <td className={td}>
                      <span className="text-[13px] num">{pct(rowTotal(p), sort === "in" ? d!.total.in : sort === "out" ? d!.total.out : total)}</span>
                      <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-sunken" aria-hidden="true">
                        <span className="bg-ink-2" style={{ width: `${(p.in / max) * 100}%` }} />
                        <span className="bg-ink-2 opacity-40" style={{ width: `${(p.out / max) * 100}%` }} />
                      </div>
                    </td>
                    <td className={td}>
                      <span className="inline-flex gap-1">{linkDots(p.links)}</span>
                    </td>
                    <td className={`${td} text-right`}>
                      <a
                        href={`https://bgp.tools/prefix/${p.prefix}`}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex text-muted hover:text-accent"
                        aria-label={`${p.prefix} on bgp.tools`}
                        title="Open on bgp.tools"
                      >
                        <ExternalIcon />
                      </a>
                    </td>
                  </tr>
                ))}
                {d!.other.in + d!.other.out > 0 && (
                  <tr className="border-t border-rule">
                    <td className={`${td} text-muted`} title="Prefixes below the minimum volume or beyond the per-AS limit, summed per hour">
                      Other prefixes
                    </td>
                    <td className={`${td} text-right text-muted num`}>{bytes(d!.other.in)}</td>
                    <td className={`${td} text-right text-muted num`}>{bytes(d!.other.out)}</td>
                    <td className={`${td} text-[13px] text-muted num`}>{pct(rowTotal(d!.other), sort === "in" ? d!.total.in : sort === "out" ? d!.total.out : total)}</td>
                    <td className={td}>
                      <span className="inline-flex gap-1">{linkDots(d!.other.links)}</span>
                    </td>
                    <td className={td} />
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-rule px-4 py-2.5 text-[13px] text-muted">
            <span>
              {d!.count > d!.prefixes.length ? `Top ${d!.prefixes.length} of ${d!.count} prefixes` : `${d!.count} prefix${d!.count === 1 ? "" : "es"}`} · hourly data
              {partial ? ` · collected since ${dateTime(d!.first!)}` : ""}
              {!enabled ? " · collection is off, showing earlier data" : ""}
            </span>
            {d!.count > TOP && (
              <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>
                {all ? `Show top ${TOP}` : `Show all ${d!.count}`}
              </Button>
            )}
          </div>
        </>
      )}
    </Panel>
  );
}
