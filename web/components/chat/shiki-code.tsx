"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { ensureLang, getHighlighter, SHIKI_DARK, SHIKI_LIGHT } from "@/lib/shiki";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const ALIASES: Record<string, string> = {
  python: "py",
  typescript: "ts",
  javascript: "js",
  shell: "bash",
  sh: "bash",
  zsh: "bash",
  rust: "rs",
  golang: "go",
  yml: "yaml",
};

interface Props {
  code: string;
  language?: string;
}

export function ShikiCode({ code, language }: Props) {
  const { resolvedTheme } = useTheme();
  const lang = (language && (ALIASES[language] ?? language)) || "txt";
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await ensureLang(lang);
        const h = await getHighlighter();
        const out = h.codeToHtml(code, {
          lang: hasLang(h, lang) ? lang : "txt",
          theme: resolvedTheme === "light" ? SHIKI_LIGHT : SHIKI_DARK,
        });
        if (!cancelled) setHtml(out);
      } catch {
        if (!cancelled) setHtml(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, lang, resolvedTheme]);

  function copy() {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }

  return (
    <div className="group relative my-2 overflow-hidden rounded-md border border-(--border) bg-(--bg-elevated)">
      <div className="flex items-center justify-between border-b border-(--border) px-3 py-1 text-[11px] text-(--fg-subtle)">
        <span className="font-mono">{lang}</span>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={copy}
          className="opacity-0 transition group-hover:opacity-100"
          aria-label={copied ? "Copied" : "Copy"}
        >
          {copied ? <Check size={11} /> : <Copy size={11} />}
        </Button>
      </div>
      {html ? (
        <div
          className="shiki-host overflow-x-auto px-3 py-2 text-[12.5px] leading-relaxed [&_pre]:!bg-transparent [&_pre]:!p-0"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre className="overflow-x-auto px-3 py-2 text-[12.5px] leading-relaxed">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

function hasLang(h: Awaited<ReturnType<typeof getHighlighter>>, lang: string): boolean {
  try {
    return h.getLoadedLanguages().includes(lang as never);
  } catch {
    return false;
  }
}

/** Inline code (single-line, no copy button). */
export function InlineCode({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <code
      className={cn(
        "rounded bg-(--bg-elevated) px-1 py-[1px] font-mono text-[0.85em] text-(--fg)",
        className,
      )}
    >
      {children}
    </code>
  );
}
