import { create } from "zustand";
import type { FunctionsMeta } from "@/lib/types";
import type { AgenticFunction } from "@/lib/session-store";

type ViewMode = "grid" | "list";
type FilterMode = "all" | "favorites" | "app" | "generated" | "user" | "meta" | "builtin";
type SortMode = "category" | "recent" | "alpha";

interface ProgramsState {
  meta: FunctionsMeta;
  functions: AgenticFunction[];
  currentFolder: string; // "__all__" | "__favorites__" | "__uncategorized__" | folderName
  viewMode: ViewMode;
  filter: FilterMode;
  sort: SortMode;
  search: string;
  draggedProgram: string | null;
  setMeta: (m: FunctionsMeta) => void;
  setFunctions: (fns: AgenticFunction[]) => void;
  setCurrentFolder: (f: string) => void;
  setViewMode: (v: ViewMode) => void;
  setFilter: (f: FilterMode) => void;
  setSort: (s: SortMode) => void;
  setSearch: (q: string) => void;
  setDragged: (n: string | null) => void;
}

export const useFunctions = create<ProgramsState>((set) => ({
  meta: { favorites: [], folders: {} },
  functions: [],
  currentFolder: "__all__",
  viewMode: "grid",
  filter: "all",
  sort: "category",
  search: "",
  draggedProgram: null,
  setMeta: (m) => set({ meta: m }),
  setFunctions: (fns) => set({ functions: fns }),
  setCurrentFolder: (f) => set({ currentFolder: f }),
  setViewMode: (v) => set({ viewMode: v }),
  setFilter: (f) => set({ filter: f }),
  setSort: (s) => set({ sort: s }),
  setSearch: (q) => set({ search: q }),
  setDragged: (n) => set({ draggedProgram: n }),
}));
