"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";

// Static export has no dynamic segments: /plugin/<name>/<slug...> is
// served by the worker's SPA fallback with this page's HTML; name and
// slug are resolved client-side from the pathname.
export default function PluginWebPage() {
  const pathname = usePathname() || "";
  const { name, slugArr } = useMemo(() => {
    const parts = pathname.split("/").filter(Boolean); // ["plugin", name, ...slug]
    return {
      name: parts[1] ? decodeURIComponent(parts[1]) : undefined,
      slugArr: parts.slice(2).map(decodeURIComponent),
    };
  }, [pathname]);
  const [hasWeb, setHasWeb] = useState<boolean | null>(null);
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    if (!name) return;
    fetch(`/api/plugins/${encodeURIComponent(name)}`)
      .then((r) => r.json())
      .then((d) => {
        if (d.error) {
          setErr(d.error);
          setHasWeb(false);
          return;
        }
        const ep = d.entrypoints || {};
        setHasWeb(Boolean(ep.web));
      })
      .catch((e) => {
        setErr(String(e));
        setHasWeb(false);
      });
  }, [name]);

  const src = useMemo(() => {
    if (!name) return "";
    const slugPath = slugArr.join("/");
    return `/api/plugins/${encodeURIComponent(name)}/web/${slugPath}`;
  }, [name, slugArr]);

  if (!name) return <div style={{ padding: 24 }}>missing plugin name</div>;
  if (hasWeb === null) return <div style={{ padding: 24 }}>加载中…</div>;
  if (err) return <div style={{ padding: 24, color: "#ef4444" }}>{err}</div>;
  if (!hasWeb) {
    return (
      <div style={{ padding: 24, color: "var(--text-dim)" }}>
        插件 <strong>{name}</strong> 未声明 web entrypoint。
      </div>
    );
  }

  return (
    <iframe
      src={src}
      style={{ width: "100%", height: "100%", border: 0, background: "var(--bg-primary)" }}
      title={`plugin:${name}`}
    />
  );
}
