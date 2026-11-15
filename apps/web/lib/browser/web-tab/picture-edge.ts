/** The colour along the bottom edge of a still picture of a page, which
 *  fills the preview below the picture while it stands in for the page.
 *
 *  The native page lays out at a fixed width and is scaled to the
 *  preview's width (setPipZoom in the desktop App), so at another size
 *  the live page shows the same content at the new width and more or
 *  less of it below. The picture is drawn the same way: scaled to the
 *  frame's width from the top; where the frame is taller than the
 *  picture, the page would go on below, and its own bottom edge colour
 *  stands for it, not the preview's background. */
export function pictureEdgeColour(image: HTMLImageElement): string | null {
  const width = image.naturalWidth, height = image.naturalHeight;
  if (!width || !height) return null;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    context.drawImage(image, 0, height - 1, width, 1, 0, 0, width, 1);
    return dominantColour(context.getImageData(0, 0, width, 1).data);
  } catch {
    return null;
  }
}

/** The most common opaque colour in RGBA pixel data, as `rgb(r, g, b)`:
 *  a page's background, not an average muddied by a line of text. */
export function dominantColour(pixels: ArrayLike<number>): string | null {
  const counts = new Map<number, number>();
  let best = 0, colour = -1;
  for (let i = 0; i + 3 < pixels.length; i += 4) {
    if (pixels[i + 3] < 255) continue;
    const key = (pixels[i] << 16) | (pixels[i + 1] << 8) | pixels[i + 2];
    const count = (counts.get(key) ?? 0) + 1;
    counts.set(key, count);
    if (count > best) { best = count; colour = key; }
  }
  return colour < 0 ? null : `rgb(${colour >> 16}, ${(colour >> 8) & 255}, ${colour & 255})`;
}
