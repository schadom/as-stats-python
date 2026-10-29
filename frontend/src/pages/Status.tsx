import { useQuery } from "@tanstack/react-query";
import { get, type Status as StatusT } from "../api";
import { Badge, Empty, ErrorNote, Loading, PageHeader, Panel, td, th } from "../components/ui";
import { ago, dateTime } from "../format";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-rule py-2 last:border-b-0">
      <dt className="text-muted">{label}</dt>
      <dd className="text-right num">{children}</dd>
    </div>
  );
}

export default function Status() {
  const q = useQuery({ queryKey: ["status"], queryFn: () => get<StatusT>("/status"), refetchInterval: 15_000 });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorNote error={q.error} />;
  const s = q.data!;
  const hb = s.collector;
  const stale = !hb || s.now - hb.ts > 300;

  return (
    <>
      <PageHeader title="Collector status" subtitle={`Version ${s.version}, ${s.database === "sqlite" ? "SQLite" : s.database === "mysql" ? "MariaDB/MySQL" : s.database} database.`} />
      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Collector">
          <dl>
            <Row label="Last write to the database">
              {hb ? ago(hb.ts, s.now) : "never"} {stale ? <Badge tone="danger">Not running?</Badge> : <Badge tone="ok">Running</Badge>}
            </Row>
            <Row label="Flows per second">{hb?.flows_per_sec ?? 0}</Row>
            <Row label="Exporters sending">{hb?.exporters ?? 0}</Row>
            <Row label="Flows dropped, template not yet known">{hb?.missing_template ?? 0}</Row>
          </dl>
          {stale && <p className="mt-3 text-[13px] text-muted">The collector writes once a minute. Check its log if this stays red.</p>}
        </Panel>
        <Panel title="Background jobs">
          <dl>
            <Row label="Hourly data complete up to">{s.rollup["3600"] ? dateTime(s.rollup["3600"]) : "not yet"}</Row>
            <Row label="Daily data complete up to">{s.rollup["86400"] ? dateTime(s.rollup["86400"]) : "not yet"}</Row>
            <Row label="AS names waiting for lookup">{s.asnames_pending}</Row>
            <Row label="Last PeeringDB sync">
              {ago(s.peeringdb.last_refresh, s.now)}
              {s.peeringdb.last_error && <Badge tone="danger" title={s.peeringdb.last_error}>Failed</Badge>}
            </Row>
          </dl>
          {s.peeringdb.last_error && <p className="mt-3 text-[13px] text-danger">{s.peeringdb.last_error}</p>}
        </Panel>
      </div>
      <Panel title="Flow sources" className="mt-5" flush>
        {s.exporters.length === 0 ? (
          <Empty title="No flows received yet">Export NetFlow v5/v9 or IPFIX to UDP 9000, or sFlow to UDP 6343, on this host.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead className="border-b border-rule">
                <tr>
                  <th className={th}>Source</th>
                  <th className={th}>Protocol</th>
                  <th className={`${th} text-right`}>Flows/s</th>
                  <th className={`${th} text-right`}>Packets</th>
                  <th className={`${th} text-right`} title="Undecodable packets plus flows dropped as implausible">Errors</th>
                  <th className={th}>Announced sampling</th>
                  <th className={th}>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {s.exporters.map((e) => (
                  <tr key={e.ip} className="border-b border-rule last:border-b-0">
                    <td className={td}>
                      <div className="font-medium">{e.exporter_name ?? e.ip}</div>
                      {e.exporter_name ? <div className="text-[12.5px] text-muted num">{e.ip}</div> : <Badge tone="warn">Not added, not counted</Badge>}
                    </td>
                    <td className={td}>
                      {e.protocol === "sflow" ? "sFlow v5" : e.protocol === "ipfix" ? "IPFIX" : `NetFlow v${e.version}`}
                      {e.templates != null && <div className="text-[12.5px] text-muted">{e.templates} templates</div>}
                    </td>
                    <td className={`${td} text-right num`}>{e.flows_per_sec}</td>
                    <td className={`${td} text-right num`}>{e.packets.toLocaleString()}</td>
                    <td className={`${td} text-right num ${e.errors ? "text-danger" : ""}`}>{e.errors.toLocaleString()}</td>
                    <td className={`${td} num`}>{e.reported_sampling ? `1:${e.reported_sampling}` : <span className="text-faint">none</span>}</td>
                    <td className={`${td} text-muted`}>{ago(e.last_seen, s.now)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
