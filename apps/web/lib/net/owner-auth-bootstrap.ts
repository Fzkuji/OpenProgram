type BootstrapLocation = Pick<Location, "hash" | "pathname" | "search">;
type BootstrapHistory = Pick<History, "state" | "replaceState">;
type BootstrapFetch = (
  input: string,
  init: RequestInit,
) => Promise<Pick<Response, "status">>;

export interface OwnerAuthBootstrapCoordinator {
  wait(): Promise<void>;
}

interface BootstrapDependencies {
  location: BootstrapLocation;
  history: BootstrapHistory;
  fetch: BootstrapFetch;
}

const OWNER_TOKEN_FRAGMENT = /^#token=([A-Za-z0-9_-]{43})$/;
const BOOTSTRAP_FAILURE =
  "Owner authentication failed. Open a new authenticated URL and try again.";

function fragmentContainsToken(hash: string): boolean {
  if (!hash.startsWith("#")) return false;
  return new URLSearchParams(hash.slice(1)).has("token");
}

/**
 * Prepare the browser's one-time owner credential and provide a shared gate.
 *
 * Preparation is intentionally synchronous: if a token parameter is present,
 * the fragment is removed from the address bar and history before `wait()` can
 * issue the bootstrap request. The token remains only in this closure until
 * that one request settles; it is never written to Web Storage or a URL.
 */
export function createOwnerAuthBootstrapCoordinator({
  location,
  history,
  fetch,
}: BootstrapDependencies): OwnerAuthBootstrapCoordinator {
  const fragment = location.hash;
  const hasToken = fragmentContainsToken(fragment);
  let token: string | null = null;
  let invalidFragment = false;

  if (hasToken) {
    history.replaceState(history.state, "", `${location.pathname}${location.search}`);
    const match = OWNER_TOKEN_FRAGMENT.exec(fragment);
    if (match) token = match[1];
    else invalidFragment = true;
  }

  let readyPromise: Promise<void> | null = null;

  return {
    wait(): Promise<void> {
      if (readyPromise) return readyPromise;

      readyPromise = (async () => {
        if (invalidFragment) {
          throw new Error("Invalid owner authentication URL.");
        }
        if (token === null) return;

        const requestToken = token;
        try {
          const response = await fetch("/api/auth/bootstrap", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: requestToken }),
            credentials: "same-origin",
            cache: "no-store",
            redirect: "error",
            referrerPolicy: "no-referrer",
          });
          if (response.status !== 204) throw new Error(BOOTSTRAP_FAILURE);
        } catch {
          throw new Error(BOOTSTRAP_FAILURE);
        } finally {
          token = null;
        }
      })();

      return readyPromise;
    },
  };
}

const browserCoordinator =
  typeof window === "undefined"
    ? null
    : createOwnerAuthBootstrapCoordinator({
        location: window.location,
        history: window.history,
        fetch: window.fetch.bind(window),
      });
if (browserCoordinator) void browserCoordinator.wait().catch(() => {});

/** Resolve only after the browser has exchanged any fragment token for its cookie. */
export function waitForOwnerAuthBootstrap(): Promise<void> {
  return browserCoordinator?.wait() ?? Promise.resolve();
}
