"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { WSProvider } from "@/lib/ws";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
        },
      })
  );
  return (
    <QueryClientProvider client={client}>
      <WSProvider>{children}</WSProvider>
    </QueryClientProvider>
  );
}
