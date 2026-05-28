"use client";

/** Provider icon with three-tier fallback.
 *
 *   1. **LobeHub colour SVG** keyed on either an explicit
 *      ``SLUGS`` mapping (for ids where ours differs from LobeHub's
 *      — e.g. ``openai-codex`` → ``openai``) or the raw provider id
 *      itself. Most community-imported providers from models.dev
 *      (fireworks, together, perplexity, qwen, alibaba, doubao,
 *      baichuan, baidu, tencentcloud, novita, aihubmix, …) live
 *      under their raw id in LobeHub too, so they pick up colour
 *      glyphs without any local mapping work.
 *   2. **LobeHub mono SVG** as the next try when colour 404s.
 *   3. **models.dev mono SVG via CSS mask** — for the long tail
 *      LobeHub doesn't carry (abacus, 302ai, siliconflow, …). The
 *      SVG itself is ``currentColor`` so we render it as a
 *      ``mask-image`` with ``backgroundColor: var(--text-primary)``
 *      to follow the active theme (cream on dark, near-black on
 *      light). models.dev serves a generic placeholder SVG for
 *      unknown ids rather than 404'ing, so this tier is the
 *      effective end of the chain — no letter-avatar fallback is
 *      needed (and the previous one was dead code anyway since the
 *      mask path can't dispatch ``onError``).
 */
import { useEffect, useState } from "react";
import styles from "./settings-page.module.css";

/** Explicit slug mapping for the providers whose id differs from
 *  LobeHub's icon key in ways no automatic suffix-strip can recover
 *  (mostly: "X is conceptually the Y brand", e.g. ``openai-codex`` →
 *  ``openai``). Auto-derived suffix variants like ``fireworks-ai`` →
 *  ``fireworks`` don't need entries here — they fall out of
 *  ``slugCandidates`` below. */
const SLUGS: Record<string, string> = {
  // OpenAI family — Codex / consumer-ChatGPT share the OpenAI mark
  "openai-codex": "openai",
  "chatgpt-subscription": "openai",
  // Anthropic family — Meridian / claude-max-proxy share Claude's mark
  anthropic: "claude",
  "claude-code": "claude",
  "claude-max-proxy": "claude",
  // Google family — multiple Gemini delivery flavours share the icon
  google: "gemini",
  "google-gemini-cli": "gemini",
  "gemini-cli": "gemini",
  "gemini-subscription": "gemini",
  // Cloud providers (amazon-bedrock gets stripped to bedrock by the
  // prefix rule below, but azure-openai-responses needs the explicit
  // "land on the azure brand" mapping)
  "azure-openai-responses": "azure",
  // Inference gateways
  "vercel-ai-gateway": "vercel",
  // GitHub Copilot's LobeHub slug is the squashed form
  "github-copilot": "githubcopilot",
};


/** Suffixes that just qualify *which flavour* of a provider this is
 *  (Chinese region, Coding plan, Workers Edge, Token Plan tier, …)
 *  but don't change the underlying brand. LobeHub keys its icons on
 *  the brand, so stripping these gets us a hit. Ordered longest-
 *  first so we don't peel off a ``-cn`` and miss the ``-coding-plan-cn``
 *  it was actually part of. */
const _STRIP_SUFFIXES = [
  "-token-plan-cn",
  "-token-plan-ams",
  "-token-plan-sgp",
  "-token-plan",
  "-coding-plan-cn",
  "-coding-plan",
  "-ai-gateway",
  "-workers-ai",
  "-for-coding",
  "-coding",
  "-responses",
  "-ai",
  "-cn",
];

/** Build the ordered list of slugs to try against LobeHub for a
 *  given provider id. Explicit mapping (if any) wins; raw id next;
 *  then strip common qualifier suffixes one at a time so e.g.
 *  ``alibaba-coding-plan-cn`` → ``alibaba-coding-plan`` →
 *  ``alibaba`` → hit. Also strips the ``amazon-`` prefix so
 *  ``amazon-bedrock`` → ``bedrock``. Deduped; original order
 *  preserved. */
function slugCandidates(id: string): string[] {
  const out: string[] = [];
  const push = (s: string) => {
    if (s && !out.includes(s)) out.push(s);
  };
  const mapped = SLUGS[id];
  if (mapped) push(mapped);
  push(id);

  let cur = id;
  for (let i = 0; i < 3; i++) {
    let stripped = cur;
    for (const sfx of _STRIP_SUFFIXES) {
      if (stripped.endsWith(sfx)) {
        stripped = stripped.slice(0, -sfx.length);
        break;
      }
    }
    if (stripped === cur || !stripped) break;
    push(stripped);
    cur = stripped;
  }

  if (id.startsWith("amazon-")) push(id.slice("amazon-".length));

  return out;
}

const LOBEHUB_CDN = "https://unpkg.com/@lobehub/icons-static-svg@1.90.0/icons/";
const MODELS_DEV_CDN = "https://models.dev/logos/";


/** Module-level cache of models.dev SVG markup, keyed by provider
 *  id. ``"loading"`` placeholder lets multiple instances of the same
 *  id share one in-flight request. ``null`` means the fetch failed
 *  (e.g. offline) so the consumer falls through to the letter tier. */
const _svgCache = new Map<string, string | "loading" | null>();
/** ``Promise``s for in-flight fetches keyed by id, so concurrent
 *  mounts await the same network round trip. */
const _svgInFlight = new Map<string, Promise<string | null>>();


/** Fetch a models.dev SVG once and cache the markup. Inlining the
 *  SVG (instead of using it as an ``<img>`` src or CSS mask) lets us
 *  handle the two layout shapes models.dev ships with one renderer:
 *
 *   * Pure ``currentColor`` SVGs (the common case) — pick up the
 *     parent's ``color`` so they follow the theme.
 *   * PNG-embedded SVGs (e.g. ``atomic-chat``) — rendered verbatim,
 *     including the embedded raster's original colours. The CSS
 *     mask path previously turned these into solid colour blocks
 *     because the PNG's alpha channel was fully opaque end-to-end. */
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
  const candidates = slugCandidates(id);
  const letter = (id[0] || "?").toUpperCase();
  // Tier index: 0=lobe-color, 1=lobe-mono, 2=models.dev (inline), 3=letter.
  const [tier, setTier] = useState<0 | 1 | 2 | 3>(0);
  // Within the LobeHub tiers, walk ``candidates`` one by one on
  // 404. Reset to 0 when we move from colour → mono so the same
  // candidate list gets tried again at the mono CDN.
  const [slugIdx, setSlugIdx] = useState(0);
  const [svg, setSvg] = useState<string | null | undefined>(undefined);

  // Kick off the models.dev fetch the moment we land on tier 2. We
  // re-key the effect on ``id`` so navigating between providers
  // doesn't reuse a stale fetch result.
  useEffect(() => {
    if (tier !== 2) return;
    let cancelled = false;
    _loadSvg(id).then((markup) => {
      if (cancelled) return;
      setSvg(markup);
      if (markup === null) setTier(3); // hard failure → letter
    });
    return () => {
      cancelled = true;
    };
  }, [tier, id]);

  if (tier === 3) {
    return (
      <span className={styles.providerIconLetter} style={{ width: size, height: size }}>
        {letter}
      </span>
    );
  }

  if (tier === 2) {
    // Render whatever models.dev returned, inline. ``color`` cascades
    // into ``currentColor`` fills; PNG-embedded SVGs render their
    // raster verbatim. While we're still fetching, render an empty
    // placeholder of the correct footprint so the row's layout
    // doesn't reflow on resolve.
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

  const slug = candidates[slugIdx] ?? id;
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
          // Walk the candidate list within the current tier first;
          // only escalate to the next tier (mono → models.dev →
          // letter) once we've exhausted every slug variant.
          if (slugIdx + 1 < candidates.length) {
            setSlugIdx(slugIdx + 1);
          } else {
            setSlugIdx(0);
            setTier((t) => (t < 3 ? ((t + 1) as 0 | 1 | 2 | 3) : 3));
          }
        }}
      />
    </div>
  );
}
