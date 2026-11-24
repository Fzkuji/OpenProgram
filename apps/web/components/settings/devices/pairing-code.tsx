"use client";

/**
 * A one-time pairing code as a QR code and a link, for a phone to scan or
 * another computer to paste. The code works once for 10 minutes and is
 * replaced while it is shown. The link carries every address this
 * computer can be reached at (``/api/auth/pairings``), so the other side
 * tries each in turn and nobody picks an address.
 */
import { useCallback, useEffect, useState } from "react";

import { CopyButton } from "@/components/ui/copy-button";
import { QrCode } from "@/components/ui/qr-code";
import { useTranslation } from "@/lib/i18n";
import { jsonFetch } from "@/lib/net/fetch-client";

interface Pairing {
  code: string;
  expires_at: number;
  origins: string[];
  links: Record<string, string>;
}

/** The link with every address: the first one's, which already names the others. */
function fullLink(pairing: Pairing): string {
  return pairing.origins.length ? pairing.links[pairing.origins[0]] ?? "" : "";
}

export function PairingCode({ onNoRoute, hint }: { onNoRoute?: () => void; hint?: string }) {
  const { text } = useTranslation();
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);

  const mint = useCallback(() => {
    void jsonFetch<Pairing>("/api/auth/pairings", { method: "POST", body: "{}" })
      .then((data) => {
        setPairing(data);
        if (!data.origins.length) onNoRoute?.();
      })
      .catch(() => {});
  }, [onNoRoute]);

  useEffect(() => {
    mint();
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [mint]);

  const left = pairing ? Math.max(0, Math.round(pairing.expires_at - now)) : 0;
  // A code that ran out is replaced while it is shown.
  const expired = !!pairing && pairing.origins.length > 0 && left === 0;
  useEffect(() => {
    if (expired) mint();
  }, [expired, mint]);

  const link = pairing ? fullLink(pairing) : "";
  const clock = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;

  if (!pairing || !link) return <div className="h-[200px]" aria-hidden="true" />;
  return (
    <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
      <QrCode value={link} label={text("Pairing QR code", "配对二维码")} className="w-[184px] shrink-0 border p-2" />
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {hint ? <p className="text-fs-base">{hint}</p> : null}
        <div className="flex min-w-0 items-center gap-2 rounded-md border px-3 py-1.5">
          <span className="min-w-0 flex-1 truncate font-mono text-fs-sm">{link}</span>
          <CopyButton value={link} label={text("Copy link", "复制链接")} />
        </div>
        <p className="text-fs-sm text-text-muted">
          {text(`Works once. A new code comes in ${clock}.`, `只能用一次，${clock} 后自动换新码。`)}
        </p>
      </div>
    </div>
  );
}
