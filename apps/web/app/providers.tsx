"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { setQueryClient } from "@/lib/query-client";
import { waitForOwnerAuthBootstrap } from "@/lib/net/owner-auth-bootstrap";

function OwnerAuthBoundary({ children }: { children: ReactNode }) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    void waitForOwnerAuthBootstrap().then(
      () => {},
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, []);

  if (failed) {
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
