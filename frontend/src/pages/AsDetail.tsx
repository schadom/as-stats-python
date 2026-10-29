import { useMemo, useState } from "react";
import { useParams } from "react-router";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, patch, post, type AsDetail as AsDetailT, type SeriesResponse } from "../api";
import { ChartLegend, TrafficChart, bucketSeconds, layersByFamily, layersByLink } from "../components/charts";
import { FilterBar } from "../components/Layout";
import { Badge, Button, Empty, ErrorNote, Field, Input, LinkDot, Loading, Modal, Panel, Segmented, td, th } from "../components/ui";
import { avgBps, bps, bytes, rangeLabel, speed } from "../format";
import { filterQuery, useFilters, useSession } from "../state";

function Rename({ data, onClose }: { data: AsDetailT; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(data.custom_name ?? "");
  const save = useMutation({
    mutationFn: () => patch(`/as/${data.asn}`, { custom_name: name }),
    onSuccess: () => {
      qc.invalidateQueries();
      onClose();
    },
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={`Name for AS${data.asn}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>Save name</Button>
        </>
      }
    >
      <Field label="Display name" hint="Shown everywhere instead of the registry name. Leave empty to use the registry name again.">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={data.description || data.handle || ""} autoFocus />
      </Field>
      {save.error && <div className="mt-3"><ErrorNote error={save.error} /></div>}
    </Modal>
  );
}

export default function AsDetail() {
  const asn = Number(useParams().asn);
  const { isAdmin, linkById, meta } = useSession();
  const [f] = useFilters();
  const [view, setView] = useState<"links" | "family">("links");
  const [renaming, setRenaming] = useState(false);
  const qc = useQueryClient();

  const detail = useQuery({
    queryKey: ["as", asn, f.range],
    queryFn: () => get<AsDetailT>(`/as/${asn}?${filterQuery(f)}`),
    enabled: Number.isFinite(asn),
    placeholderData: keepPreviousData,
  });
  const series = useQuery({
    queryKey: ["series", String(asn), f.range, f.links.join(",")],
    queryFn: () => get<SeriesResponse>(`/series?${filterQuery({ ...f, ip: "all" }, { asns: asn })}`),
    enabled: Number.isFinite(asn),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  const refresh = useMutation({
    mutationFn: () => post(`/as/${asn}/refresh`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["as", asn] }),
  });

  const layers = useMemo(() => {
    if (!series.data) return [];
    return view === "links"
      ? layersByLink(series.data, f.ip, linkById, asn)
      : layersByFamily(series.data, ["#3d7be0", "#d48a1c"]);
  }, [series.data, view, f.ip, linkById, asn]);
  const seconds = useMemo(() => (series.data ? bucketSeconds(series.data) : []), [series.data]);

  if (!Number.isFinite(asn)) return <ErrorNote error="That is not an AS number." />;
  if (detail.isLoading) return <Loading />;
  if (detail.error) return <ErrorNote error={detail.error} />;
  const d = detail.data!;
  const duration = d.end - d.start;
  const visibleLinks = d.links.filter((l) => !f.links.length || f.links.includes(l.link_id));
  const sum = (k: "in_v4" | "out_v4" | "in_v6" | "out_v6") => visibleLinks.reduce((a, l) => a + l[k], 0);
  const hasTraffic = visibleLinks.some((l) => l.in_v4 + l.out_v4 + l.in_v6 + l.out_v6 > 0);
  const title = d.name || d.handle || `AS${asn}`;

  return (
    <>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-[13px] text-muted num">AS{asn}</div>
          <h1 className="text-[24px] leading-tight font-semibold tracking-[-0.01em]">{title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-muted">
            {d.handle && d.handle !== title && <span>{d.handle}</span>}
            {d.country && <Badge>{d.country}</Badge>}
            {d.custom_name && d.description && <span>Registry name: {d.description}</span>}
            {d.pdb_name && d.pdb_name !== title && <span>PeeringDB: {d.pdb_name}</span>}
            {d.updated_at === 0 && <Badge tone="warn">Name lookup pending</Badge>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {d.pdb_id && (
            <a className="h-9 rounded-md border border-rule-strong bg-surface px-3.5 leading-9 font-medium hover:bg-sunken" href={`https://www.peeringdb.com/net/${d.pdb_id}`} target="_blank" rel="noreferrer">
              PeeringDB
            </a>
          )}
          <a className="h-9 rounded-md border border-rule-strong bg-surface px-3.5 leading-9 font-medium hover:bg-sunken" href={`https://bgp.tools/as/${asn}`} target="_blank" rel="noreferrer">
            bgp.tools
          </a>
          {isAdmin && (
            <>
              <Button onClick={() => setRenaming(true)}>Rename</Button>
              <Button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                {refresh.isPending ? "Looking up" : "Look up name again"}
              </Button>
            </>
          )}
        </div>
      </div>
      {refresh.error && <div className="mb-4"><ErrorNote error={refresh.error} /></div>}
      <FilterBar />

      <Panel
        title="Traffic"
        actions={
          <Segmented
            label="Split by"
            value={view}
            onChange={setView}
            options={[
              { id: "links", label: "By link" },
              { id: "family", label: "IPv4 and IPv6" },
            ]}
          />
        }
      >
        {series.data && layers.length > 0 ? (
          <>
            <div className="mb-3 text-[13px] text-muted">{rangeLabel(d.start, d.end)}. Inbound is drawn above the line, outbound below.</div>
            <TrafficChart layers={layers} buckets={series.data.buckets} seconds={seconds} height={340} />
            <div className="mt-3">
              <ChartLegend layers={layers} seconds={duration} />
            </div>
          </>
        ) : series.isLoading ? (
          <Loading />
        ) : (
          <Empty title="No traffic in this range">Try a longer time range or include more links.</Empty>
        )}
      </Panel>

      <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_22rem]">
        <Panel title="Per link" flush>
          {hasTraffic ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px]">
                <thead className="border-b border-rule">
                  <tr>
                    <th className={th}>Link</th>
                    <th className={`${th} text-right`}>In, IPv4</th>
                    <th className={`${th} text-right`}>In, IPv6</th>
                    <th className={`${th} text-right`}>Out, IPv4</th>
                    <th className={`${th} text-right`}>Out, IPv6</th>
                    <th className={`${th} text-right`}>Avg in / out</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleLinks
                    .slice()
                    .sort((a, b) => meta.links.findIndex((l) => l.id === a.link_id) - meta.links.findIndex((l) => l.id === b.link_id))
                    .map((l) => {
                      const link = linkById.get(l.link_id);
                      return (
                        <tr key={l.link_id} className="border-b border-rule last:border-b-0">
                          <td className={td}>
                            <span className="inline-flex items-center gap-2 font-medium">
                              <LinkDot color={link?.color ?? "#8b97a3"} />
                              {link?.name ?? `Link ${l.link_id}`}
                            </span>
                          </td>
                          <td className={`${td} text-right num`}>{bytes(l.in_v4)}</td>
                          <td className={`${td} text-right num`}>{bytes(l.in_v6)}</td>
                          <td className={`${td} text-right num`}>{bytes(l.out_v4)}</td>
                          <td className={`${td} text-right num`}>{bytes(l.out_v6)}</td>
                          <td className={`${td} text-right num`}>
                            {bps(avgBps(l.in_v4 + l.in_v6, duration))} / {bps(avgBps(l.out_v4 + l.out_v6, duration))}
                          </td>
                        </tr>
                      );
                    })}
                  {visibleLinks.length > 1 && (
                    <tr className="border-t border-rule-strong font-semibold">
                      <td className={td}>All links</td>
                      <td className={`${td} text-right num`}>{bytes(sum("in_v4"))}</td>
                      <td className={`${td} text-right num`}>{bytes(sum("in_v6"))}</td>
                      <td className={`${td} text-right num`}>{bytes(sum("out_v4"))}</td>
                      <td className={`${td} text-right num`}>{bytes(sum("out_v6"))}</td>
                      <td className={`${td} text-right num`}>
                        {bps(avgBps(sum("in_v4") + sum("in_v6"), duration))} / {bps(avgBps(sum("out_v4") + sum("out_v6"), duration))}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No traffic on these links" />
          )}
        </Panel>

        <Panel title="Internet exchanges" flush>
          {d.ixs.length ? (
            <ul>
              {d.ixs.map((ix, i) => (
                <li key={`${ix.ix_id}-${i}`} className="border-b border-rule px-4 py-2.5 last:border-b-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{ix.name}</span>
                    <span className="flex gap-1.5">
                      {ix.speed ? <Badge>{speed(ix.speed)}</Badge> : null}
                      {ix.is_rs_peer && <Badge tone="accent" title="Peers with the route servers">RS</Badge>}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[12.5px] text-muted num">{[ix.ipaddr4, ix.ipaddr6].filter(Boolean).join(", ")}</div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="px-4 py-5 text-muted">
              {meta.my_asn ? "Not present at any IX where you are present, according to PeeringDB." : "Set your own AS number under General settings to see shared IXs."}
            </div>
          )}
        </Panel>
      </div>
      {renaming && <Rename data={d} onClose={() => setRenaming(false)} />}
    </>
  );
}
