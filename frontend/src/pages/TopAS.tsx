import { Fragment, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { get, type SeriesResponse, type TopItem, type TopResponse } from "../api";
import { MirrorSpark, bucketSeconds, layersByLink } from "../components/charts";
import { FilterBar } from "../components/Layout";
import { AsName, Button, Empty, ErrorNote, Loading, PageHeader, Segmented, cx } from "../components/ui";
import { avgBps, bps, bytes, rangeLabel } from "../format";
import { filterQuery, navQuery, useFilters, useSession } from "../state";

type Sort = "total" | "in" | "out";
type Unit = "bandwidth" | "volume";

function ShareBar({ item, colors }: { item: TopItem; colors: Map<number, string> }) {
  const total = item.in + item.out;
  if (!total) return null;
  const parts = Object.entries(item.links)
    .map(([id, v]) => ({ id: Number(id), value: v.in + v.out }))
    .filter((p) => p.value > 0)
    .sort((a, b) => [...colors.keys()].indexOf(a.id) - [...colors.keys()].indexOf(b.id));
  return (
    <div className="mt-1.5 flex h-1 w-full overflow-hidden rounded-full bg-sunken" aria-hidden="true">
      {parts.map((p) => (
        <span key={p.id} style={{ width: `${(p.value / total) * 100}%`, background: colors.get(p.id) ?? "#8b97a3" }} />
      ))}
    </div>
  );
}

export default function TopAS() {
  const { meta, linkById } = useSession();
  const [f] = useFilters();
  const [sort, setSort] = useState<Sort>("total");
  // Kept in the URL (?show=volume) so a view can be shared; bandwidth is the default.
  const [params, setParams] = useSearchParams();
  const unit: Unit = params.get("show") === "volume" ? "volume" : "bandwidth";
  const setUnit = (u: Unit) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (u === "volume") next.set("show", "volume");
        else next.delete("show");
        return next;
      },
      { replace: true },
    );
  const [limit, setLimit] = useState(meta.top_default_limit || 20);

  const top = useQuery({
    queryKey: ["top", f, sort, limit],
    queryFn: () => get<TopResponse>(`/top?${filterQuery(f, { sort, limit })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  const asns = top.data?.items.map((i) => i.asn) ?? [];
  const series = useQuery({
    queryKey: ["series", asns.join(","), f.range, f.links.join(",")],
    queryFn: () => get<SeriesResponse>(`/series?${filterQuery({ ...f, ip: "all" }, { asns: asns.join(",") })}`),
    enabled: asns.length > 0,
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });

  const seconds = useMemo(() => (series.data ? bucketSeconds(series.data) : []), [series.data]);
  const colors = useMemo(() => new Map(meta.links.map((l) => [l.id, l.color])), [meta.links]);
  const duration = top.data ? top.data.end - top.data.start : 1;
  const q = navQuery(f);

  return (
    <>
      <PageHeader
        title="Top AS"
        subtitle={top.data ? `Ranked by ${sort === "total" ? "inbound plus outbound" : sort === "in" ? "inbound" : "outbound"} traffic, ${rangeLabel(top.data.start, top.data.end)}` : " "}
      />
      <FilterBar
        extra={
          <>
            <Segmented
              label="Show"
              value={unit}
              onChange={setUnit}
              options={[
                { id: "volume", label: "Total volume" },
                { id: "bandwidth", label: "Average bandwidth" },
              ]}
            />
            <Segmented
              label="Sort by"
              value={sort}
              onChange={setSort}
              options={[
                { id: "total", label: "Total" },
                { id: "in", label: "In" },
                { id: "out", label: "Out" },
              ]}
            />
          </>
        }
      />
      {top.error && <ErrorNote error={top.error} />}
      {top.isLoading && <Loading />}
      {top.data && top.data.items.length === 0 && (
        <div className="rounded-lg border border-rule bg-surface">
          {meta.links.length === 0 ? (
            <Empty title="No links configured yet">
              Traffic is counted per link. Point your routers at this collector, then turn the interfaces it discovers into links under Links &amp; interfaces.
            </Empty>
          ) : (
            <Empty title="No traffic in this range">
              Nothing was recorded for the selected links and time range. New data shows up within a few minutes of the first flows arriving.
            </Empty>
          )}
        </div>
      )}
      {top.data && top.data.items.length > 0 && (
        <ol className={cx("overflow-hidden rounded-lg border border-rule bg-surface", top.isFetching && "opacity-80")}>
          <li className="hidden grid-cols-[2.5rem_minmax(12rem,17rem)_1fr_11.5rem] gap-4 border-b border-rule px-4 py-2 text-[12.5px] text-muted md:grid" aria-hidden="true">
            <span className="text-right">#</span>
            <span>Autonomous system</span>
            <span className="flex justify-between">
              <span>In above the line, out below</span>
            </span>
            <span className="text-right">{unit === "volume" ? "Total volume" : "Average bandwidth"}</span>
          </li>
          {top.data.items.map((item, idx) => {
            const layers = series.data ? layersByLink(series.data, f.ip, linkById, item.asn) : [];
            return (
              <li
                key={item.asn}
                className="grid grid-cols-[2rem_1fr] items-center gap-x-3 gap-y-2 border-b border-rule px-4 py-3 last:border-b-0 hover:bg-sunken/40 md:grid-cols-[2.5rem_minmax(12rem,17rem)_1fr_11.5rem] md:gap-4"
              >
                <span className="self-start pt-0.5 text-right text-[13px] text-faint num">{idx + 1}</span>
                <div className="min-w-0">
                  <AsName asn={item.asn} name={item.name} handle={item.handle} country={item.country} to={`/as/${item.asn}${q}`} />
                  <ShareBar item={item} colors={colors} />
                </div>
                <div className="col-span-2 md:col-span-1">
                  {series.data ? (
                    <MirrorSpark layers={layers} seconds={seconds} label={`Traffic of AS${item.asn} over time`} />
                  ) : (
                    <div className="h-[52px] animate-pulse rounded bg-sunken" />
                  )}
                </div>
                <dl className="col-span-2 grid grid-cols-[auto_1fr] gap-x-3 text-[13px] md:col-span-1">
                  {(
                    [
                      ["In", item.in, "text-right font-medium num"],
                      ["Out", item.out, "text-right font-medium num"],
                      ["Total", item.in + item.out, "text-right text-muted num"],
                    ] as const
                  ).map(([label, value, cls]) => (
                    <Fragment key={label}>
                      <dt className="text-muted">{label}</dt>
                      <dd
                        className={cls}
                        title={unit === "volume" ? `average ${bps(avgBps(value, duration))}` : `${bytes(value)} in total`}
                      >
                        {unit === "volume" ? bytes(value) : bps(avgBps(value, duration))}
                      </dd>
                    </Fragment>
                  ))}
                </dl>
              </li>
            );
          })}
        </ol>
      )}
      {top.data?.has_more && limit < 500 && (
        <div className="mt-4 flex justify-center">
          <Button onClick={() => setLimit((l) => Math.min(500, l + 20))} disabled={top.isFetching}>
            Show 20 more
          </Button>
        </div>
      )}
    </>
  );
}
