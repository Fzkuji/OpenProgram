"use client";

/**
 * WebTabPane — center-column content of a web tab (kind "web"): a
 * 40px address toolbar (mono URL input · reload · open-external) over
 * a sandboxed <iframe>, with a persistent slim hint bar in between —
 * many sites refuse framing (X-Frame-Options / CSP frame-ancestors)
 * and a cross-origin load failure is not reliably detectable from
 * here, so the open-externally escape hatch stays always visible.
 *
 * Long-term path: a sidecar browser + CDP screencast rendering into
 * this pane (agent-driven browsing shares the same surface). The tab
 * model — kind "web" with {url, title} in the center-tabs store — is
 * the stable contract; only this pane's rendering backend changes.
 *
 * No back/forward buttons: iframe history is unreliable cross-origin.
 */
import { useEffect, useState } from "react";
import { ExternalLink, RotateCw } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { normalizeWebUrl, useCenterTabs } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";

export function WebTabPane({ tabId, url }: { tabId: string; url: string }) {
  const { text } = useTranslation();
  const updateWebTab = useCenterTabs((s) => s.updateWebTab);
  const [address, setAddress] = useState(url);
  // Bumping remounts the iframe — that's the reload button.
  const [frameEpoch, setFrameEpoch] = useState(0);

  // Store url changed elsewhere (restore, future agent navigation) →
  // resync the address bar.
  useEffect(() => setAddress(url), [url]);

  function navigate() {
    const normalized = normalizeWebUrl(address);
    if (!normalized) {
      setAddress(url); // invalid input → snap back to the real URL
      return;
    }
    setAddress(normalized);
    if (normalized === url) {
      setFrameEpoch((e) => e + 1); // same URL → treat Enter as reload
    } else {
      updateWebTab(tabId, { url: normalized });
    }
  }

  function openExternal() {
    window.open(url, "_blank", "noopener");
  }

  return (
    <div className={styles.webPane}>
      <div className={styles.webToolbar}>
        <input
          className={styles.webAddress}
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") navigate();
          }}
          spellCheck={false}
          autoComplete="off"
          aria-label={text("Address", "地址")}
        />
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={() => setFrameEpoch((e) => e + 1)}
          title={text("Reload", "重新加载")}
        >
          <RotateCw size={14} />
        </button>
        <button
          type="button"
          className={styles.webToolbarBtn}
          onClick={openExternal}
          title={text("Open in browser", "在浏览器中打开")}
        >
          <ExternalLink size={14} />
        </button>
      </div>
      <div className={styles.webHint}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
          {text(
            "If the page stays blank, the site refuses embedding — open it externally.",
            "页面空白说明该站点拒绝内嵌，请点右上角外部打开。",
          )}
        </span>
        <button type="button" className={styles.webHintLink} onClick={openExternal}>
          <ExternalLink size={11} />
          {text("Open externally", "外部打开")}
        </button>
      </div>
      <iframe
        key={frameEpoch}
        className={styles.webFrame}
        src={url}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
        referrerPolicy="no-referrer"
        title={text("Web page", "网页")}
      />
    </div>
  );
}
