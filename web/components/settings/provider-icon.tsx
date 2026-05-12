"use client";

/** LobeHub-style provider icon — port of _providerIconInner / _providerIconHtml. */
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
  // Other
  "github-copilot": "githubcopilot",
  xai: "xai",
};
// v1.90.0: covers openai/openrouter/groq/githubcopilot/moonshot/vercel/
// opencode/xai/zai (all mono-only) on top of the brand-color SVGs.
const CDN = "https://unpkg.com/@lobehub/icons-static-svg@1.90.0/icons/";

export function ProviderIcon({ id, size = 24 }: { id: string; size?: number }) {
  const slug = SLUGS[id];
  const letter = (id[0] || "?").toUpperCase();
  const [step, setStep] = useState<0 | 1 | 2>(0);

  if (!slug || step === 2) {
    return (
      <span className={styles.providerIconLetter} style={{ width: size, height: size }}>
        {letter}
      </span>
    );
  }
  const url = step === 0 ? `${CDN}${slug}-color.svg` : `${CDN}${slug}.svg`;
  return (
    <div className={styles.providerIcon} style={{ width: size, height: size }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        key={url}
        src={url}
        alt={id}
        onError={() => setStep(step === 0 ? 1 : 2)}
      />
    </div>
  );
}
