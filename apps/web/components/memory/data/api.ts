import { createContext, useCallback, useContext } from "react";

/** The project whose memory this page shows; null means the personal one
 *  (the home project's). The server serves it for `?project=<id>`. */
export const MemoryProjectContext = createContext<string | null>(null);

export function memoryUrl(path: string, projectId: string | null | undefined): string {
  if (!projectId || projectId === "default") return path;
  return `${path}${path.includes("?") ? "&" : "?"}project=${encodeURIComponent(projectId)}`;
}

/** `memoryUrl` bound to the project of the surrounding memory page. */
export function useMemoryUrl(): (path: string) => string {
  const projectId = useContext(MemoryProjectContext);
  return useCallback((path: string) => memoryUrl(path, projectId), [projectId]);
}
