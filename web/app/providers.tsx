"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { setQueryClient } from "@/lib/query-client";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => {
    const c = new QueryClient({
      defaultOptions: {
        queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
      },
    });
    // Publish for non-React callers (use-ws invalidates query caches on
    // server pushes, e.g. agent_settings_changed -> models-enabled).
    setQueryClient(c);
    return c;
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
