import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, post, put, type Discovered, type Exporter, type LinkFull, type LinkIface } from "../../api";
import { Badge, Button, Empty, ErrorNote, Field, Input, LinkDot, Loading, Modal, PageHeader, Panel, Select, Toggle, cx, td, th } from "../../components/ui";
import { ago, bps, speed } from "../../format";
import { ExporterModal } from "./SettingsExporters";

export const PALETTE = ["#3d7be0", "#e0703d", "#2fa37a", "#a35bd6", "#d64570", "#c9a227", "#3fb3c9", "#7a8b2e", "#8a5a44", "#5c6bc0"];

function slug(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32);
}

interface Draft {
  id?: number;
  name: string;
  tag: string;
  color: string;
  sort_order: number;
  enabled: boolean;
  capacity_mbit?: number | null;
  interfaces: LinkIface[];
}

function invalidateAll(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["links-full"] });
  qc.invalidateQueries({ queryKey: ["discovered"] });
  qc.invalidateQueries({ queryKey: ["meta"] });
}

export function LinkEditor({ draft, exporters, onClose }: { draft: Draft; exporters: Exporter[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>(draft);
  const [tagTouched, setTagTouched] = useState(!!draft.id);
  const set = (patch: Partial<Draft>) => setD((prev) => ({ ...prev, ...patch }));
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: d.name.trim(),
        tag: d.tag.trim(),
        color: d.color,
        sort_order: d.sort_order,
        enabled: d.enabled,
        capacity_mbit: d.capacity_mbit || null,
        interfaces: d.interfaces.map((i) => ({ exporter_id: i.exporter_id, ifindex: i.ifindex, sampling_rate: i.sampling_rate || null })),
      };
      return d.id ? put(`/links/${d.id}`, body) : post("/links", body);
    },
    onSuccess: () => {
      invalidateAll(qc);
      onClose();
    },
  });
  // Capacity is stored in Mbit/s; edit it in Gbit/s when it is a whole number of those.
  const [capUnit, setCapUnit] = useState<1 | 1000>(draft.capacity_mbit && draft.capacity_mbit % 1000 === 0 ? 1000 : 1);
  const capValue = d.capacity_mbit ? d.capacity_mbit / capUnit : "";
  const setIface = (idx: number, patch: Partial<LinkIface>) =>
    set({ interfaces: d.interfaces.map((i, k) => (k === idx ? { ...i, ...patch } : i)) });

  return (
    <Modal
      open
      wide
      onClose={onClose}
      title={d.id ? `Edit ${draft.name}` : "New link"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending || !d.name.trim() || !d.tag.trim()}>
            {d.id ? "Save link" : "Create link"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" hint="Shown in charts and legends, for example “Transit Cogent”.">
          <Input
            value={d.name}
            autoFocus
            onChange={(e) => set({ name: e.target.value, ...(tagTouched ? {} : { tag: slug(e.target.value) }) })}
          />
        </Field>
        <Field label="Short tag" hint="Letters, digits, dots, dashes. Used as a stable identifier.">
          <Input
            value={d.tag}
            onChange={(e) => {
              setTagTouched(true);
              set({ tag: e.target.value });
            }}
          />
        </Field>
      </div>
      <div className="mt-4">
        <span className="mb-1.5 block text-[13px] font-medium text-ink-2">Color</span>
        <div className="flex flex-wrap items-center gap-2">
          {PALETTE.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`Use color ${c}`}
              aria-pressed={d.color === c}
              onClick={() => set({ color: c })}
              className={cx("h-7 w-7 rounded-md border-2", d.color === c ? "border-ink" : "border-transparent")}
              style={{ background: c }}
            />
          ))}
          <label className="ml-1 inline-flex items-center gap-2 text-[13px] text-muted">
            Custom
            <input type="color" value={d.color} onChange={(e) => set({ color: e.target.value })} className="h-7 w-9 cursor-pointer rounded border border-rule-strong bg-surface" />
          </label>
        </div>
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field label="Order" hint="Lower numbers are listed and stacked first.">
          <Input type="number" value={d.sort_order} onChange={(e) => set({ sort_order: Number(e.target.value) || 0 })} />
        </Field>
        <div className="pt-6">
          <Toggle checked={d.enabled} onChange={(v) => set({ enabled: v })} label="Count traffic on this link" />
        </div>
        <div>
          <label htmlFor="link-capacity" className="mb-1 block text-[13px] font-medium text-ink-2">
            Capacity
          </label>
          <div className="flex gap-2">
            <Input
              id="link-capacity"
              type="number"
              min={0}
              step="any"
              placeholder="Not set"
              aria-describedby="link-capacity-hint"
              className="min-w-0 flex-1"
              value={capValue}
              onChange={(e) => set({ capacity_mbit: e.target.value ? Math.round(Number(e.target.value) * capUnit) || null : null })}
            />
            <div className="w-28 shrink-0">
              <Select aria-label="Capacity unit" value={capUnit} onChange={(e) => setCapUnit(Number(e.target.value) as 1 | 1000)}>
                <option value={1}>Mbit/s</option>
                <option value={1000}>Gbit/s</option>
              </Select>
            </div>
          </div>
          <p id="link-capacity-hint" className="mt-1 text-[12.5px] text-muted">
            Optional, per direction. Shows how full the link is on the Links page.
          </p>
        </div>
      </div>

      <div className="mt-6">
        <div className="mb-2 flex items-center justify-between">
          <span className="font-semibold">Interfaces</span>
          <Button
            size="sm"
            disabled={!exporters.length}
            onClick={() => set({ interfaces: [...d.interfaces, { exporter_id: exporters[0].id, ifindex: 0, sampling_rate: null }] })}
          >
            Add interface
          </Button>
        </div>
        {!exporters.length && <p className="text-muted">Add an exporter first, then pick its interfaces here.</p>}
        {d.interfaces.length === 0 && exporters.length > 0 && (
          <p className="text-muted">A link counts traffic from one or more router interfaces, for example both routers facing the same transit provider.</p>
        )}
        {d.interfaces.length > 0 && (
          <table className="w-full">
            <thead>
              <tr>
                <th className={`${th} pl-0`}>Exporter</th>
                <th className={th}>ifIndex</th>
                <th className={th}>Sampling rate</th>
                <th className={th}><span className="sr-only">Remove</span></th>
              </tr>
            </thead>
            <tbody>
              {d.interfaces.map((i, idx) => (
                <tr key={idx}>
                  <td className="py-1 pr-2">
                    <Select value={i.exporter_id} onChange={(e) => setIface(idx, { exporter_id: Number(e.target.value) })} aria-label="Exporter">
                      {exporters.map((x) => (
                        <option key={x.id} value={x.id}>{x.name} ({x.ip})</option>
                      ))}
                    </Select>
                  </td>
                  <td className="w-32 py-1 pr-2">
                    <Input type="number" min={0} value={i.ifindex} onChange={(e) => setIface(idx, { ifindex: Number(e.target.value) || 0 })} aria-label="ifIndex" />
                    {i.if_name && <div className="mt-0.5 truncate text-[12px] text-muted">{i.if_name}</div>}
                  </td>
                  <td className="w-40 py-1 pr-2">
                    <Input
                      type="number"
                      min={1}
                      placeholder="Exporter default"
                      value={i.sampling_rate ?? ""}
                      onChange={(e) => setIface(idx, { sampling_rate: e.target.value ? Number(e.target.value) : null })}
                      aria-label="Sampling rate override"
                    />
                  </td>
                  <td className="w-10 py-1 text-right">
                    <Button size="sm" variant="ghost" aria-label="Remove interface" onClick={() => set({ interfaces: d.interfaces.filter((_, k) => k !== idx) })}>
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {save.error && <div className="mt-4"><ErrorNote error={save.error} /></div>}
    </Modal>
  );
}

function AssignModal({
  row,
  links,
  exporters,
  onClose,
  onNewLink,
}: {
  row: Discovered;
  links: LinkFull[];
  exporters: Exporter[];
  onClose: () => void;
  onNewLink: () => void;
}) {
  const qc = useQueryClient();
  const [target, setTarget] = useState<number>(links[0]?.id ?? 0);
  const add = useMutation({
    mutationFn: () => {
      const link = links.find((l) => l.id === target)!;
      return put(`/links/${link.id}`, {
        ...link,
        interfaces: [
          ...link.interfaces.map((i) => ({ exporter_id: i.exporter_id, ifindex: i.ifindex, sampling_rate: i.sampling_rate })),
          { exporter_id: row.exporter_id, ifindex: row.ifindex, sampling_rate: null },
        ],
      });
    },
    onSuccess: () => {
      invalidateAll(qc);
      onClose();
    },
  });
  const exp = exporters.find((e) => e.id === row.exporter_id);
  return (
    <Modal open onClose={onClose} title={`Use ${row.if_name || `ifIndex ${row.ifindex}`} as a link`}>
      <p className="mb-4 text-muted">
        On {exp?.name ?? row.exporter_ip}. Traffic through this interface will be counted per AS from the next minute on.
      </p>
      <div className="grid gap-3">
        <Button variant="primary" onClick={onNewLink} className="justify-start">Create a new link with this interface</Button>
        {links.length > 0 && (
          <div className="rounded-md border border-rule p-3">
            <div className="flex items-end gap-2">
              <Field label="Or add it to an existing link" className="flex-1">
                <Select value={target} onChange={(e) => setTarget(Number(e.target.value))}>
                  {links.map((l) => (
                    <option key={l.id} value={l.id}>{l.name}</option>
                  ))}
                </Select>
              </Field>
              <Button onClick={() => add.mutate()} disabled={add.isPending}>Add to link</Button>
            </div>
          </div>
        )}
      </div>
      {add.error && <div className="mt-3"><ErrorNote error={add.error} /></div>}
    </Modal>
  );
}

export default function SettingsLinks() {
  const qc = useQueryClient();
  const links = useQuery({ queryKey: ["links-full"], queryFn: () => get<LinkFull[]>("/links") });
  const exporters = useQuery({ queryKey: ["exporters"], queryFn: () => get<Exporter[]>("/exporters") });
  const discovered = useQuery({ queryKey: ["discovered"], queryFn: () => get<Discovered[]>("/discovered"), refetchInterval: 30_000 });
  const [editing, setEditing] = useState<Draft | null>(null);
  const [assigning, setAssigning] = useState<Discovered | null>(null);
  const [addExporterIp, setAddExporterIp] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<LinkFull | null>(null);
  const [showAssigned, setShowAssigned] = useState(false);

  const remove = useMutation({
    mutationFn: (id: number) => del(`/links/${id}`),
    onSuccess: () => {
      invalidateAll(qc);
      setDeleting(null);
    },
  });
  const forget = useMutation({
    mutationFn: (r: Discovered) => del(`/discovered?exporter_ip=${encodeURIComponent(r.exporter_ip)}&ifindex=${r.ifindex}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovered"] }),
  });

  const rows = useMemo(
    () =>
      (discovered.data ?? [])
        .filter((r) => showAssigned || r.link_id === null)
        .sort((a, b) => b.bps_in + b.bps_out - (a.bps_in + a.bps_out)),
    [discovered.data, showAssigned],
  );
  const nextColor = PALETTE[(links.data?.length ?? 0) % PALETTE.length];
  const newDraft = (ifaces: LinkIface[] = [], name = ""): Draft => ({
    name,
    tag: slug(name),
    color: nextColor,
    sort_order: (links.data?.length ?? 0) * 10,
    enabled: true,
    interfaces: ifaces,
  });

  if (links.isLoading || exporters.isLoading) return <Loading />;
  const exp = exporters.data ?? [];

  return (
    <>
      <PageHeader
        title="Links & interfaces"
        subtitle="A link is a transit, peering or IX connection. Traffic entering a link is counted for the source AS, traffic leaving it for the destination AS."
        actions={<Button variant="primary" onClick={() => setEditing(newDraft())} disabled={!exp.length}>New link</Button>}
      />
      {links.error && <ErrorNote error={links.error} />}

      <Panel title="Links" flush>
        {links.data && links.data.length === 0 ? (
          <Empty title="No links yet">
            {exp.length
              ? "Pick an interface from the list below, or create a link and enter the ifIndex yourself."
              : "Interfaces appear below as soon as a router sends flows here. Add the router as an exporter first."}
          </Empty>
        ) : (
          <ul>
            {links.data!.map((l) => (
              <li key={l.id} className="flex flex-wrap items-start gap-4 border-b border-rule px-4 py-3 last:border-b-0">
                <LinkDot color={l.color} className="mt-1.5 h-3 w-3" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{l.name}</span>
                    <span className="text-[13px] text-muted">{l.tag}</span>
                    {!l.enabled && <Badge tone="warn">Paused</Badge>}
                    {l.capacity_mbit ? <Badge>{speed(l.capacity_mbit)}</Badge> : null}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {l.interfaces.length === 0 && <span className="text-[13px] text-warn">No interfaces, nothing is counted</span>}
                    {l.interfaces.map((i) => (
                      <Badge key={`${i.exporter_id}-${i.ifindex}`} title={i.sampling_rate ? `Sampling rate ${i.sampling_rate}` : undefined}>
                        {i.exporter_name} ifIndex {i.ifindex}
                        {i.if_name ? ` (${i.if_name})` : ""}
                      </Badge>
                    ))}
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setEditing({ ...l })}>Edit</Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleting(l)}>Delete</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        className="mt-6"
        flush
        title="Interfaces seen by the collector"
        actions={<Toggle checked={showAssigned} onChange={setShowAssigned} label="Include interfaces already in a link" />}
      >
        {discovered.error && <ErrorNote error={discovered.error} />}
        {rows.length === 0 ? (
          <Empty title={discovered.data?.length ? "Every interface is assigned" : "No flows received yet"}>
            {discovered.data?.length
              ? "Turn on the switch above to see them all."
              : "Configure your routers to export NetFlow v5/v9, IPFIX (UDP 9000) or sFlow (UDP 6343) to this host. Interfaces show up here within a minute."}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px]">
              <thead className="border-b border-rule">
                <tr>
                  <th className={th}>Exporter</th>
                  <th className={th}>Interface</th>
                  <th className={`${th} text-right`}>Recent in</th>
                  <th className={`${th} text-right`}>Recent out</th>
                  <th className={th}>Last seen</th>
                  <th className={th}>Link</th>
                  <th className={th}><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={`${r.exporter_ip}-${r.ifindex}`} className="border-b border-rule last:border-b-0">
                    <td className={td}>
                      <div className="font-medium">{r.exporter_name ?? r.exporter_ip}</div>
                      {r.exporter_name ? <div className="text-[12.5px] text-muted num">{r.exporter_ip}</div> : <Badge tone="warn">Not added yet</Badge>}
                    </td>
                    <td className={td}>
                      <div className="num">ifIndex {r.ifindex}</div>
                      {r.if_name && <div className="text-[12.5px] text-muted">{r.if_name}</div>}
                    </td>
                    <td className={`${td} text-right num`}>{bps(r.bps_in)}</td>
                    <td className={`${td} text-right num`}>{bps(r.bps_out)}</td>
                    <td className={`${td} text-muted`}>{ago(r.last_seen)}</td>
                    <td className={td}>{r.link_name ?? <span className="text-faint">None</span>}</td>
                    <td className={`${td} text-right whitespace-nowrap`}>
                      {r.link_id === null &&
                        (r.exporter_id === null ? (
                          <Button size="sm" onClick={() => setAddExporterIp(r.exporter_ip)}>Add exporter</Button>
                        ) : (
                          <Button size="sm" variant="primary" onClick={() => setAssigning(r)}>Use as link</Button>
                        ))}
                      <Button size="sm" variant="ghost" className="ml-1" onClick={() => forget.mutate(r)} title="Remove from this list. It reappears if traffic is seen again.">
                        Hide
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <p className="mt-3 text-[13px] text-muted">Rates are averages over the collector's last flush interval, after applying the sampling rate.</p>

      {editing && <LinkEditor draft={editing} exporters={exp} onClose={() => setEditing(null)} />}
      {assigning && (
        <AssignModal
          row={assigning}
          links={links.data ?? []}
          exporters={exp}
          onClose={() => setAssigning(null)}
          onNewLink={() => {
            const r = assigning;
            setAssigning(null);
            setEditing(newDraft([{ exporter_id: r.exporter_id!, ifindex: r.ifindex, if_name: r.if_name, sampling_rate: null }], r.if_name ?? ""));
          }}
        />
      )}
      {addExporterIp && <ExporterModal initial={{ ip: addExporterIp }} onClose={() => setAddExporterIp(null)} />}
      <Modal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.name ?? ""}?`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>Keep link</Button>
            <Button variant="danger" onClick={() => deleting && remove.mutate(deleting.id)} disabled={remove.isPending}>Delete link and its data</Button>
          </>
        }
      >
        <p>All traffic history recorded for this link is deleted as well. To stop counting but keep the history, edit the link and switch counting off instead.</p>
        {remove.error && <div className="mt-3"><ErrorNote error={remove.error} /></div>}
      </Modal>
    </>
  );
}
