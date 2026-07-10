"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
        },
      })
  );
  // Window bridge for non-React callers (use-ws invalidates query caches
  // on server pushes, e.g. agent_settings_changed -> models-enabled).
  if (typeof window !== "undefined") {
    (window as Window & { __queryClient?: QueryClient }).__queryClient = client;
  }
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
