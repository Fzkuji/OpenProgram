// OpenProgram's service worker: shows the Web Push notices this computer
// sends (interfaces/server/web_push.py) and opens the conversation a notice
// is about. It caches nothing: OpenProgram needs its computer to be useful.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let notice = { title: "OpenProgram", body: "", url: "/chat", tag: "openprogram" };
  try {
    notice = { ...notice, ...event.data.json() };
  } catch (_) {
    // A notice that cannot be read still shows, with no text of its own.
  }
  event.waitUntil(
    self.registration.showNotification(notice.title, {
      body: notice.body,
      tag: notice.tag,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: notice.url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/chat", self.location.origin);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (new URL(client.url).origin === url.origin && "focus" in client) {
          client.postMessage({ type: "open", url: url.pathname });
          return client.focus();
        }
      }
      return self.clients.openWindow(url.href);
    }),
  );
});
