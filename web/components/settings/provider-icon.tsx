"use client";

/** Provider icon with three-tier LobeHub fallback.
 *
 *   1. **LobeHub colour SVG** keyed on either an explicit
 *      ``SLUGS`` mapping (for ids where ours differs from LobeHub's —
 *      e.g. ``openai-codex`` → ``openai``) or the raw provider id
 *      itself. Most community-imported providers from models.dev
 *      (fireworks, together, perplexity, qwen, alibaba, doubao,
 *      baichuan, baidu, tencentcloud, novita, aihubmix, …) live
 *      under their raw id in LobeHub too, so they pick up colour
 *      glyphs without any local mapping work.
 *   2. **LobeHub mono SVG** as the next try when colour 404s — covers
 *      providers LobeHub ships mono-only (lambda, replicate, …).
 *   3. **Letter avatar** when both LobeHub tiers miss. Chosen over a
 *      currentColor monochrome SVG because the user wanted clean
 *      coloured icons or no icon, not a partial-coverage mono fallback.
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

export function ProviderIcon({ id, size = 24 }: { id: string; size?: number }) {
  const slug = SLUGS[id] ?? id;
  const letter = (id[0] || "?").toUpperCase();
  // Tier index: 0 = colour, 1 = mono, 2 = letter avatar.
  const [tier, setTier] = useState<0 | 1 | 2>(0);

  if (tier === 2) {
    return (
      <span className={styles.providerIconLetter} style={{ width: size, height: size }}>
        {letter}
      </span>
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
        onError={() => setTier((t) => (t < 2 ? ((t + 1) as 0 | 1 | 2) : 2))}
      />
    </div>
  );
}
