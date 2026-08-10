"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { setQueryClient } from "@/lib/query-client";
import { waitForOwnerAuthBootstrap } from "@/lib/net/owner-auth-bootstrap";

function OwnerAuthBoundary({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"pending" | "ready" | "failed">("pending");

  useEffect(() => {
    let active = true;
    void waitForOwnerAuthBootstrap().then(
      () => {
        if (active) setState("ready");
      },
      () => {
        if (active) setState("failed");
      },
    );
    return () => {
      active = false;
    };
  }, []);

  if (state === "pending") {
    return <main aria-busy="true" aria-label="Authenticating" />;
  }
  if (state === "failed") {
    return (
      <main role="alert">
        Owner authentication failed. Open a new authenticated URL and try again.
      </main>
    );
  }
  return children;
}

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
  return (
    <OwnerAuthBoundary>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </OwnerAuthBoundary>
  );
}
