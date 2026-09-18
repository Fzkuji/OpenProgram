import { create } from "zustand";
/** Backend UI commands select a currently visible session row, never invent it. */
export const useResourceSelection = create<{ id: string | null; revision: number }>(() => ({ id: null, revision: 0 }));
