"use client";

/** Provider icon with three-tier fallback.
 *
 *   1. **LobeHub colour SVG** for the providers we've explicitly
 *      mapped in ``SLUGS`` — these are the major branded ones
 *      (OpenAI, Anthropic, DeepSeek, Gemini, …) where the
 *      hand-curated colour glyph reads cleaner than models.dev's.
 *   2. **LobeHub mono SVG** for the same set, if colour is missing.
 *   3. **models.dev logo** for everything else — covers the 119
 *      community-imported providers (Fireworks, Together, 302.AI,
 *      Lambda, Perplexity, …) that aren't in our hand-rolled
 *      LobeHub slug map. The URL is keyed on the raw provider id
 *      we already use elsewhere, no alias table required.
 *   4. **Letter avatar** if all three return errors.
 *
 *  models.dev *does* serve a generic placeholder SVG for unknown
 *  ids rather than 404'ing, so step 3 won't fall through to step 4
 *  on miss — it just shows the placeholder, which is still nicer
 *  than the bare letter.
 */
import { useState } from "react";
import styles from "./settings-page.module.css";

const SLUGS: Record<string, string> = {
  // OpenAI family
  openai: "openai",
  "openai-codex": "openai",
  "chatgpt-subscription": "openai",
  // Anthropic family
  anthropic: "claude",
  "claude-code": "claude",
  "claude-max-proxy": "claude",
  // Google family
  google: "gemini",
  "google-gemini-cli": "gemini",
  "gemini-cli": "gemini",
  "gemini-subscription": "gemini",
  // Cloud providers
  "azure-openai-responses": "azure",
  "amazon-bedrock": "bedrock",
  // Inference gateways
  openrouter: "openrouter",
  "vercel-ai-gateway": "vercel",
  opencode: "opencode",
  // Inference clouds
  groq: "groq",
  cerebras: "cerebras",
  mistral: "mistral",
  huggingface: "huggingface",
  // Chinese providers
  minimax: "minimax",
  "minimax-cn": "minimax",
  "kimi-coding": "moonshot",
  zai: "zai",
  deepseek: "deepseek",
  // Other
  "github-copilot": "githubcopilot",
  xai: "xai",
};
// v1.90.0: covers openai/openrouter/groq/githubcopilot/moonshot/vercel/
// opencode/xai/zai (all mono-only) on top of the brand-color SVGs.
const LOBEHUB_CDN = "https://unpkg.com/@lobehub/icons-static-svg@1.90.0/icons/";
const MODELS_DEV_CDN = "https://models.dev/logos/";

export function ProviderIcon({ id, size = 24 }: { id: string; size?: number }) {
  const slug = SLUGS[id];
  const letter = (id[0] || "?").toUpperCase();
  // Tier index: 0=lobe-color, 1=lobe-mono, 2=models.dev, 3=letter.
  // Mapped ids start at 0; unmapped ids skip straight to tier 2.
  const [tier, setTier] = useState<0 | 1 | 2 | 3>(slug ? 0 : 2);

  if (tier === 3) {
    return (
      <span className={styles.providerIconLetter} style={{ width: size, height: size }}>
        {letter}
      </span>
    );
  }

  const url =
    tier === 0
      ? `${LOBEHUB_CDN}${slug}-color.svg`
      : tier === 1
        ? `${LOBEHUB_CDN}${slug}.svg`
        : `${MODELS_DEV_CDN}${encodeURIComponent(id)}.svg`;

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
