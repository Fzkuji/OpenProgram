"use client";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/copy-button";
import { SettingsHeading } from "@/components/settings/settings-ui";
import { useTranslation } from "@/lib/i18n";

import type { Provider } from "../types";

/** One ``$ command`` in a setup hint: the command in a small card with the
 *  kit's copy button at its end. */
function CommandRow({ command }: { command: string }) {
  const { text } = useTranslation();
  return (
    <Card size="sm" className="flex-row items-start gap-2 bg-muted px-2.5 py-1.5" data-command>
      <pre className="m-0 min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-fs-xs text-text-primary">{command}</pre>
      <CopyButton value={command} label={text("Copy command", "复制命令")} />
    </Card>
  );
}

/** Provider-specific setup blurb rendered in the detail pane.
 *  Tiny markdown subset:
 *    - backticked spans → inline <code>;
 *    - lines starting with "$ " render as a command row (own block);
 *    - consecutive non-command, non-empty lines collapse into ONE
 *      paragraph that reflows at the container width;
 *    - blank lines separate paragraphs.
 *  The "collapse" rule matters: hint strings in
 *  ``openprogram/models/listing/setup_hints.py`` are hard-wrapped to ~72
 *  chars for Python source readability; without it every source line would
 *  be its own block. */
export function SetupHint({ hint, configured, local = false }: { hint: string; configured: boolean; local?: boolean }) {
  const { text } = useTranslation();
  type Block = { kind: "para"; text: string } | { kind: "cmd"; text: string };
  const blocks: Block[] = [];
  let buf: string[] = [];
  const flushPara = () => {
    if (buf.length) {
      blocks.push({ kind: "para", text: buf.join(" ") });
      buf = [];
    }
  };
  for (const raw of hint.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("$ ")) {
      flushPara();
      blocks.push({ kind: "cmd", text: line.slice(2) });
    } else if (!line.trim()) {
      flushPara();
    } else {
      buf.push(line.trim());
    }
  }
  flushPara();

  const renderInline = (s: string) =>
    s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((seg, j) => {
      if (seg.startsWith("`") && seg.endsWith("`")) {
        return <code key={j} className="rounded-xs bg-muted px-1 font-mono text-fs-xs">{seg.slice(1, -1)}</code>;
      }
      if (seg.startsWith("**") && seg.endsWith("**")) {
        return <strong key={j}>{seg.slice(2, -2)}</strong>;
      }
      return <span key={j}>{seg}</span>;
    });

  return (
    <section className="flex flex-col gap-3" aria-label={text("Setup", "设置")}>
      <SettingsHeading
        title={text("Setup", "设置")}
        action={local ? <Badge variant={configured ? "success" : "secondary"} dot>{configured ? text("Detected", "已检测到") : text("Not running", "未运行")}</Badge> : undefined}
      />
      <div className="flex flex-col gap-2 text-fs-sm text-text-muted">
        {blocks.map((b, i) =>
          b.kind === "cmd" ? <CommandRow key={i} command={b.text} /> : <p key={i} className="m-0">{renderInline(b.text)}</p>,
        )}
      </div>
    </section>
  );
}

/** CLI-runtime providers (e.g. claude-code) — show the binary name
 *  and whether it was found in PATH instead of an API-key input. */
export function CliInfo({ provider }: { provider: Provider }) {
  const { text } = useTranslation();
  return (
    <section className="flex flex-col gap-3" aria-label={text("CLI binary", "CLI 程序")}>
      <SettingsHeading
        title={text("CLI binary", "CLI 程序")}
        action={<Badge variant={provider.configured ? "success" : "secondary"} dot>{provider.configured ? text("Found in PATH", "已在 PATH 中找到") : text("Not found", "未找到")}</Badge>}
      />
      <p className="m-0 text-fs-sm text-text-muted">
        {text("This provider wraps the ", "这个服务商封装 ")}
        <code className="rounded-xs bg-muted px-1 font-mono text-fs-xs">{provider.cli_binary}</code>
        {text(
          " CLI. Install it and run its own login command; enable the toggle above to use it here.",
          " CLI。安装它并运行它自己的登录命令；启用上方开关后即可在这里使用。",
        )}
      </p>
    </section>
  );
}
