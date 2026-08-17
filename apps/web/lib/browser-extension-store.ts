export function isExtensionStoreListing(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    const storePath = parsed.hostname === "microsoftedge.microsoft.com"
      ? parsed.pathname.toLowerCase().startsWith("/addons/detail/")
      : parsed.hostname === "chromewebstore.google.com"
        ? parsed.pathname.toLowerCase().startsWith("/detail/")
        : parsed.hostname === "chrome.google.com"
          && parsed.pathname.toLowerCase().startsWith("/webstore/detail/");
    return storePath && parsed.pathname.split("/").some((part) => /^[a-p]{32}$/.test(part));
  } catch {
    return false;
  }
}
