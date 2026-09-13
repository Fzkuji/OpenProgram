export interface FolderPickerResponse {
  path?: string | null;
  unsupported?: boolean;
  error?: string | null;
}

export type FolderPickerFetch = (
  input: string,
  init: RequestInit,
) => Promise<{
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}>;

export class FolderPickerRequestError extends Error {
  readonly kind: "request" | "invalid-path";

  constructor(
    kind: "request" | "invalid-path",
    message: string,
  ) {
    super(message);
    this.name = "FolderPickerRequestError";
    this.kind = kind;
  }
}

async function postFolderPicker(
  body: Record<string, string>,
  fetcher: FolderPickerFetch,
): Promise<{ ok: boolean; status: number; data: FolderPickerResponse | null }> {
  const response = await fetcher("/api/pick-folder", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let data: FolderPickerResponse | null = null;
  try {
    data = (await response.json()) as FolderPickerResponse;
  } catch {
    // A proxy-generated non-JSON error still becomes a useful request error.
  }
  return { ok: response.ok, status: response.status, data };
}

/** Ask the worker to open its native folder picker.

``unsupported`` is intentionally distinct from cancellation: headless Linux
must open the manual server-path dialog, while an ordinary Cancel is a no-op.
*/
export async function requestNativeFolder(
  start = "",
  fetcher: FolderPickerFetch = fetch,
): Promise<FolderPickerResponse> {
  const result = await postFolderPicker(start ? { start } : {}, fetcher);
  if (!result.ok || !result.data) {
    throw new FolderPickerRequestError(
      "request",
      result.data?.error || `folder picker request failed (HTTP ${result.status})`,
    );
  }
  return result.data;
}

/** Validate and normalize a manually entered path on the worker machine. */
export async function validateManualFolder(
  path: string,
  fetcher: FolderPickerFetch = fetch,
): Promise<string> {
  const result = await postFolderPicker({ manual_path: path }, fetcher);
  if (!result.ok || !result.data?.path) {
    throw new FolderPickerRequestError(
      "invalid-path",
      result.data?.error || `folder path validation failed (HTTP ${result.status})`,
    );
  }
  return result.data.path;
}
