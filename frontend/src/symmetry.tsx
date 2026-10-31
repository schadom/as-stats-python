import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { get, type LinkMeta, type SymmetryItem, type SymmetryResponse } from "./api";
import { filterQuery, type Filters } from "./state";

/** Routing symmetry helpers shared by the page, the sidebar badge and the AS page. */

export const PEERING_TYPES = new Set(["ix", "pni"]);

/** Shared query: the page, the sidebar badge and the AS page all use it. */
export function useSymmetry(f: Pick<Filters, "range" | "ip">, extra: { min_mb?: number; asn?: number } = {}, enabled = true) {
  return useQuery({
    queryKey: ["symmetry", f.range, f.ip, extra.min_mb ?? 100, extra.asn ?? null],
    queryFn: () => get<SymmetryResponse>(`/symmetry?${filterQuery({ range: f.range, ip: f.ip, links: [] }, extra)}`),
    placeholderData: keepPreviousData,
    enabled,
    staleTime: 60_000,
  });
}

function names(ids: string[], linkById: Map<number, LinkMeta>): string {
  const list = ids.map((id) => linkById.get(Number(id))?.name ?? `link ${id}`);
  return list.length > 1 ? `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}` : list[0] ?? "";
}

/** Links carrying most of a direction, filtered by type group. */
function mainLinks(byLink: Record<string, number>, linkById: Map<number, LinkMeta>, peering: boolean): string[] {
  const total = Object.values(byLink).reduce((a, b) => a + b, 0) || 1;
  return Object.entries(byLink)
    .filter(([id, v]) => {
      const t = linkById.get(Number(id))?.link_type ?? "other";
      return v / total >= 0.1 && (peering ? PEERING_TYPES.has(t) : t === "transit");
    })
    .sort((a, b) => b[1] - a[1])
    .map(([id]) => id);
}

/** One sentence on what is going on, plus the likely causes. */
export function finding(it: SymmetryItem, linkById: Map<number, LinkMeta>): { head: string; detail: string } | null {
  if (it.kind === "in_peer_out_transit") {
    const via = names(mainLinks(it.in_links, linkById, true), linkById);
    return {
      head: `They send via ${via}, you answer via ${names(mainLinks(it.out_links, linkById, false), linkById)}.`,
      detail: `Your router does not use a route over ${via} back to AS${it.asn}. Likely causes: no session with them or the route servers on your side, their prefixes rejected by your import filter or max-prefix limit, or a higher local preference on transit. Check the routes you receive from AS${it.asn} on ${via}.`,
    };
  }
  if (it.kind === "in_transit_out_peer") {
    const via = names(mainLinks(it.out_links, linkById, true), linkById);
    return {
      head: `They reach you via ${names(mainLinks(it.in_links, linkById, false), linkById)}, you reach them via ${via}.`,
      detail: `Your traffic uses ${via}, but theirs does not come back that way. Likely causes: they filter or do not accept your prefixes (IRR, RPKI, max-prefix), they prefer transit by local preference, or your announcement to them or the route servers is missing. Check what they receive from you, for example on bgp.tools or their looking glass.`,
    };
  }
  if (it.kind === "asymmetric") {
    return {
      head: "Different links in each direction.",
      detail: "Common between transits and usually harmless. Only relevant if you steer traffic for cost or capacity.",
    };
  }
  return null;
}

