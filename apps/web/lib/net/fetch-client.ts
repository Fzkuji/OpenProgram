/**
 * Shared JSON fetch client for the web stores/clients.
 *
 * Previously `jsonFetch` / `jsonReq` was copy-pasted into lib/api.ts,
 * lib/skills-store.ts, and lib/plugins-store.ts with subtly different error
 * handling (only plugins parsed a `{error, code}` body). Centralising it here
 * gives one place to evolve global retry / auth / logging policy.
 *
 * On a non-2xx response it throws an {@link HttpError} carrying the status, the
 * server's `code` when present, and the raw body. The message is the server's
 * `error` field when present, else `HTTP <status>: <body>` (the shape the older
 * api.ts/skills-store helpers produced). On success it returns the parsed JSON
 * (`{}` for an empty 2xx body).
 */

import { waitForOwnerAuthBootstrap } from "./owner-auth-bootstrap.ts";
import { recoverOwnerAuth } from "./owner-auth-recovery.ts";

export class HttpError extends Error {
  status: number;
  code?: string;
  body?: string;
  constructor(message: string, status: number, code?: string, body?: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    if (code) this.code = code;
    this.body = body;
  }
}

export async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  return jsonFetchOnce(url, init, true);
}

async function jsonFetchOnce<T>(
  url: string,
  init: RequestInit | undefined,
  allowRecover: boolean,
): Promise<T> {
  await waitForOwnerAuthBootstrap();
  const r = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const text = await r.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text };
  }
  if (!r.ok) {
    if (r.status === 401 && allowRecover && await recoverOwnerAuth()) {
      return jsonFetchOnce(url, init, false);
    }
    const d = data as { error?: string; detail?: string; code?: string };
    const message =
      d.error || d.detail || (text ? `HTTP ${r.status}: ${text.slice(0, 300)}` : `HTTP ${r.status}`);
    throw new HttpError(message, r.status, d.code, text);
  }
  return data as T;
}
