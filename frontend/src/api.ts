export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d.length) {
      return d
        .map((e) => {
          const loc = Array.isArray(e?.loc) ? e.loc.filter((x: unknown) => x !== "body").join(".") : "";
          return loc ? `${loc}: ${e.msg}` : e.msg;
        })
        .join("; ");
    }
  }
  return fallback;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "XMLHttpRequest" };
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`/api${path}`, { ...init, body, headers, credentials: "same-origin" });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = new ApiError(res.status, detail(data, res.statusText));
    if (res.status === 401 || (res.status === 403 && err.message === "password_change_required")) {
      window.dispatchEvent(new CustomEvent("asstats:auth", { detail: err.message }));
    }
    throw err;
  }
  return data as T;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, json?: unknown) => api<T>(path, { method: "POST", json });
export const put = <T,>(path: string, json?: unknown) => api<T>(path, { method: "PUT", json });
export const patch = <T,>(path: string, json?: unknown) => api<T>(path, { method: "PATCH", json });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

// ---------------------------------------------------------------- types

export type Role = "admin" | "readonly";

export interface User {
  id: number;
  username: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  created_at: number;
  last_login: number | null;
}

export type RateUnit = "b/s" | "bit/s";

export interface LinkMeta {
  id: number;
  tag: string;
  name: string;
  color: string;
  enabled: boolean;
  sort_order: number;
}

export interface Meta {
  version: string;
  site_name: string;
  my_asn: number | null;
  top_default_limit: number;
  /** Missing on servers older than this frontend: treat as "b/s". */
  rate_unit?: RateUnit;
  links: LinkMeta[];
  has_ix: boolean;
}

export interface AsMeta {
  name: string;
  handle: string | null;
  country: string | null;
}

export interface TopItem extends AsMeta {
  asn: number;
  in: number;
  out: number;
  links: Record<string, { in: number; out: number }>;
}

export interface TopResponse {
  start: number;
  end: number;
  items: TopItem[];
  has_more: boolean;
}

export interface SeriesEntry {
  asn?: number;
  link_id: number;
  in_v4: number[];
  out_v4: number[];
  in_v6: number[];
  out_v6: number[];
}

export interface SeriesResponse {
  step: number;
  start: number;
  end: number;
  buckets: number[];
  series: SeriesEntry[];
}

export interface AsDetail {
  asn: number;
  name: string;
  handle: string | null;
  description: string | null;
  country: string | null;
  pdb_name: string | null;
  pdb_id: number | null;
  custom_name: string | null;
  updated_at: number;
  start: number;
  end: number;
  links: { link_id: number; in_v4: number; out_v4: number; in_v6: number; out_v6: number }[];
  ixs: { ix_id: number; name: string; speed: number | null; is_rs_peer: boolean; ipaddr4: string | null; ipaddr6: string | null }[];
}

export interface UsageResponse {
  start: number;
  end: number;
  links: { link_id: number; in: number; out: number; top: (AsMeta & { asn: number; in: number; out: number })[] }[];
}

export interface IxList {
  my_asn: number | null;
  last_refresh: number | null;
  last_error: string | null;
  ixs: { id: number; name: string; name_long: string | null; city: string | null; country: string | null; members: number }[];
}

export interface IxMember extends AsMeta {
  asn: number;
  speed: number;
  is_rs_peer: boolean;
  ipaddr4: string[];
  ipaddr6: string[];
  in: number;
  out: number;
}

export interface IxDetail {
  ix: { id: number; name: string; name_long: string | null; city: string | null; country: string | null };
  start: number;
  end: number;
  members: IxMember[];
}

export type AsSource = "auto" | "router" | "lookup";

export interface Exporter {
  id: number;
  ip: string;
  name: string;
  sampling_rate: number | null;
  as_mode: "origin" | "peer";
  as_source: AsSource;
  enabled: boolean;
  status: ExporterStatus | null;
}

export interface ExporterStatus {
  ip: string;
  protocol: string;
  version: number;
  last_seen: number;
  packets: number;
  flows: number;
  errors: number;
  flows_per_sec: number;
  reported_sampling: number | null;
  templates: number | null;
  exporter_id?: number | null;
  exporter_name?: string | null;
}

export interface LinkIface {
  id?: number;
  exporter_id: number;
  exporter_name?: string;
  exporter_ip?: string;
  ifindex: number;
  if_name?: string | null;
  sampling_rate: number | null;
}

export interface LinkFull extends LinkMeta {
  interfaces: LinkIface[];
}

export interface Discovered {
  exporter_ip: string;
  exporter_id: number | null;
  exporter_name: string | null;
  ifindex: number;
  if_name: string | null;
  first_seen: number;
  last_seen: number;
  bps_in: number;
  bps_out: number;
  flows: number;
  link_id: number | null;
  link_name: string | null;
}

export interface Settings {
  site_name: string;
  my_asn: number | null;
  retention_5m_days: number;
  retention_1h_days: number;
  retention_1d_days: number;
  ripestat_enabled: boolean;
  ripestat_sourceapp: string;
  asname_ttl_days: number;
  peeringdb_enabled: boolean;
  peeringdb_api_key: string;
  top_default_limit: number;
  rate_unit: RateUnit;
  ipasn_enabled: boolean;
  ipasn_url: string;
}

export interface Status {
  version: string;
  database: string;
  collector: {
    ts: number;
    flows_per_sec: number;
    exporters: number;
    missing_template: number;
    ipasn_ranges?: number;
    ipasn_mtime?: number | null;
  } | null;
  ipasn: { enabled: boolean; last_refresh: number | null; last_error: string | null; ranges: number | null };
  exporters: ExporterStatus[];
  rollup: Record<string, number>;
  peeringdb: { last_refresh: number | null; last_error: string | null };
  asnames_pending: number;
  jobs: Record<string, number>;
  now: number;
}

export interface SearchHit extends AsMeta {
  asn: number;
}
