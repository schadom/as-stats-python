import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, type IxDetail, type IxList } from "../api";
import { FilterBar } from "../components/Layout";
import { AsName, Badge, Button, Empty, ErrorNote, Input, Loading, PageHeader, Select, Toggle, cx, td, th } from "../components/ui";
import { ago, avgBps, bps, bytes, speed } from "../format";
import { filterQuery, navQuery, useFilters, useSession } from "../state";

export default function Ix() {
  const { isAdmin, meta } = useSession();
  const params = useParams();
  const navigate = useNavigate();
  const [f] = useFilters();
  const qc = useQueryClient();
  const [showIdle, setShowIdle] = useState(false);
  const [text, setText] = useState("");

  const list = useQuery({ queryKey: ["ix-list"], queryFn: () => get<IxList>("/ix") });
  const selected = params.id ? Number(params.id) : list.data?.ixs[0]?.id;
  const detail = useQuery({
    queryKey: ["ix", selected, f],
    queryFn: () => get<IxDetail>(`/ix/${selected}?${filterQuery(f)}`),
    enabled: !!selected,
    placeholderData: keepPreviousData,
  });
  const refresh = useMutation({
    mutationFn: () => post("/ix/refresh"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ix-list"] });
      qc.invalidateQueries({ queryKey: ["ix"] });
      qc.invalidateQueries({ queryKey: ["meta"] });
    },
  });

  const members = useMemo(() => {
    const m = detail.data?.members ?? [];
    const needle = text.trim().toLowerCase();
    return m.filter(
      (x) =>
        (showIdle || x.in + x.out > 0) &&
        (!needle || `as${x.asn} ${x.name} ${x.handle ?? ""}`.toLowerCase().includes(needle)),
    );
  }, [detail.data, showIdle, text]);
  const totals = useMemo(() => {
    const m = detail.data?.members ?? [];
    return { active: m.filter((x) => x.in + x.out > 0).length, all: m.length, max: Math.max(1, ...m.map((x) => x.in + x.out)) };
  }, [detail.data]);

  const q = navQuery(f);
  const duration = detail.data ? detail.data.end - detail.data.start : 1;

  if (list.isLoading) return <Loading />;
  if (list.error) return <ErrorNote error={list.error} />;
  const ixs = list.data!.ixs;

  return (
    <>
      <PageHeader
        title="Internet exchanges"
        subtitle={
          list.data!.my_asn
            ? `Exchanges where AS${list.data!.my_asn} is present according to PeeringDB, ${list.data!.last_refresh ? `synced ${ago(list.data!.last_refresh)}` : "not synced yet"}.`
            : "Set your own AS number under General settings to load your exchanges from PeeringDB."
        }
        actions={
          isAdmin && meta.my_asn ? (
            <Button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              {refresh.isPending ? "Refreshing" : "Refresh from PeeringDB"}
            </Button>
          ) : null
        }
      />
      {(refresh.error || list.data!.last_error) && (
        <div className="mb-4">
          <ErrorNote error={refresh.error || list.data!.last_error} />
        </div>
      )}
      {ixs.length === 0 ? (
        <div className="rounded-lg border border-rule bg-surface">
          <Empty title="No exchanges loaded">
            {meta.my_asn
              ? "PeeringDB lists no exchange for your AS yet, or the first sync has not finished."
              : "Enter your AS number in General settings. The member lists are then synced from PeeringDB once a day."}
          </Empty>
        </div>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <label className="sr-only" htmlFor="ix-select">Exchange</label>
            <Select id="ix-select" value={selected} onChange={(e) => navigate(`/ix/${e.target.value}${q}`)} className="w-auto min-w-[16rem] font-medium">
              {ixs.map((ix) => (
                <option key={ix.id} value={ix.id}>
                  {ix.name}
                  {ix.city ? `, ${ix.city}` : ""} ({ix.members} members)
                </option>
              ))}
            </Select>
            {detail.data && (
              <span className="text-muted">
                {totals.active} of {totals.all} members exchanged traffic with you in this range.
              </span>
            )}
          </div>
          <FilterBar
            extra={
              <>
                <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Filter members" className="h-8 w-44" aria-label="Filter members" />
                <Toggle checked={showIdle} onChange={setShowIdle} label="Members without traffic" />
              </>
            }
          />
          {detail.error && <ErrorNote error={detail.error} />}
          {detail.isLoading && <Loading />}
          {detail.data && (
            <div className={cx("overflow-x-auto rounded-lg border border-rule bg-surface", detail.isFetching && "opacity-80")}>
              {members.length === 0 ? (
                <Empty title={showIdle ? "No members match" : "No traffic with members of this exchange"}>
                  {showIdle ? "Change the filter text." : "Turn on “Members without traffic” to see the full member list."}
                </Empty>
              ) : (
                <table className="w-full min-w-[760px]">
                  <thead className="border-b border-rule">
                    <tr>
                      <th className={th}>Member</th>
                      <th className={th}>Port</th>
                      <th className={`${th} w-[28%]`}>Share of the busiest member</th>
                      <th className={`${th} text-right`}>Avg in</th>
                      <th className={`${th} text-right`}>Avg out</th>
                      <th className={`${th} text-right`}>Volume</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((m) => (
                      <tr key={m.asn} className="border-b border-rule last:border-b-0 hover:bg-sunken/40">
                        <td className={td}>
                          <AsName asn={m.asn} name={m.name} handle={m.handle} country={m.country} to={`/as/${m.asn}${q}`} />
                        </td>
                        <td className={td}>
                          <span className="flex flex-wrap gap-1.5">
                            {m.speed ? <Badge>{speed(m.speed)}</Badge> : null}
                            {m.is_rs_peer && <Badge tone="accent" title="Peers with the route servers">RS</Badge>}
                          </span>
                        </td>
                        <td className={td}>
                          <div className="flex h-1.5 overflow-hidden rounded-full bg-sunken" aria-hidden="true">
                            <span className="bg-accent" style={{ width: `${(m.in / totals.max) * 100}%` }} />
                            <span className="bg-accent opacity-45" style={{ width: `${(m.out / totals.max) * 100}%` }} />
                          </div>
                        </td>
                        <td className={`${td} text-right num`}>{bps(avgBps(m.in, duration))}</td>
                        <td className={`${td} text-right num`}>{bps(avgBps(m.out, duration))}</td>
                        <td className={`${td} text-right text-muted num`}>{bytes(m.in + m.out)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
          <p className="mt-3 text-[13px] text-muted">
            Traffic counts every link you selected, not only your port at this exchange. Members you reach over transit show up here too, which is useful for spotting peering candidates.{" "}
            <Link to={`/links${q}`} className="text-accent hover:underline">Compare links</Link>
          </p>
        </>
      )}
    </>
  );
}
