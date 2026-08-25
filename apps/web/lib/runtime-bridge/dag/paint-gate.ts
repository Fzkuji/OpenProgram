export interface CoverageNode {
  node_id: string;
  in_context: boolean;
  aged: boolean;
  spilled: boolean;
}

export type CoverageMap = Record<string, { aged: boolean; spilled: boolean }>;

/** Skip SVG emit when the host is `display:none` or not the DAG view. */
export function shouldEmitHistorySvg(
  panelDisplay: string | null | undefined,
  centerView: string | null | undefined,
): boolean {
  if (panelDisplay == null) return false;
  if (panelDisplay === "none") return false;
  if (centerView != null && centerView !== "dag") return false;
  return true;
}

export function contextRangeUnchanged(
  prevIds: Record<string, boolean> | null,
  prevCoverage: CoverageMap | null,
  ids: string[] | null,
  coverage?: CoverageNode[] | null,
): boolean {
  const nextEmpty = !ids || !ids.length;
  const prevEmpty = !prevIds;
  if (nextEmpty && prevEmpty) {
    return !prevCoverage && !(coverage && coverage.length);
  }
  if (nextEmpty || prevEmpty) return false;
  if (ids.length !== Object.keys(prevIds).length) return false;
  for (const id of ids) {
    if (!prevIds[id]) return false;
  }
  const nextHas = !!(coverage && coverage.length);
  const prevHas = !!prevCoverage;
  if (nextHas !== prevHas) return false;
  if (!nextHas) return true;
  const nextKeys = new Set<string>();
  for (const row of coverage!) {
    if (!row || !row.node_id) continue;
    nextKeys.add(row.node_id);
    const prev = prevCoverage![row.node_id];
    if (!prev || prev.aged !== !!row.aged || prev.spilled !== !!row.spilled) {
      return false;
    }
  }
  for (const k of Object.keys(prevCoverage!)) {
    if (!nextKeys.has(k)) return false;
  }
  return true;
}

/** Membership + aged/spilled — the fields that force a coverage repaint. */
export function coveragePaintSignature(
  contextSet: Record<string, boolean> | null,
  coverageSet: CoverageMap | null,
): string {
  const ids = contextSet ? Object.keys(contextSet).sort().join(",") : "";
  const cov = coverageSet
    ? Object.keys(coverageSet).sort().map((id) => {
        const row = coverageSet[id];
        return id + ":" + (row.aged ? "1" : "0") + (row.spilled ? "1" : "0");
      }).join(",")
    : "";
  return ids + "/" + cov;
}

export function viewOpenSignature(open: Record<string, boolean>): string {
  return Object.keys(open).filter((k) => open[k]).sort().join(",");
}

export function branchTagsSignature(
  rows: Array<{
    head_msg_id?: string;
    head_id?: string;
    name?: string;
    active?: boolean;
  }> | null | undefined,
): string {
  if (!rows || !rows.length) return "";
  return rows.map((b) =>
    (b.head_msg_id || b.head_id || "") + ":" + (b.name || "") + ":"
    + (b.active ? "1" : "0")).join(",");
}

export type DagSigNode = {
  id: string;
  predecessor?: string | null;
  role?: string;
  display?: string;
  status?: string;
  function?: string;
  caller?: string;
  source?: string;
  name?: string;
  covers_ids?: unknown;
  created_at?: number;
  _tier?: number;
  _lane?: number;
  _depth?: number;
  _runNode?: boolean;
  is_error?: boolean;
  is_named?: boolean;
  branch_name?: string;
  superseded_summary?: unknown;
  attach_ref?: string;
  attach_label?: string;
  spawned_from?: { label?: string | null } | null;
};

export type DagSignatureInput = {
  graph: DagSigNode[] | null | undefined;
  headId: string | null;
  threadOpen: Record<string, boolean>;
  summaryExpanded: Record<string, boolean>;
  locale: string;
  contextSet: Record<string, boolean> | null;
  coverageSet: CoverageMap | null;
  sessionId: string | null;
  branchTags: string;
  highlightMode: string;
};

function nodeGeometryPart(m: DagSigNode): string {
  return m.id + ":" + (m.predecessor || "") + ":" + (m.role || "") + ":"
    + (m.display || "") + ":"
    + (m.function || "") + ":" + (m.caller || "") + ":"
    + (m.source || "") + ":" + (m.name || "") + ":"
    + (Array.isArray(m.covers_ids) ? m.covers_ids.map(String).join("+") : "")
    + ":" + (m.created_at ?? "") + ":" + (m._tier ?? "") + ":"
    + (m._lane ?? "") + ":" + (m._depth ?? "") + ":"
    + (m._runNode ? "1" : "") + ":"
    + (m.is_named ? "1" : "") + ":"
    + (m.branch_name || "") + ":" + (m.superseded_summary ? "1" : "") + ":"
    + (m.attach_ref || "") + ":" + (m.attach_label || "") + ":"
    + (m.spawned_from && m.spawned_from.label || "");
}

function graphParts(
  graph: DagSigNode[] | null | undefined,
  headId: string | null,
  withStatus: boolean,
): string {
  if (!graph || !graph.length) return "empty|" + (headId || "");
  const parts = graph.map((m) =>
    nodeGeometryPart(m) + (withStatus
      ? ":" + (m.status || "") + ":" + (m.is_error ? "1" : "")
      : ""));
  parts.sort();
  return parts.join(",") + "|" + (headId || "") + "|" + graph.map((m) => m.id).join(">");
}

function viewFingerprint(input: DagSignatureInput): string {
  return [
    viewOpenSignature(input.threadOpen),
    viewOpenSignature(input.summaryExpanded),
    input.locale,
    coveragePaintSignature(input.contextSet, input.coverageSet),
    input.sessionId || "",
    input.branchTags,
    input.highlightMode,
  ].join("||");
}

function graphInputSignature(
  graph: DagSigNode[] | null | undefined,
  headId: string | null,
): string {
  return graphParts(graph, headId, true);
}

/** Input fingerprint for render() — compared before merge/fold/thread. */
export function dagInputSignature(input: DagSignatureInput): string {
  return graphInputSignature(input.graph, input.headId) + "||" + viewFingerprint(input);
}

/** Same as dagInputSignature but ignores status / is_error. */
export function geometryInputSignature(input: DagSignatureInput): string {
  return graphParts(input.graph, input.headId, false) + "||" + viewFingerprint(input);
}

/** Backend layout stamps — without both, row/column fallback is a guess. */
export function hasAuthoritativeLayout(
  graph: DagSigNode[] | null | undefined,
): boolean {
  if (!graph || !graph.length) return false;
  for (const m of graph) {
    if (typeof m._depth !== "number" || typeof m._lane !== "number") return false;
  }
  return true;
}

export function readHistoryEmitGate(doc: {
  getElementById(id: string): HTMLElement | null;
}): { panelDisplay: string | null; centerView: string | null } {
  const panel = doc.getElementById("historyPanel");
  if (!panel) return { panelDisplay: null, centerView: null };
  let hidden = false;
  let el: HTMLElement | null = panel;
  while (el) {
    if (el.style.display === "none") {
      hidden = true;
      break;
    }
    el = el.parentElement;
  }
  const host = panel.closest("[data-center-view]");
  return {
    panelDisplay: hidden ? "none" : (panel.style.display || "flex"),
    centerView: host?.getAttribute("data-center-view") ?? null,
  };
}
