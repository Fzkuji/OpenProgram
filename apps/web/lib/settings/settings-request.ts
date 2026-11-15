import { showToast } from "@/lib/format-utils/toast";
import { jsonFetch } from "@/lib/net/fetch-client";

/** Send one change from a Settings page. A refused or lost request tells the
 *  user (the server's reason when it gave one) and resolves to null, so no
 *  page can carry on as if a failed change had worked. */
export async function settingsSend<T = Record<string, unknown>>(
  url: string,
  options: { method?: "POST" | "DELETE"; body?: unknown; failure: string },
): Promise<T | null> {
  try {
    return await jsonFetch<T>(url, {
      method: options.method ?? "POST",
      ...(options.body === undefined ? {} : {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options.body),
      }),
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    showToast(reason && !reason.startsWith("HTTP ") ? reason : options.failure, { tone: "error" });
    return null;
  }
}
