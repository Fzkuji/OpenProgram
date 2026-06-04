"use client";

/** Provider icon with a precomputed LobeHub coverage map + a
 *  models.dev fallback for the long tail.
 *
 *  Lookup flow per render:
 *
 *    1. ``LOBE_ICONS[id]`` — generated offline by
 *       ``_gen_lobe_slugs.ps1`` against the live LobeHub icon
 *       catalogue. One O(1) hash hit decides which CDN URL to load:
 *       colour SVG when ``hasColor`` is true, else mono SVG when
 *       ``hasMono`` is true. No 404 round-trips, no runtime
 *       suffix-stripping. Coverage on the current
 *       ``/api/providers/list`` (141 ids): 41 colour + 19 mono = 60.
 *
 *    2. **models.dev inline SVG** — for the 81 community providers
 *       LobeHub doesn't carry (302ai, abacus, atomic-chat,
 *       siliconflow, …). Fetched once via ``_loadSvg`` and injected
 *       as DOM. Handles both vector ``currentColor`` glyphs
 *       (theme-coloured) and PNG-embedded SVGs (raster verbatim
 *       with the upstream's branded colour intact).
 *
 *    3. **Letter avatar** as the absolute fallback when models.dev's
 *       fetch hard-fails. models.dev returns a placeholder for
 *       unknown ids rather than 404'ing, so in practice tier 3 only
 *       fires on a network failure.
 *
 *  Re-run ``_gen_lobe_slugs.ps1`` whenever
 *  ``/api/providers/list`` grows or LobeHub publishes new icons —
 *  it overwrites ``./lobe-icons.ts`` in place.
 */
import { useEffect, useState } from "react";
import styles from "./settings-page.module.css";
import { LOBE_ICONS } from "./lobe-icons";
import { MinimaxGlyph } from "./minimax-glyph";

const LOBEHUB_CDN = "https://unpkg.com/@lobehub/icons-static-svg@1.90.0/icons/";
const MODELS_DEV_CDN = "https://models.dev/logos/";


/** Module-level cache of models.dev SVG markup, keyed by provider
 *  id. ``"loading"`` placeholder lets multiple instances of the same
 *  id share one in-flight request. ``null`` means the fetch failed
 *  (e.g. offline) so the consumer falls through to the letter tier. */
const _svgCache = new Map<string, string | "loading" | null>();
/** Promises for in-flight fetches keyed by id, so concurrent mounts
 *  await the same network round trip. */
const _svgInFlight = new Map<string, Promise<string | null>>();


/** Fetch a models.dev SVG once and cache the markup. Inlining the
 *  SVG (instead of using it as an ``<img>`` src or CSS mask) lets us
 *  handle both shapes models.dev ships with one renderer:
 *
 *   * Pure ``currentColor`` SVGs (the common case) — adopt the
 *     parent's ``color`` so they follow the theme.
 *   * PNG-embedded SVGs (e.g. ``atomic-chat``) — rendered verbatim,
 *     including the embedded raster's original colours. The earlier
 *     CSS-mask path turned these into solid colour blocks because
 *     the PNG's alpha channel was fully opaque end-to-end. */
async function _loadSvg(id: string): Promise<string | null> {
  const cached = _svgCache.get(id);
  if (cached === null || (typeof cached === "string" && cached !== "loading")) {
    return cached;
  }
  const existing = _svgInFlight.get(id);
  if (existing) return existing;

  const url = `${MODELS_DEV_CDN}${encodeURIComponent(id)}.svg`;
  const p = (async () => {
    try {
      const r = await fetch(url);
      if (!r.ok) {
        _svgCache.set(id, null);
        return null;
      }
      let text = await r.text();
      // Normalise the root <svg> width/height — many models.dev rows
      // hard-code "200" or "24", which would override our wrapper's
      // size. Strip the attributes and let the wrapper's flex
      // dimensions (combined with the SVG's own viewBox) drive
      // layout.
      text = text.replace(/<svg([^>]*?)\s+width="[^"]*"/i, "<svg$1");
      text = text.replace(/<svg([^>]*?)\s+height="[^"]*"/i, "<svg$1");
      _svgCache.set(id, text);
      return text;
    } catch {
      _svgCache.set(id, null);
      return null;
    } finally {
      _svgInFlight.delete(id);
    }
  })();
  _svgInFlight.set(id, p);
  _svgCache.set(id, "loading");
  return p;
}


export function ProviderIcon({ id, size = 24 }: { id: string; size?: number }) {
  const match = LOBE_ICONS[id];
  const letter = (id[0] || "?").toUpperCase();

  // Pick the starting tier from the precomputed map. ``hasColor`` →
  // tier 0; ``hasMono`` only → tier 1; nothing in LobeHub → skip
  // straight to tier 2 (models.dev inline). Saves one or two 404
  // round-trips on every render for providers we know LobeHub
  // doesn't carry.
  const startTier: 0 | 1 | 2 = !match
    ? 2
    : match.hasColor
      ? 0
      : 1;
  const [tier, setTier] = useState<0 | 1 | 2 | 3>(startTier);
  const [svg, setSvg] = useState<string | null | undefined>(undefined);

  // Kick off the models.dev fetch the moment we land on tier 2.
  useEffect(() => {
    if (tier !== 2) return;
    let cancelled = false;
    _loadSvg(id).then((markup) => {
      if (cancelled) return;
      setSvg(markup);
      if (markup === null) setTier(3);
    });
    return () => {
      cancelled = true;
    };
  }, [tier, id]);

  // MiniMax: its LobeHub color variant is a pink→orange gradient
  // app-icon (skeuomorphic). Render the mono waveform inline instead so
  // `currentColor` follows the theme — a flat glyph that stays visible on
  // both dark and light, matching the rest of the list. Covers all
  // minimax ids (they share slug "minimax"). Placed after the hooks so it
  // never short-circuits a hook call.
  if (match?.slug === "minimax") {
    return (
      <div
        className={styles.providerIcon}
        style={{ width: size, height: size, color: "var(--text-primary)" }}
        title={id}
      >
        <MinimaxGlyph />
      </div>
    );
  }

  if (tier === 3) {
    return (
      <span className={styles.providerIconLetter} style={{ width: size, height: size }}>
        {letter}
      </span>
    );
  }

  if (tier === 2) {
    // Inline whatever models.dev returned. ``color`` cascades into
    // ``currentColor`` fills; PNG-embedded SVGs render their raster
    // verbatim. While we're still fetching, render an empty
    // placeholder of the correct footprint so the row doesn't
    // reflow on resolve.
    return (
      <div
        className={styles.providerIcon}
        style={{
          width: size,
          height: size,
          color: "var(--text-primary)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
        }}
        title={id}
        dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
      />
    );
  }

  // Tier 0 / 1: LobeHub. ``match`` is guaranteed non-null here
  // because ``startTier`` would have been 2 otherwise — and the
  // ``onError`` handler only advances from 0 → 1 when ``hasMono``
  // is true, otherwise it jumps straight to 2.
  const slug = match!.slug;
  const url =
    tier === 0
      ? `${LOBEHUB_CDN}${encodeURIComponent(slug)}-color.svg`
      : `${LOBEHUB_CDN}${encodeURIComponent(slug)}.svg`;

  return (
    <div className={styles.providerIcon} style={{ width: size, height: size }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        key={url}
        src={url}
        alt={id}
        onError={() => {
          // Tier 0 → 1 only if mono also exists in LobeHub; else
          // skip to tier 2 (models.dev). Tier 1 always escalates to
          // tier 2 on failure.
          if (tier === 0 && match!.hasMono) {
            setTier(1);
          } else {
            setTier(2);
          }
        }}
      />
    </div>
  );
}
