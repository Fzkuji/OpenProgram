"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";

export default function PluginWebPage() {
  const params = useParams<{ name: string; slug: string[] }>();
  const name = params?.name;
  const slugArr = Array.isArray(params?.slug) ? params.slug : [];
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
