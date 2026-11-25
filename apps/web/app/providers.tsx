"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { createAppQueryClient, followQueryTopics, setQueryClient } from "@/lib/query-client";
import { PAIRING_FAILURE, waitForOwnerAuthBootstrap } from "@/lib/net/auth/owner-auth-bootstrap";
import { ConfirmDialogHost } from "@/components/ui/confirm-dialog";
import { registerServiceWorker } from "@/lib/push/web-push";

const OWNER_AUTH_FAILURE =
  "Owner authentication failed. Open a new authenticated URL and try again.";

function OwnerAuthBoundary({ children }: { children: ReactNode }) {
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void waitForOwnerAuthBootstrap().then(
      () => {},
      (error: unknown) => {
        if (!active) return;
        const pairing = error instanceof Error && error.message === PAIRING_FAILURE;
        setFailed(pairing ? PAIRING_FAILURE : OWNER_AUTH_FAILURE);
      },
    );
    return () => {
      active = false;
    };
  }, []);

  if (failed !== null) {
    return <main role="alert">{failed}</main>;
  }
  return children;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => {
    const c = createAppQueryClient();
    // Publish for non-React callers (use-ws invalidates query caches on
    // server pushes, e.g. agent_settings_changed -> models-enabled).
    setQueryClient(c);
    return c;
  });
  // Cached answers stay fresh until the Worker says what they read changed.
  useEffect(() => followQueryTopics(client), [client]);
  // Web Push: a tapped notice opens its conversation in this window.
  useEffect(() => registerServiceWorker((path) => {
    const id = path.startsWith("/s/") ? path.slice(3) : "";
    if (id) void import("@/lib/tabs/navigation/open-session").then(({ openSession }) => openSession(id));
  }), []);
  return (
    <OwnerAuthBoundary>
      <QueryClientProvider client={client}>
        {children}
        <ConfirmDialogHost />
      </QueryClientProvider>
    </OwnerAuthBoundary>
  );
}
