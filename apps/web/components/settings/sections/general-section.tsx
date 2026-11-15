"use client";

/** General settings — language, suggestions, user, notifications, app metadata.
 *  How the interface looks lives in Appearance (appearance-section.tsx). */
import { useDesktopBridge } from "@/lib/desktop/use-desktop-bridge";
import { useEffect, useId, useState, type CSSProperties } from "react";

import { useTranslation, type Locale } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  type DesktopUpdateState,
} from "@/lib/desktop/desktop-bridge";
import { setUserProfile, useUserProfile, type UserProfilePrefs } from "@/lib/prefs/user-profile";
import {
  Avatar,
  AvatarPicker,
  type AvatarConfig,
} from "@/components/avatar";
import { DesktopAttentionSection } from "./desktop-attention-section";
import { SimpleSelect } from "@/components/ui/select";
import { setComposerPredictions, useComposerPredictions } from "@/lib/prefs/composer-predictions";
import { Input } from "@/components/ui/input";
import { SettingsPage, SettingsRow, SettingsSection } from "../settings-ui";

export type DropdownOption<T extends string> = {
  value: T;
  label: string;
  style?: CSSProperties;
};

export function SettingsDropdown<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: DropdownOption<T>[];
  onChange: (v: T) => void;
  /** Name of the setting this dropdown controls. The visible row label
   *  is a plain <div>, so it can't be associated with htmlFor — pass the
   *  same string here and the trigger announces "Font, Inter" instead of
   *  a bare value. */
  label?: string;
}) {
  return (
    <SimpleSelect
      value={value}
      onValueChange={onChange}
      aria-label={label}
      options={options.map((option) => ({
        value: option.value,
        label: option.style ? <span style={option.style}>{option.label}</span> : option.label,
      }))}
    />
  );
}

const LANG_OPTIONS: DropdownOption<Locale>[] = [
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
];

/** Letter-avatar background swatches. */
const PROFILE_COLORS = [
  "#4f8ef7", "#5aad4e", "#d4843a", "#9d6fe0", "#e0445a", "#2db3d5",
  "#e0b020", "#35b89a", "#e066b3", "#6b8dd6", "#8fbf3f", "#d9694f",
  "#52c4c4", "#b08be0", "#c79a4a", "#e08a3a", "#6fae6f", "#d05fa0",
];

/** The local user's name and avatar, shown at the bottom of the sidebar. */
function UserSection() {
  const { t } = useTranslation();
  const profile = useUserProfile();
  const nameId = useId();

  function update(next: Partial<UserProfilePrefs>) {
    setUserProfile({ ...profile, ...next });
  }
  function updateInitial(raw: string) {
    const cleaned = raw.trim();
    const initial =
      cleaned.length === 0
        ? Array.from(profile.name.trim())[0]?.toUpperCase() ?? "?"
        : Array.from(cleaned)[0]!.toUpperCase();
    update({ initial });
  }

  return (
    <SettingsSection title={t("general.section.you")}>
      <SettingsRow label={t("general.profile.preview")}>
        <span className="inline-flex items-center gap-2.5">
          <Avatar
            size={40}
            name={profile.name}
            config={
              profile.avatar?.kind === "letter"
                ? {
                    kind: "letter",
                    letter: profile.initial,
                    bg: profile.color,
                  }
                : profile.avatar
            }
          />
          <span className="font-medium">{profile.name}</span>
        </span>
      </SettingsRow>
      <SettingsRow label={t("general.profile.name")} htmlFor={nameId}>
        <Input
          id={nameId}
          value={profile.name}
          maxLength={32}
          placeholder={t("general.you.name.placeholder")}
          onChange={(e) => update({ name: e.target.value.slice(0, 32) })}
        />
      </SettingsRow>
      <SettingsRow label={t("general.avatar")} stacked>
        <AvatarPicker
          value={profile.avatar}
          onChange={(avatar: AvatarConfig) => update({ avatar })}
          name={profile.name}
          letterBg={profile.color}
          letterText={profile.initial}
          onLetterBgChange={(color) => update({ color })}
          onLetterTextChange={updateInitial}
          colors={PROFILE_COLORS}
        />
      </SettingsRow>
    </SettingsSection>
  );
}

export function ApplicationSection() {
  const { t, text } = useTranslation();
  const [updateState, setUpdateState] = useState<DesktopUpdateState | null>(null);
  const [hostVersion, setHostVersion] = useState("unknown");
  const [rawInstallType, setInstallType] = useState("unknown");
  // The backend reports an enum (openprogram/updater/detect.py); show words.
  const installType = rawInstallType === "source_checkout"
    ? text("Source checkout", "源码检出")
    : rawInstallType === "managed_release"
      ? text("Managed release", "托管发行版")
      : text("Unknown", "未知");
  const bridge = useDesktopBridge();
  const [updateActionError, setUpdateActionError] = useState<string | null>(null);

  useEffect(() => {
    const updates = bridge?.updates;
    if (updates) {
      let active = true;
      void updates.getState().then((state) => {
        if (active && state) setUpdateState(state);
      }).catch((error: unknown) => {
        if (active) setUpdateActionError(error instanceof Error ? error.message : String(error));
      });
      const unsubscribe = updates.onState((state) => {
        if (active) setUpdateState(state);
      });
      return () => {
        active = false;
        unsubscribe();
      };
    }

    const controller = new AbortController();
    void fetch("/api/system/version", { signal: controller.signal })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("version request failed")))
      .then((payload: { currentVersion?: unknown; installType?: unknown }) => {
        if (typeof payload.currentVersion === "string") setHostVersion(payload.currentVersion);
        if (typeof payload.installType === "string") setInstallType(payload.installType);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [bridge]);

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
      case "error": return failedStatusText;
      default: return text("Not checked", "尚未检查");
    }
  })();

  const busy = updateState?.status === "checking" || updateState?.status === "downloading";
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
            {updateState?.release?.status === "available" && (
              <Button disabled={busy} onClick={() => { void runUpdateAction(() => bridge.updates.download()); }}>
                {text("Download and open installer", "下载并打开安装程序")}
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

export function GeneralSection() {
  const { t, text, locale, setLocale } = useTranslation();
  const predictions = useComposerPredictions();

  return (
    <SettingsPage title={t("general.title")} description={t("general.meta")}>
      <SettingsSection title={t("general.section.preferences")}>
        <SettingsRow label={t("general.language")}>
          <SettingsDropdown
            value={locale}
            options={LANG_OPTIONS}
            onChange={setLocale}
            label={t("general.language")}
          />
        </SettingsRow>

        <SettingsRow
          label={text("Suggest your next message after each reply (Tab to use it; one extra model call per turn)",
            "每次回复后建议下一句（按 Tab 采用；每轮多一次模型调用）")}
          htmlFor="composer-predictions"
        >
          <Switch id="composer-predictions" checked={predictions} onCheckedChange={setComposerPredictions} />
        </SettingsRow>
      </SettingsSection>

      <UserSection />

      <DesktopAttentionSection />

      <ApplicationSection />
    </SettingsPage>
  );
}
