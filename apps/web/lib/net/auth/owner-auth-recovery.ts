import { adoptOwnerAuthToken } from "./owner-auth-bootstrap.ts";

/** Refresh the owner cookie after worker credential rotation. Desktop-only. */
export async function recoverOwnerAuth(): Promise<boolean> {
  const refresh = typeof window === "undefined"
    ? undefined
    : window.openprogramDesktop?.refreshOwnerAuth;
  if (typeof refresh !== "function") return false;
  let token: unknown;
  try {
    token = await refresh();
  } catch {
    return false;
  }
  if (typeof token !== "string") return false;
  try {
    await adoptOwnerAuthToken(token);
    return true;
  } catch {
    return false;
  }
}
