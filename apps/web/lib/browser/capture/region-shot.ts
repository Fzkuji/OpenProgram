// A region screenshot taken in a web tab, as the composer gets it: a PNG
// attachment named after the page. The desktop host draws the selection and
// captures the pixels (apps/desktop/main/web-views/web-views.js pickRegionInView).
// Plain TypeScript so tests load it.

export interface PickedRegion {
  id: string;
  url: string;
  title: string;
  width: number;
  height: number;
  dataUrl: string;
}

/** ``region-github.com-1280x320.png``: the site and size, safe as a file name. */
export function regionFileName(region: Pick<PickedRegion, "url" | "width" | "height">): string {
  let site = "page";
  try { site = new URL(region.url).hostname || site; } catch { /* keep "page" */ }
  site = site.replace(/^www\./, "").replace(/[^A-Za-z0-9.-]+/g, "-").slice(0, 60) || "page";
  return `region-${site}-${Math.round(region.width)}x${Math.round(region.height)}.png`;
}

export async function regionFile(region: PickedRegion): Promise<File> {
  const blob = await (await fetch(region.dataUrl)).blob();
  return new File([blob], regionFileName(region), { type: "image/png" });
}
