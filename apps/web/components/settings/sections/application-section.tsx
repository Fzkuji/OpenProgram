"use client";

/** The running OpenProgram's version and its updates, shown at the top of
 *  Settings > Versions and Updates. */
import { useState } from "react";

import { useDesktopBridge } from "@/lib/desktop/use-desktop-bridge";
import { useDesktopUpdateState } from "@/lib/desktop/use-desktop-update";
import { type DesktopUpdateState } from "@/lib/desktop/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { useSettingsData } from "@/lib/prefs/settings-cache";
import { SYSTEM_VERSION_URL } from "@/lib/settings/settings-urls";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { SettingsRow, SettingsSection } from "../settings-ui";

/** What /api/system/version answers; checked field by field before use. */
interface HostVersion {
  currentVersion?: unknown;
  installType?: unknown;
}

export function ApplicationSection() {
  const { t, text } = useTranslation();
  const [updateState, setUpdateState] = useDesktopUpdateState();
  const bridge = useDesktopBridge();
  // In the browser the server says which version runs; kept across visits and
  // read as Settings opens. Until it answers, the rows stay empty.
  const host = useSettingsData<HostVersion>(bridge?.updates ? null : SYSTEM_VERSION_URL);
  const hostVersion = typeof host?.currentVersion === "string" ? host.currentVersion : "";
  const rawInstallType = typeof host?.installType === "string" ? host.installType : "";
  // The backend reports an enum (openprogram/updater/detect.py); show words.
  const installType = !host ? ""
    : rawInstallType === "source_checkout"
      ? text("Source checkout", "源码检出")
      : rawInstallType === "managed_release"
        ? text("Managed release", "托管发行版")
        : text("Unknown", "未知");
  const [updateActionError, setUpdateActionError] = useState<string | null>(null);

  const runUpdateAction = async (action: () => Promise<DesktopUpdateState | null | void>) => {
    setUpdateActionError(null);
    try {
      const state = await action();
      if (state) setUpdateState(state);
    } catch (error) {
      setUpdateActionError(error instanceof Error ? error.message : String(error));
    }
  };

  const failedStatusText = text("Update check failed", "更新检查失败");
  const statusDetail = updateActionError
    || (updateState?.status === "error" ? updateState.error : null)
    || undefined;
  const statusText = (() => {
    if (updateActionError) return failedStatusText;
    switch (updateState?.status) {
      case "checking": return text("Checking…", "正在检查…");
      case "up-to-date": return text("Up to date", "已是最新版本");
      case "available": return text(`OpenProgram ${updateState.release?.latestVersion} is available`, `OpenProgram ${updateState.release?.latestVersion} 可用`);
      case "downloading": {
        const progress = updateState.progress;
        const percentage = progress?.total ? Math.floor(progress.downloaded / progress.total * 100) : 0;
        const downloaded = progress?.downloaded.toLocaleString() || "0";
        const total = progress?.total.toLocaleString() || "0";
        return text(
          `Downloading… ${downloaded} / ${total} bytes (${percentage}%)`,
          `正在下载… ${downloaded} / ${total} 字节（${percentage}%）`,
        );
      }
      case "downloaded": return text("Installer opened", "安装程序已打开");
      case "ready": return text(
        `OpenProgram ${updateState.readyVersion} is ready. Restart to update.`,
        `OpenProgram ${updateState.readyVersion} 已就绪，重启即可更新`,
      );
      case "installing": return text("Restarting to update…", "正在重启以更新…");
      case "not-published": return text(
        `OpenProgram ${updateState.release?.latestVersion} has no file for this computer yet`,
        `OpenProgram ${updateState.release?.latestVersion} 还没有这台电脑的安装文件`,
      );
      case "error": return failedStatusText;
      default: return text("Not checked", "尚未检查");
    }
  })();

  const busy = updateState?.status === "checking" || updateState?.status === "downloading"
    || updateState?.status === "installing";
  const readyVersion = updateState?.readyVersion;
  const progress = updateState?.progress;

  return (
    <SettingsSection title={t("general.section.application")}>
      <SettingsRow label={t("general.version")}>
        <span className="text-fs-base text-text-muted">{updateState?.currentVersion || hostVersion}</span>
      </SettingsRow>
      {bridge?.updates ? (
        <>
          <SettingsRow label={text("Automatically check for updates", "自动检查更新")} htmlFor="automatic-update-checks">
            <Switch
              id="automatic-update-checks"
              checked={updateState?.automaticChecks ?? true}
              onCheckedChange={(checked) => { void runUpdateAction(() => bridge.updates.setAutomaticChecks(checked)); }}
            />
          </SettingsRow>
          <SettingsRow label={text("Update status", "更新状态")} description={statusDetail}>
            <span
              className="text-fs-base text-text-muted"
              role={updateState?.status === "downloading" ? "progressbar" : "status"}
              aria-live={updateState?.status === "downloading" ? undefined : "polite"}
              aria-atomic="true"
              aria-valuemin={progress ? 0 : undefined}
              aria-valuemax={progress?.total}
              aria-valuenow={progress?.downloaded}
              aria-valuetext={progress ? statusText : undefined}
            >
              {statusText}
            </span>
          </SettingsRow>
          {updateState?.release?.publishedAt && updateState.release.status === "available" && (
            <SettingsRow label={text("Published", "发布时间")}>
              <span className="text-fs-base text-text-muted">{new Date(updateState.release.publishedAt).toLocaleDateString()}</span>
            </SettingsRow>
          )}
          {updateState?.release?.releaseNotes && updateState.release.status === "available" && (
            <SettingsRow label={text("Release notes", "版本说明")} stacked>
              <p className="max-h-60 overflow-y-auto text-fs-base whitespace-pre-wrap text-text-muted">
                {updateState.release.releaseNotes}
              </p>
            </SettingsRow>
          )}
          <SettingsRow label={text("Actions", "操作")}>
            <Button variant="elevated" disabled={busy} onClick={() => { void runUpdateAction(() => bridge.updates.check()); }}>
              {text("Check now", "立即检查")}
            </Button>
            {readyVersion && bridge.updates.install ? (
              <Button disabled={busy} onClick={() => { void runUpdateAction(() => bridge.updates.install!()); }}>
                {text("Restart to update", "重启以更新")}
              </Button>
            ) : updateState?.release?.status === "available" && (
              <Button disabled={busy} onClick={() => { void runUpdateAction(() => bridge.updates.download()); }}>
                {updateState.installsInPlace
                  ? text("Download update", "下载更新")
                  : text("Download and open installer", "下载并打开安装程序")}
              </Button>
            )}
            {updateState?.release && (
              <Button variant="elevated" onClick={() => { void runUpdateAction(() => bridge.updates.openRelease()); }}>
                {text("View release", "查看 Release")}
              </Button>
            )}
          </SettingsRow>
        </>
      ) : (
        <>
          <SettingsRow label={text("Installation", "安装类型")}>
            <span className="text-fs-base text-text-muted">{installType}</span>
          </SettingsRow>
          <SettingsRow label={text("Check for updates", "检查更新")}>
            <div className="grid gap-1 text-right font-mono text-fs-base text-text-muted">
              <code>openprogram upgrade --check</code>
              <code>openprogram upgrade</code>
            </div>
          </SettingsRow>
        </>
      )}
    </SettingsSection>
  );
}
