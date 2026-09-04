/** Node classification shared by thread attribution and DAG layout. */

import { type GNode, layoutParent } from "../types";

function isRootRef(id: string | null | undefined): boolean {
  return !id || id === "ROOT";
}

export function isSpawnRoot(node: GNode): boolean {
  return (node as Record<string, unknown>).source === "agent_spawn"
    && isRootRef(node.predecessor);
}

export function isTopProgramRun(node: GNode): boolean {
  const name = String(node.function || node.name || "");
  return (
    (node.role === "tool" || node.role === "code" || !!node._runNode)
    && isRootRef(node.caller)
    && !!name
    && node.display !== "root"
    && node.display !== "runtime"
    && node.function !== "attach"
    && !name.startsWith("context/")
  );
}

export function isIndependentRootProgram(
  node: GNode,
  byId: Record<string, GNode>,
): boolean {
  const parentId = layoutParent(node);
  const parent = parentId ? byId[parentId] : undefined;
  return isTopProgramRun(node) && parent?.display === "root" && !node.retry_of;
}

/** True when ``caller`` is a function / Program / spawn, not a chat turn. */
function isExecutionCaller(
  caller: string | null | undefined,
  byId?: Record<string, GNode>,
): boolean {
  if (!byId || isRootRef(caller)) return false;
  const parent = byId[String(caller)];
  if (!parent) return false;
  if (isSpawnRoot(parent) || isTopProgramRun(parent) || parent._runNode) {
    return true;
  }
  if (parent.role === "tool" || parent.role === "code") return true;
  return !!parent.function && parent.function !== "merge";
}

export function isChainNode(
  node: GNode,
  byId?: Record<string, GNode>,
): boolean {
  if (node.display === "root") return true;
  if (isSpawnRoot(node)) return false;
  if ((node as Record<string, unknown>)._agentTurn) return false;
  if (node.function === "merge") return true;
  if (isTopProgramRun(node)) return true;
  if (isExecutionCaller(node.caller, byId)) return false;
  return (
    (node.role === "user" || node.role === "assistant")
    && node.display !== "runtime"
    && !node._runNode
    && !node.function
    && (!!node.predecessor || isRootRef(node.caller))
  );
}

export function isFollowup(node: GNode | undefined): boolean {
  return !!node
    && (node as Record<string, unknown>).source === "job_followup"
    && node.role === "assistant";
}
