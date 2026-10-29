import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, put, type Settings } from "../../api";
import { Button, ErrorNote, Field, Input, Loading, PageHeader, Panel, Select, Toggle } from "../../components/ui";

export default function SettingsGeneral() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => get<Settings>("/settings") });
  const [s, setS] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (settings.data) setS(settings.data);
  }, [settings.data]);
  const save = useMutation({
    mutationFn: (body: Settings) => put<Settings>("/settings", body),
    onSuccess: (data) => {
      qc.setQueryData(["settings"], data);
      qc.invalidateQueries({ queryKey: ["meta"] });
      qc.invalidateQueries({ queryKey: ["ix-list"] });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    },
  });
  if (settings.isLoading || !s) return <Loading />;
  const set = (patch: Partial<Settings>) => setS({ ...s, ...patch });
  const num = (v: string) => (v === "" ? 0 : Number(v));

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({ ...s, my_asn: s.my_asn || null });
      }}
    >
      <PageHeader
        title="General"
        actions={
          <>
            {saved && <span className="text-ok">Settings saved</span>}
            <Button type="submit" variant="primary" disabled={save.isPending}>Save settings</Button>
          </>
        }
      />
      {save.error && <div className="mb-4"><ErrorNote error={save.error} /></div>}
      <div className="grid gap-5 xl:grid-cols-2">
        <Panel title="Your network">
          <div className="grid gap-4">
            <Field label="Site name" hint="Shown in the sidebar.">
              <Input value={s.site_name} onChange={(e) => set({ site_name: e.target.value })} maxLength={64} />
            </Field>
            <Field label="Your AS number" hint="Used to load the exchanges you are present at, and their members, from PeeringDB.">
              <Input type="number" min={0} value={s.my_asn ?? ""} onChange={(e) => set({ my_asn: e.target.value ? Number(e.target.value) : null })} placeholder="64500" />
            </Field>
            <Field label="Rows on the Top AS page" hint="Between 5 and 200.">
              <Input type="number" min={5} max={200} value={s.top_default_limit} onChange={(e) => set({ top_default_limit: num(e.target.value) })} />
            </Field>
            <Field label="Bandwidth unit" hint="How bit rates are written everywhere in the interface, for all users.">
              <Select value={s.rate_unit ?? "b/s"} onChange={(e) => set({ rate_unit: e.target.value as Settings["rate_unit"] })}>
                <option value="b/s">Mb/s (short)</option>
                <option value="bit/s">Mbit/s (long)</option>
              </Select>
            </Field>
          </div>
        </Panel>

        <Panel title="Data retention">
          <p className="mb-4 text-muted">
            Traffic is stored in 5 minute buckets, then summed into hourly and daily buckets. Longer retention needs more disk space, especially for 5 minute data.
          </p>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="5 minute data, days">
              <Input type="number" min={1} max={60} value={s.retention_5m_days} onChange={(e) => set({ retention_5m_days: num(e.target.value) })} />
            </Field>
            <Field label="Hourly data, days">
              <Input type="number" min={2} max={3650} value={s.retention_1h_days} onChange={(e) => set({ retention_1h_days: num(e.target.value) })} />
            </Field>
            <Field label="Daily data, days">
              <Input type="number" min={30} max={36500} value={s.retention_1d_days} onChange={(e) => set({ retention_1d_days: num(e.target.value) })} />
            </Field>
          </div>
        </Panel>

        <Panel title="AS names from RIPEstat">
          <div className="grid gap-4">
            <Toggle checked={s.ripestat_enabled} onChange={(v) => set({ ripestat_enabled: v })} label="Look up names of new AS numbers automatically" />
            <Field label="Refresh names after, days">
              <Input type="number" min={1} max={365} value={s.asname_ttl_days} onChange={(e) => set({ asname_ttl_days: num(e.target.value) })} />
            </Field>
            <Field label="Application name sent to RIPEstat" hint="RIPE asks heavy users to identify themselves. Your organization name works well.">
              <Input value={s.ripestat_sourceapp} onChange={(e) => set({ ripestat_sourceapp: e.target.value })} maxLength={64} />
            </Field>
          </div>
        </Panel>

        <Panel title="IP-to-AS lookup">
          <div className="grid gap-4">
            <Toggle checked={s.ipasn_enabled} onChange={(v) => set({ ipasn_enabled: v })} label="Download the IP-to-AS dataset once a day" />
            <Field label="Dataset URL" hint="A tab separated file of address ranges and AS numbers, gzip or plain, in the iptoasn.com format.">
              <Input value={s.ipasn_url} onChange={(e) => set({ ipasn_url: e.target.value })} maxLength={512} />
            </Field>
          </div>
        </Panel>

        <Panel title="PeeringDB">
          <div className="grid gap-4">
            <Toggle checked={s.peeringdb_enabled} onChange={(v) => set({ peeringdb_enabled: v })} label="Sync exchanges and members once a day" />
            <Field label="API key (optional)" hint="Raises PeeringDB's rate limit. Create one under your PeeringDB profile. Stored in the database, never shown again.">
              <Input
                type="password"
                autoComplete="off"
                value={s.peeringdb_api_key}
                onChange={(e) => set({ peeringdb_api_key: e.target.value })}
                placeholder="Not set"
              />
            </Field>
          </div>
        </Panel>
      </div>
    </form>
  );
}
