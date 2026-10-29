import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, put, type AsSource, type Exporter, type Status } from "../../api";
import { Badge, Button, Empty, ErrorNote, Field, Input, Loading, Modal, PageHeader, Panel, Select, Toggle, td, th } from "../../components/ui";
import { ago } from "../../format";

type Draft = Partial<Omit<Exporter, "status">>;

export const AS_SOURCE_LABEL: Record<AsSource, string> = {
  auto: "From the router, else IP lookup",
  router: "From the router only",
  lookup: "IP lookup only",
};

/** Docker's bridge gateways: flows seen from here had their source address rewritten. */
export function isDockerGateway(ip: string): boolean {
  return /^172\.(1[6-9]|2\d|3[01])\.0\.1$/.test(ip);
}

export function DockerGatewayNote() {
  return (
    <div className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-[13px] text-ink-2">
      This is Docker's internal gateway, not a router. Docker rewrote the source address of the incoming flows, so all
      routers look like one exporter and their templates get mixed up. Run the container with host networking
      (<code>--network host</code>), then add the routers under their real addresses.
    </div>
  );
}

export function ExporterModal({ initial, onClose }: { initial: Draft; onClose: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>({ name: "", as_mode: "origin", as_source: "auto", enabled: true, sampling_rate: null, ...initial });
  const save = useMutation({
    mutationFn: () => {
      const body = {
        ip: (d.ip ?? "").trim(),
        name: (d.name ?? "").trim() || (d.ip ?? "").trim(),
        sampling_rate: d.sampling_rate || null,
        as_mode: d.as_mode,
        as_source: d.as_source,
        enabled: d.enabled,
      };
      return d.id ? put(`/exporters/${d.id}`, body) : post("/exporters", body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["exporters"] });
      qc.invalidateQueries({ queryKey: ["discovered"] });
      qc.invalidateQueries({ queryKey: ["status"] });
      onClose();
    },
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={d.id ? `Edit ${initial.name}` : "Add exporter"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending || !d.ip}>
            {d.id ? "Save exporter" : "Add exporter"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label="Source IP address" hint="The address flow packets arrive from, as shown on the collector status page.">
          <Input value={d.ip ?? ""} onChange={(e) => setD({ ...d, ip: e.target.value })} autoFocus={!d.ip} />
        </Field>
        {d.ip && isDockerGateway(d.ip.trim()) && <DockerGatewayNote />}
        <Field label="Name">
          <Input value={d.name ?? ""} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="edge1.vie" autoFocus={!!d.ip} />
        </Field>
        <Field
          label="Sampling rate"
          hint="Leave empty to use the rate the router announces (NetFlow v9/IPFIX options, sFlow samples). Set it if the router does not announce one."
        >
          <Input
            type="number"
            min={1}
            value={d.sampling_rate ?? ""}
            placeholder="Announced by the router"
            onChange={(e) => setD({ ...d, sampling_rate: e.target.value ? Number(e.target.value) : null })}
          />
        </Field>
        <Field
          label="Where AS numbers come from"
          hint="Many routers (VyOS, MikroTik, Linux) and most switches send no AS numbers. IP lookup finds the AS announcing each address, using a dataset refreshed daily."
        >
          <Select value={d.as_source} onChange={(e) => setD({ ...d, as_source: e.target.value as AsSource })}>
            <option value="auto">{AS_SOURCE_LABEL.auto}</option>
            <option value="router">{AS_SOURCE_LABEL.router}</option>
            <option value="lookup">{AS_SOURCE_LABEL.lookup}</option>
          </Select>
        </Field>
        <Field label="AS numbers to count" hint="Neighbor AS needs the router to export BGP adjacent AS fields (IPFIX 128/129, sFlow extended gateway). IP lookup always finds the origin AS.">
          <Select value={d.as_mode} onChange={(e) => setD({ ...d, as_mode: e.target.value as "origin" | "peer" })}>
            <option value="origin">Origin AS (where traffic starts or ends)</option>
            <option value="peer">Neighbor AS (the next hop network)</option>
          </Select>
        </Field>
        <Toggle checked={!!d.enabled} onChange={(v) => setD({ ...d, enabled: v })} label="Count traffic from this exporter" />
      </div>
      {save.error && <div className="mt-4"><ErrorNote error={save.error} /></div>}
    </Modal>
  );
}

export default function SettingsExporters() {
  const qc = useQueryClient();
  const exporters = useQuery({ queryKey: ["exporters"], queryFn: () => get<Exporter[]>("/exporters"), refetchInterval: 30_000 });
  const status = useQuery({ queryKey: ["status"], queryFn: () => get<Status>("/status"), refetchInterval: 30_000 });
  const [editing, setEditing] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<Exporter | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => del(`/exporters/${id}`),
    onSuccess: () => {
      qc.invalidateQueries();
      setDeleting(null);
    },
  });
  const unknown = (status.data?.exporters ?? []).filter((s) => !s.exporter_id);

  if (exporters.isLoading) return <Loading />;
  return (
    <>
      <PageHeader
        title="Exporters"
        subtitle="Routers and switches sending NetFlow, IPFIX or sFlow. Only traffic from exporters listed here is counted."
        actions={<Button variant="primary" onClick={() => setEditing({})}>Add exporter</Button>}
      />
      {exporters.error && <ErrorNote error={exporters.error} />}
      {unknown.length > 0 && (
        <Panel title="Sending flows but not added" className="mb-6" flush>
          <ul>
            {unknown.map((s) => (
              <li key={s.ip} className="flex flex-wrap items-center justify-between gap-3 border-b border-rule px-4 py-2.5 last:border-b-0">
                <span>
                  <span className="font-medium num">{s.ip}</span>
                  <span className="ml-2 text-muted">
                    {s.protocol === "sflow" ? "sFlow" : s.protocol === "ipfix" ? "IPFIX" : `NetFlow v${s.version}`}, {s.flows_per_sec} flows/s, last seen {ago(s.last_seen)}
                  </span>
                </span>
                <Button size="sm" variant="primary" onClick={() => setEditing({ ip: s.ip })}>Add exporter</Button>
                {isDockerGateway(s.ip) && <div className="w-full"><DockerGatewayNote /></div>}
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <Panel flush>
        {exporters.data!.length === 0 ? (
          <Empty title="No exporters yet">Send flows to this host on UDP 9000 (NetFlow/IPFIX) or 6343 (sFlow). Exporters that send flows appear above so you can add them with one click.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px]">
              <thead className="border-b border-rule">
                <tr>
                  <th className={th}>Exporter</th>
                  <th className={th}>Protocol</th>
                  <th className={th}>Sampling</th>
                  <th className={th}>AS numbers</th>
                  <th className={th}>Last seen</th>
                  <th className={th}><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {exporters.data!.map((e) => (
                  <tr key={e.id} className="border-b border-rule last:border-b-0">
                    <td className={td}>
                      <div className="font-medium">{e.name}</div>
                      <div className="text-[12.5px] text-muted num">{e.ip}</div>
                      {isDockerGateway(e.ip) && <Badge tone="warn" title="Docker rewrote the source address, see the note when editing">Docker gateway</Badge>}
                    </td>
                    <td className={td}>
                      {e.status ? (
                        <>
                          {e.status.protocol === "sflow" ? "sFlow v5" : e.status.protocol === "ipfix" ? "IPFIX" : `NetFlow v${e.status.version}`}
                          <div className="text-[12.5px] text-muted num">
                            {e.status.flows_per_sec} flows/s
                            {e.status.errors > 0 && `, ${e.status.errors} errors`}
                          </div>
                        </>
                      ) : (
                        <span className="text-faint">No flows yet</span>
                      )}
                    </td>
                    <td className={`${td} num`}>
                      {e.sampling_rate ? (
                        <>1:{e.sampling_rate} <Badge>fixed</Badge></>
                      ) : e.status?.reported_sampling ? (
                        <>1:{e.status.reported_sampling} <Badge tone="ok">announced</Badge></>
                      ) : (
                        <span className="text-warn" title="Counters are not scaled. Set a rate if the router samples.">none announced</span>
                      )}
                    </td>
                    <td className={td}>
                      {e.as_mode === "peer" ? "Neighbor AS" : "Origin AS"}
                      <div className="text-[12.5px] text-muted">{AS_SOURCE_LABEL[e.as_source] ?? e.as_source}</div>
                    </td>
                    <td className={td}>
                      {e.status ? ago(e.status.last_seen) : <span className="text-faint">never</span>}
                      {!e.enabled && <Badge tone="warn">Paused</Badge>}
                    </td>
                    <td className={`${td} text-right whitespace-nowrap`}>
                      <Button size="sm" onClick={() => setEditing(e)}>Edit</Button>
                      <Button size="sm" variant="danger" className="ml-2" onClick={() => setDeleting(e)}>Delete</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {editing && <ExporterModal initial={editing} onClose={() => setEditing(null)} />}
      <Modal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.name ?? ""}?`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>Keep exporter</Button>
            <Button variant="danger" onClick={() => deleting && remove.mutate(deleting.id)} disabled={remove.isPending}>Delete exporter</Button>
          </>
        }
      >
        <p>Its interfaces are removed from all links. Traffic history stays, but nothing new is counted from this router.</p>
        {remove.error && <div className="mt-3"><ErrorNote error={remove.error} /></div>}
      </Modal>
    </>
  );
}
