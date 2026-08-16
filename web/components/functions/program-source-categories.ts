type CatalogEntry = {
  name: string;
  description?: string;
};

type ProgramEntry = CatalogEntry & {
  category?: string;
};

export function programsForSelection<T extends ProgramEntry>(
  selection: string,
  programs: T[],
  favorites: string[],
): T[] {
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
  return [];
}

export function toolsForSelection<T extends CatalogEntry>(
  selection: string,
  tools: T[],
  favorites: string[],
): T[] {
  if (selection === "__functions__") return tools;
  if (selection === "__favorites__") {
    const names = new Set(favorites);
    return tools.filter((tool) => names.has(tool.name));
  }
  if (
    selection === "__agentic_functions__" ||
    selection === "__applications__"
  ) {
    return [];
  }
  return [];
}

export function matchesProgramSearch(entry: CatalogEntry, query: string): boolean {
  const normalized = query.toLowerCase();
  return (
    !normalized ||
    entry.name.toLowerCase().includes(normalized) ||
    (entry.description || "").toLowerCase().includes(normalized)
  );
}
