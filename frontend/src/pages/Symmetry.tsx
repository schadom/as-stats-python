import { useState } from "react";
import { Link } from "react-router";
import type { LinkMeta, SymmetryItem } from "../api";
import { FilterBar } from "../components/Layout";
import { Empty, ErrorNote, Input, Loading, PageHeader, Segmented, cx } from "../components/ui";
import { bytes, rangeLabel } from "../format";

function duration(seconds: number): string {
  const h = Math.max(1, Math.round(seconds / 3600));
  if (h < 48) return `${h} hour${h === 1 ? "" : "s"}`;
  const d = Math.round(h / 24);
  return `${d} days`;
}
import { navQuery, useFilters, useSession } from "../state";
import { finding, useSymmetry } from "../symmetry";

type Show = "mismatch" | "asymmetric" | "all";

function MixBar({ byLink, linkById }: { byLink: Record<string, number>; linkById: Map<number, LinkMeta> }) {
  const total = Object.values(byLink).reduce((a, b) => a + b, 0) || 1;
  const parts = Object.entries(byLink).sort((a, b) => b[1] - a[1]);
  // one cell on phones (bar above the text), two grid columns from sm up
  return (
    <div className="grid min-w-0 gap-1 sm:contents">
      <div className="flex h-3.5 overflow-hidden rounded-[3px] bg-sunken">
        {parts.map(([id, v]) => {
          const l = linkById.get(Number(id));
          return <span key={id} title={`${l?.name ?? id}: ${bytes(v)}`} style={{ width: `${(v / total) * 100}%`, background: l?.color ?? "var(--c-faint)" }} />;
        })}
      </div>
      <span className="truncate text-[12.5px] text-muted num">
        {parts
          .filter(([, v]) => v / total >= 0.01)
          .map(([id, v]) => `${Math.round((v / total) * 100)}% ${linkById.get(Number(id))?.name ?? id}`)
          .join(" · ")}
      </span>
    </div>
  );
}

const PILL: Record<SymmetryItem["kind"], [string, string]> = {
  in_peer_out_transit: ["IX vs transit", "bg-warn/12 text-warn"],
  in_transit_out_peer: ["IX vs transit", "bg-warn/12 text-warn"],
  asymmetric: ["Asymmetric", "bg-sunken text-ink-2"],
  ok: ["Symmetric", "bg-ok/12 text-ok"],
};

function Card({ it, linkById, q }: { it: SymmetryItem; linkById: Map<number, LinkMeta>; q: string }) {
  const f = finding(it, linkById);
  const mismatch = it.kind.startsWith("in_");
  return (
    <div
      id={`as${it.asn}`}
      className={cx(
        "rounded-lg border border-rule bg-surface px-4 py-3",
        mismatch && "border-l-[3px] border-l-warn",
        it.kind === "asymmetric" && "border-l-[3px] border-l-rule-strong",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="inline-flex min-w-0 flex-wrap items-baseline gap-x-2">
          <Link to={`/as/${it.asn}${q}`} className="truncate font-semibold hover:text-accent">
            {it.name || it.handle || `AS${it.asn}`}
          </Link>
          <span className="text-[12.5px] text-muted num">AS{it.asn}</span>
          <span className={cx("rounded px-1.5 text-[12px] font-medium", PILL[it.kind][1])}>{PILL[it.kind][0]}</span>
        </span>
        <span className="text-[12.5px] text-muted num">
          {bytes(it.in + it.out)} · symmetry {Math.round(it.score * 100)}%{it.since ? ` · for ${duration(Date.now() / 1000 - it.since)}` : ""}
        </span>
      </div>
      <div className="mt-2.5 grid grid-cols-[64px_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5 sm:grid-cols-[64px_minmax(0,1fr)_minmax(0,16rem)]">
        <span className="text-[12.5px] text-muted">Inbound</span>
        <MixBar byLink={it.in_links} linkById={linkById} />
        <span className="text-[12.5px] text-muted">Outbound</span>
        <MixBar byLink={it.out_links} linkById={linkById} />
      </div>
      {f && (
        <p className="mt-2.5 text-[13px] leading-relaxed">
          <span className="font-medium">{f.head}</span> <span className="text-muted">{f.detail}</span>
        </p>
      )}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-sunken px-3.5 py-2.5">
      <div className="text-[12.5px] text-muted">{label}</div>
      <div className="text-[20px] font-semibold num">{value}</div>
    </div>
  );
}

export default function Symmetry() {
  const { isAdmin, linkById } = useSession();
  const [f] = useFilters();
  const [show, setShow] = useState<Show>("mismatch");
  const [minMb, setMinMb] = useState(100);
  const q = useSymmetry(f, { min_mb: minMb });
  const d = q.data;
  const nav = navQuery(f);

  const items =
    d?.items.filter((it) => (show === "all" ? true : show === "asymmetric" ? it.kind !== "ok" : it.kind.startsWith("in_"))) ?? [];

  return (
    <>
      <PageHeader
        title="Routing symmetry"
        subtitle={d ? `${rangeLabel(d.start, d.end)}. Which of your links each AS uses inbound compared with outbound.` : " "}
      />
      <FilterBar noLinks />
      {q.error && <ErrorNote error={q.error} />}
      {q.isLoading ? (
        <Loading />
      ) : d && !d.types_set ? (
        <div className="rounded-lg border border-rule bg-surface">
          <Empty title="Set the link types first">
            This view needs to know which links are transit and which are IX ports or private interconnects.{" "}
            {isAdmin ? (
              <>
                Set a <span className="font-medium">Link type</span> for each link under{" "}
                <Link to="/settings/links" className="font-medium text-accent hover:underline">Links &amp; interfaces</Link>.
              </>
            ) : (
              "An administrator can set them under Links & interfaces."
            )}
          </Empty>
        </div>
      ) : d ? (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile label="IX vs transit mismatches" value={String(d.summary.mismatches)} />
            <Tile label="Traffic affected" value={bytes(d.summary.affected)} />
            <Tile label="Other asymmetric ASes" value={String(d.summary.asymmetric)} />
            <Tile label="Symmetric via peering" value={String(d.summary.symmetric_peering)} />
          </div>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <Segmented
              label="Show"
              value={show}
              onChange={setShow}
              options={[
                { id: "mismatch", label: "IX vs transit" },
                { id: "asymmetric", label: "All asymmetric" },
                { id: "all", label: `All compared (${d.summary.compared})` },
              ]}
            />
            <label className="inline-flex items-center gap-2 text-[13px] whitespace-nowrap text-muted">
              Ignore ASes below
              <span className="w-24">
                <Input
                  type="number"
                  min={0}
                  step="any"
                  value={minMb}
                  onChange={(e) => setMinMb(Math.max(0, Number(e.target.value) || 0))}
                  aria-label="Minimum volume in MB"
                />
              </span>
              MB
            </label>
          </div>
          {items.length === 0 ? (
            <div className="rounded-lg border border-rule bg-surface">
              <Empty title={show === "mismatch" ? "No IX vs transit mismatches" : "Nothing to show"}>
                {show === "mismatch"
                  ? `None of the ${d.summary.compared} ASes above ${minMb} MB arrives via peering and leaves via transit, or the other way round.`
                  : "No AS has enough traffic in both directions in this range."}
              </Empty>
            </div>
          ) : (
            <div className="grid gap-3">
              {items.map((it) => (
                <Card key={it.asn} it={it} linkById={linkById} q={nav} />
              ))}
            </div>
          )}
          <p className="mt-4 text-[13px] text-muted">
            Compares the share of each AS's traffic per link, inbound against outbound, over the selected range. ASes need traffic in both directions.
            Links of type “Other” never count as peering or transit.
          </p>
        </>
      ) : null}
    </>
  );
}
