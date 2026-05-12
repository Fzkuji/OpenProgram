"use client";

/** LobeHub-style provider icon — port of _providerIconInner / _providerIconHtml. */
import { useState } from "react";
import styles from "./settings-page.module.css";

const SLUGS: Record<string, string> = {
  openai: "openai",
  "openai-codex": "openai",
  "chatgpt-subscription": "openai",
  anthropic: "claude",
  google: "gemini",
  "google-gemini-cli": "gemini",
  "azure-openai-responses": "azure",
  "amazon-bedrock": "bedrock",
  openrouter: "openrouter",
  groq: "groq",
  cerebras: "cerebras",
  mistral: "mistral",
  minimax: "minimax",
  "minimax-cn": "minimax",
  huggingface: "huggingface",
  "github-copilot": "githubcopilot",
  "kimi-coding": "moonshot",
  "vercel-ai-gateway": "vercel",
  opencode: "opencode",
  "claude-code": "claude",
  "claude-max-proxy": "claude",
  "gemini-cli": "gemini",
};
const CDN = "https://unpkg.com/@lobehub/icons-static-svg@1.4.0/icons/";

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
