"use client";

/** Provider icon with four-tier fallback.
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
 *      light) instead of falling through to letter on every
 *      community provider LobeHub doesn't cover.
 *   4. **Letter avatar** as the absolute fallback — only hit when
 *      all three remote sources error.
 */
import { useState } from "react";
import styles from "./settings-page.module.css";

/** Explicit slug mapping for the providers whose id differs from
 *  LobeHub's icon key. Everything not listed here falls through to
 *  ``id`` verbatim. */
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
  // Cloud providers
  "azure-openai-responses": "azure",
  "amazon-bedrock": "bedrock",
  // Inference gateways
  "vercel-ai-gateway": "vercel",
  // Chinese providers — LobeHub keys these slightly differently
  "minimax-cn": "minimax",
  "kimi-coding": "moonshot",
  // GitHub Copilot's slug isn't ``github-copilot``
  "github-copilot": "githubcopilot",
};

const LOBEHUB_CDN = "https://unpkg.com/@lobehub/icons-static-svg@1.90.0/icons/";
const MODELS_DEV_CDN = "https://models.dev/logos/";

export function ProviderIcon({ id, size = 24 }: { id: string; size?: number }) {
  const slug = SLUGS[id] ?? id;
  const letter = (id[0] || "?").toUpperCase();
  // Tier index: 0=lobe-color, 1=lobe-mono, 2=models.dev (mask), 3=letter.
  const [tier, setTier] = useState<0 | 1 | 2 | 3>(0);

  if (tier === 3) {
    return (
      <span className={styles.providerIconLetter} style={{ width: size, height: size }}>
        {letter}
      </span>
    );
  }

  // Tier 2 — models.dev's SVG is ``fill="currentColor"`` so loading
  // it via ``<img>`` would render black. CSS ``mask-image`` turns the
  // SVG shape into a stencil and the visible fill is the wrapper's
  // ``backgroundColor`` (pinned to ``--text-primary``), which makes
  // it follow the theme. No ``onError`` here — models.dev returns a
  // generic placeholder for unknown ids rather than 404'ing, so the
  // worst case is a clean placeholder shape instead of broken image.
  if (tier === 2) {
    const url = `${MODELS_DEV_CDN}${encodeURIComponent(id)}.svg`;
    return (
      <div
        className={styles.providerIcon}
        style={{
          width: size,
          height: size,
          backgroundColor: "var(--text-primary)",
          color: "var(--text-primary)",
          WebkitMaskImage: `url(${url})`,
          maskImage: `url(${url})`,
          WebkitMaskSize: "contain",
          maskSize: "contain",
          WebkitMaskRepeat: "no-repeat",
          maskRepeat: "no-repeat",
          WebkitMaskPosition: "center",
          maskPosition: "center",
        }}
        title={id}
      />
    );
  }

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
        onError={() => setTier((t) => (t < 3 ? ((t + 1) as 0 | 1 | 2 | 3) : 3))}
      />
    </div>
  );
}
