import type { ProgramKind } from "./programs-logic";

export type ProgramExplorerEntry = {
  name: string;
  path: string;
  kind: "folder" | "file";
  program_kind: ProgramKind | null;
  has_children: boolean;
  logic_path?: string | null;
  runtime_only?: boolean;
  description?: string;
  callable_name?: string;
};

export type RuntimeToolEntry = {
  name: string;
  description?: string;
  group?: string;
  source?: "builtin" | "mcp";
};

export type RuntimeProgramEntry = {
  name: string;
  category?: string;
  description?: string;
  filepath?: string;
};

type GroupDefinition = readonly [string, string, string];

export function programInvocationName(
  entry: { name: string; callable_name?: string } | null | undefined,
): string {
  return entry?.callable_name || entry?.name || "";
}

function folder(name: string, path: string): ProgramExplorerEntry {
  return { name, path, kind: "folder", program_kind: null, has_children: true };
}

function sourcePath(filepath?: string): string | null {
  if (!filepath) return null;
  const marker = "/programs/";
  const index = filepath.lastIndexOf(marker);
  if (index < 0) return null;
  let relative = filepath.slice(index + marker.length).replaceAll("\\", "/");
  if (relative.endsWith("/__init__.py")) relative = relative.slice(0, -"/__init__.py".length);
  else if (relative.endsWith(".py")) relative = relative.slice(0, -3);
  return relative.startsWith("functions/agentic/") ? relative : null;
}

export function buildRuntimeProgramDirectories(
  tools: RuntimeToolEntry[],
  programs: RuntimeProgramEntry[],
  groupDefinitions: readonly GroupDefinition[],
) {
  const directories: Record<string, ProgramExplorerEntry[]> = {};
  const builtins = tools.filter((tool) => tool.source !== "mcp");
  const agentic = programs.filter((program) => program.category === "agentic");

  directories.functions = [
    folder("vanilla", "functions/vanilla"),
    folder("agentic", "functions/agentic"),
  ];

  const labels = new Map(groupDefinitions.map(([key, en]) => [key, en]));
  const order = new Map(groupDefinitions.map(([key], index) => [key, index]));
  const builtinGroups = new Map<string, RuntimeToolEntry[]>();
  for (const tool of builtins) {
    const key = tool.group || "other";
    builtinGroups.set(key, [...(builtinGroups.get(key) || []), tool]);
  }
  const sortedBuiltinGroups = [...builtinGroups].sort(
    ([left], [right]) => (order.get(left) ?? 999) - (order.get(right) ?? 999) || left.localeCompare(right),
  );
  directories["functions/vanilla"] = sortedBuiltinGroups.map(([key]) =>
    folder(labels.get(key) || key, `functions/vanilla/${encodeURIComponent(key)}`),
  );
  for (const [key, items] of sortedBuiltinGroups) {
    directories[`functions/vanilla/${encodeURIComponent(key)}`] = items
      .toSorted((left, right) => left.name.localeCompare(right.name))
      .map((tool) => ({
        name: tool.name,
        path: `functions/vanilla/${encodeURIComponent(key)}/${encodeURIComponent(tool.name)}`,
        kind: "file",
        program_kind: "vanilla_function",
        has_children: false,
        runtime_only: true,
        description: tool.description,
      }));
  }

  directories["functions/agentic"] = agentic
    .toSorted((left, right) => left.name.localeCompare(right.name))
    .map((program) => {
      const logicPath = sourcePath(program.filepath);
      return {
        name: program.name,
        path: `functions/agentic/${encodeURIComponent(program.name)}`,
        kind: "file",
        program_kind: "agentic_function",
        has_children: false,
        logic_path: logicPath,
        runtime_only: !logicPath,
        description: program.description,
      };
    });

  return {
    directories,
    firstFunction: directories["functions/agentic"][0]?.path
      || Object.values(directories).flat().find((entry) => entry.program_kind)?.path
      || null,
  };
}
