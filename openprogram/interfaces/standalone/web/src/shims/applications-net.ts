// The App's request helper (lib/net/applications) for the standalone page:
// same origin, the session cookie, JSON in and out, the server's error text.
export async function applicationRequest<T>(url: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method, credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const value = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error((value && typeof value.error === "string" && value.error) || response.statusText);
  return value as T;
}
