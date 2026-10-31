import { useCallback, useMemo } from "react";
import { Link } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, type LinkFull, type SeriesResponse, type UsageResponse } from "../api";
import { ChartLegend, TrafficChart, bucketSeconds, layersByLink } from "../components/charts";
import { FilterBar } from "../components/Layout";
import { LinkPipes } from "../components/LinkPipes";
import { Empty, ErrorNote, LinkDot, Loading, PageHeader, Panel } from "../components/ui";
import { avgBps, bps, bytes, pct, rangeLabel } from "../format";
import { filterQuery, navQuery, useFilters, useSession } from "../state";

export default function Links() {
  const { meta, linkById } = useSession();
  const [f] = useFilters();
  const series = useQuery({
    queryKey: ["link-series", f.range, f.links.join(",")],
    queryFn: () => get<SeriesResponse>(`/links/series?${filterQuery({ ...f, ip: "all" })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  const usage = useQuery({
    queryKey: ["usage", f.range, f.ip],
    queryFn: () => get<UsageResponse>(`/links/usage?${filterQuery({ ...f, links: [] })}`),
    placeholderData: keepPreviousData,
  });
  const linksFull = useQuery({ queryKey: ["links-full"], queryFn: () => get<LinkFull[]>("/links"), staleTime: 60_000 });
  const linkFilter = f.links.join(",");
  const isVisible = useCallback((id: number) => !linkFilter || linkFilter.split(",").map(Number).includes(id), [linkFilter]);
  const layers = useMemo(() => (series.data ? layersByLink(series.data, f.ip, linkById) : []), [series.data, f.ip, linkById]);
  const seconds = useMemo(() => (series.data ? bucketSeconds(series.data) : []), [series.data]);
  const q = navQuery(f);

  if (meta.links.length === 0) {
    return (
      <>
        <PageHeader title="Links" />
        <div className="rounded-lg border border-rule bg-surface">
          <Empty title="No links configured yet">Links are your transit, peering and IX ports. Create them from the interfaces your routers report under Links &amp; interfaces.</Empty>
        </div>
      </>
    );
  }

  const visible = usage.data?.links.filter((l) => !f.links.length || f.links.includes(l.link_id)) ?? [];
  const duration = usage.data ? usage.data.end - usage.data.start : 1;

  return (
    <>
      <PageHeader title="Links" subtitle={series.data ? rangeLabel(series.data.start, series.data.end) : " "} />
      <FilterBar />
      <Panel title="All links, stacked">
        {series.error && <ErrorNote error={series.error} />}
        {series.data && layers.length > 0 ? (
          <>
            <TrafficChart layers={layers} buckets={series.data.buckets} seconds={seconds} height={320} />
            <div className="mt-3">
              <ChartLegend layers={layers} seconds={series.data.end - series.data.start} />
            </div>
          </>
        ) : series.isLoading ? (
          <Loading />
        ) : (
          <Empty title="No traffic in this range" />
        )}
      </Panel>

      <Panel title="Link utilization" className="mt-5">
        {usage.error && <ErrorNote error={usage.error} />}
        {usage.data && visible.length > 0 ? (
          <LinkPipes usage={usage.data} links={linksFull.data} linkById={linkById} visible={isVisible} />
        ) : usage.isLoading ? (
          <Loading />
        ) : (
          <Empty title="No traffic in this range" />
        )}
      </Panel>

      <h2 className="mt-8 mb-3 text-[17px] font-semibold">Who uses each link</h2>
      {usage.isLoading && <Loading />}
      <div className="grid gap-5 lg:grid-cols-2">
        {visible.map((u) => {
          const link = linkById.get(u.link_id);
          const total = u.in + u.out;
          const max = Math.max(1, ...u.top.map((t) => t.in + t.out));
          return (
            <Panel
              key={u.link_id}
              flush
              title={
                <span className="inline-flex items-center gap-2">
                  <LinkDot color={link?.color ?? "#8b97a3"} />
                  {link?.name ?? `Link ${u.link_id}`}
                </span>
              }
              actions={
                <span className="text-[13px] text-muted num">
                  in {bps(avgBps(u.in, duration))}, out {bps(avgBps(u.out, duration))}
                </span>
              }
            >
              {u.top.length === 0 ? (
                <div className="px-4 py-6 text-muted">No traffic on this link in the selected range.</div>
              ) : (
                <ol>
                  {u.top.map((t) => (
                    <li key={t.asn} className="border-b border-rule px-4 py-2 last:border-b-0">
                      <div className="flex items-baseline justify-between gap-3">
                        <Link to={`/as/${t.asn}${q}`} className="min-w-0 truncate font-medium hover:text-accent">
                          {t.name || t.handle || `AS${t.asn}`} <span className="font-normal text-muted num">AS{t.asn}</span>
                        </Link>
                        <span className="shrink-0 text-[13px] num">
                          {bytes(t.in + t.out)} <span className="text-muted">({pct(t.in + t.out, total)})</span>
                        </span>
                      </div>
                      <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-sunken" aria-hidden="true">
                        <span style={{ width: `${(t.in / max) * 100}%`, background: link?.color }} />
                        <span style={{ width: `${(t.out / max) * 100}%`, background: link?.color, opacity: 0.45 }} />
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </Panel>
          );
        })}
      </div>
      {visible.length > 0 && <p className="mt-3 text-[13px] text-muted">Bars: inbound in full color, outbound lighter. Percentages are each AS's share of the link's total volume.</p>}
    </>
  );
}
