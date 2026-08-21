import type { ProgramKind } from "./programs-logic";

export type ProgramExplorerEntry = {
  name: string;
  path: string;
  kind: "folder" | "file";
  program_kind: ProgramKind | null;
  has_children: boolean;
  logic_path?: string | null;
  description?: string;
  callable_name?: string;
};

export function programInvocationName(
  entry: { name: string; callable_name?: string } | null | undefined,
): string {
  return entry?.callable_name || "";
}
