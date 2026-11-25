// Web Push on this device: register the service worker (public/sw.js),
// subscribe with this computer's key and tell it which notices to send
// (routes/identity/web_push.py). Works where the page is https and the
// browser has Push: Chrome, Edge, Firefox and Safari, and on iPhone once
// OpenProgram is on the Home Screen.
import { jsonFetch } from "@/lib/net/fetch-client";

export type PushCategory = "attention" | "replies" | "failures";
export const PUSH_CATEGORIES: PushCategory[] = ["attention", "replies", "failures"];

/** Why this device cannot get notifications, or null when it can. */
export type PushBlocker = "insecure" | "home_screen" | "unsupported" | "denied" | null;

function isIos(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
}

function standalone(): boolean {
  return window.matchMedia?.("(display-mode: standalone)").matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

export function pushBlocker(): PushBlocker {
  if (typeof window === "undefined") return "unsupported";
  if (!window.isSecureContext) return "insecure";
  if (isIos() && !standalone()) return "home_screen";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  return null;
}

/** Register the service worker once per page, and open the conversation a
 *  tapped notice names when this window was already open. */
export function registerServiceWorker(open: (path: string) => void): void {
  if (typeof window === "undefined" || !window.isSecureContext || !("serviceWorker" in navigator)) return;
  void navigator.serviceWorker.register("/sw.js").catch(() => {});
  navigator.serviceWorker.addEventListener("message", (event: MessageEvent) => {
    const data = event.data as { type?: string; url?: string } | null;
    if (data?.type === "open" && typeof data.url === "string") open(data.url);
  });
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function current(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

async function save(subscription: PushSubscription, lang: string, categories?: PushCategory[]): Promise<PushCategory[]> {
  const answer = await jsonFetch<{ categories: PushCategory[] }>("/api/push/subscription", {
    method: "PUT",
    body: JSON.stringify({ subscription: subscription.toJSON(), lang, categories }),
  });
  return answer.categories;
}

/** The categories this device receives, or null when it is not subscribed. */
export async function readPush(lang: string): Promise<PushCategory[] | null> {
  if (pushBlocker() !== null) return null;
  const subscription = await current();
  return subscription ? save(subscription, lang) : null;
}

/** Ask the browser, subscribe and tell this computer; null when refused. */
export async function enablePush(lang: string): Promise<PushCategory[] | null> {
  if ((await Notification.requestPermission()) !== "granted") return null;
  const { public_key } = await jsonFetch<{ public_key: string }>("/api/push");
  const registration = await navigator.serviceWorker.ready;
  const subscription = (await registration.pushManager.getSubscription())
    ?? await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) });
  return save(subscription, lang, PUSH_CATEGORIES);
}

export async function disablePush(): Promise<void> {
  const subscription = await current();
  if (!subscription) return;
  await jsonFetch("/api/push/subscription", { method: "DELETE", body: JSON.stringify({ endpoint: subscription.endpoint }) });
  await subscription.unsubscribe();
}

export async function choosePushCategories(lang: string, categories: PushCategory[]): Promise<PushCategory[] | null> {
  const subscription = await current();
  return subscription ? save(subscription, lang, categories) : null;
}

export async function testPush(): Promise<void> {
  const subscription = await current();
  if (!subscription) return;
  await jsonFetch("/api/push/test", { method: "POST", body: JSON.stringify({ endpoint: subscription.endpoint }) });
}
