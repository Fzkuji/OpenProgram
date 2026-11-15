"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useDesktopBridge } from "@/lib/desktop/use-desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { BrowserImportDialog } from "@/components/center-tabs/browser/browser-home-page";
import { SettingsHeading, SettingsPage, SettingsRow, SettingsSection } from "../settings-ui";

export function BrowserSettings() {
  const { t, text } = useTranslation();
  const bridge = useDesktopBridge();
  const [showImport, setShowImport] = useState(false);
  const [clearHistory, setClearHistory] = useState(true);
  const [clearCookies, setClearCookies] = useState(true);
  const [clearing, setClearing] = useState(false);
  const [clearResult, setClearResult] = useState("");

  async function clearData() {
    if (!bridge?.browserData || (!clearHistory && !clearCookies)) return;
    setClearing(true);
    setClearResult("");
    try {
      const result = await bridge.browserData.clear({
        history: clearHistory,
        cookies: clearCookies,
      });
      setClearResult(result.ok
        ? text("Selected browsing data was cleared.", "已清除所选浏览数据。")
        : text("Browsing data could not be cleared.", "无法清除浏览数据。"));
    } catch {
      setClearResult(text("Browsing data could not be cleared.", "无法清除浏览数据。"));
    } finally {
      setClearing(false);
    }
  }

  // Only the desktop bridge can clear browser storage.
  const canClear = Boolean(bridge?.browserData);

  return (
    <SettingsPage
      title={t("settings.tab.browser")}
      description={text(
        "Import and clear the built-in browser's local data. The bookmarks bar is in Appearance.",
        "导入和清除内置浏览器的本地数据。书签栏在「外观」里。",
      )}
    >
      {bridge?.browserImport ? (
        showImport ? (
          <section className="flex flex-col gap-3">
            <SettingsHeading title={text("Import", "导入")} />
            <BrowserImportDialog onDismiss={() => setShowImport(false)} />
          </section>
        ) : (
          <SettingsSection title={text("Import", "导入")}>
            <SettingsRow
              label={text("Import from another browser", "从其他浏览器导入")}
              description={text("Copies history, bookmarks and cookies from a browser on this computer.", "从这台电脑上的浏览器复制历史、书签和 Cookie。")}
            >
              <Button variant="elevated" onClick={() => setShowImport(true)}>
                {text("Import data", "导入资料")}
              </Button>
            </SettingsRow>
          </SettingsSection>
        )
      ) : null}

      <SettingsSection
        className="scroll-mt-6"
        title={<span id="clear-data">{text("Clear browsing data", "清除浏览数据")}</span>}
      >
        <SettingsRow label={text("Browsing history", "浏览历史")}>
          <Switch
            checked={clearHistory}
            disabled={!canClear}
            onCheckedChange={setClearHistory}
            aria-label={text("Browsing history", "浏览历史")}
          />
        </SettingsRow>
        <SettingsRow label={text("Cookies", "Cookie")}>
          <Switch
            checked={clearCookies}
            disabled={!canClear}
            onCheckedChange={setClearCookies}
            aria-label={text("Cookies", "Cookie")}
          />
        </SettingsRow>
        <SettingsRow
          label={text("Clear the selected data", "清除所选数据")}
          description={
            clearResult
              ? <span role="status">{clearResult}</span>
              : canClear
                ? text("Bookmarks are not removed.", "不会删除书签。")
                : text("Bookmarks are not removed. Clearing is available in the desktop app.", "不会删除书签。清除功能仅在桌面 App 中可用。")
          }
        >
          <Button
            variant="destructive"
            disabled={clearing || !canClear || (!clearHistory && !clearCookies)}
            onClick={() => void clearData()}
          >
            {clearing ? text("Clearing…", "正在清除…") : text("Clear data", "清除资料")}
          </Button>
        </SettingsRow>
      </SettingsSection>
    </SettingsPage>
  );
}
