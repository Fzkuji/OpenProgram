type CatalogEntry = {
  name: string;
  description?: string;
};

type ProgramEntry = CatalogEntry & {
  category?: string;
};

type Profiles = Record<string, string[]>;

const PROFILE_PREFIX = "profile:";

export function profileSelection(name: string): string {
  return PROFILE_PREFIX + encodeURIComponent(name);
}

export function selectionProfileName(selection: string): string | null {
  if (!selection.startsWith(PROFILE_PREFIX)) return null;
  try {
    return decodeURIComponent(selection.slice(PROFILE_PREFIX.length));
  } catch {
    return null;
  }
}

export function programsForSelection<T extends ProgramEntry>(
  selection: string,
  programs: T[],
  favorites: string[],
  profiles: Profiles,
): T[] {
  const profileName = selectionProfileName(selection);
  if (profileName !== null) {
    const names = new Set(profiles[profileName] || []);
    return programs.filter((program) => names.has(program.name));
  }
  if (selection === "__all__") return programs;
  if (selection === "__functions__") return [];
  if (selection === "__agentic_functions__") {
    return programs.filter((program) => program.category !== "app");
  }
  if (selection === "__applications__") {
    return programs.filter((program) => program.category === "app");
  }
  if (selection === "__favorites__") {
    const names = new Set(favorites);
    return programs.filter((program) => names.has(program.name));
  }
  if (selection === "__uncategorized__") {
    const assigned = new Set(Object.values(profiles).flat());
    return programs.filter((program) => !assigned.has(program.name));
  }
  const names = new Set(profiles[selection] || []);
  return programs.filter((program) => names.has(program.name));
}

export function toolsForSelection<T extends CatalogEntry>(
  selection: string,
  tools: T[],
  profiles: Profiles,
): T[] {
  const profileName = selectionProfileName(selection);
  if (profileName !== null) {
    const names = new Set(profiles[profileName] || []);
    return tools.filter((tool) => names.has(tool.name));
  }
  if (selection === "__all__" || selection === "__functions__") return tools;
  if (
    selection === "__agentic_functions__" ||
    selection === "__applications__" ||
    selection === "__favorites__"
  ) {
    return [];
  }
  if (selection === "__uncategorized__") {
    const assigned = new Set(Object.values(profiles).flat());
    return tools.filter((tool) => !assigned.has(tool.name));
  }
  const names = new Set(profiles[selection] || []);
  return tools.filter((tool) => names.has(tool.name));
}

export function matchesProgramSearch(entry: CatalogEntry, query: string): boolean {
  const normalized = query.toLowerCase();
  return (
    !normalized ||
    entry.name.toLowerCase().includes(normalized) ||
    (entry.description || "").toLowerCase().includes(normalized)
  );
}
