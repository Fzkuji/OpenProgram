"use client";

/** General settings — theme, font, language, app metadata. */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { useTranslation, type Locale } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import {
  desktopBridge,
  type DesktopBridge,
  type DesktopUpdateState,
} from "@/lib/desktop-bridge";
import { useFontPref, FONT_LABELS, fontStack, type FontKey } from "@/lib/prefs/font-pref";
import {
  setAgentProfile,
  useAgentProfile,
} from "@/lib/format-utils/agent-style";
import { setUserProfile, useUserProfile } from "@/lib/prefs/user-profile";
import {
  Avatar,
  AvatarPicker,
  type AvatarConfig,
} from "@/components/avatar";
import {
  useThemePref,
  ACCENT_PRESETS,
  CUSTOM_CSS_TEMPLATE,
  THEME_MODES,
  THEME_STYLES,
  THEME_STYLE_PAIRS,
  type ThemeMode,
  type ThemeStyle,
} from "@/lib/prefs/theme-pref";
import styles from "./settings-page.module.css";

const FONT_OPTIONS: FontKey[] = ["system", "inter", "serif", "mono"];

type DropdownOption<T extends string> = {
  value: T;
  label: string;
  style?: CSSProperties;
};

function SettingsDropdown<T extends string>({
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
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const current = options.find((option) => option.value === value);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className={styles.settingsDropdownWrap}>
      <button
        type="button"
        className={styles.settingsDropdownTrigger}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
      >
        <span style={current?.style}>{current?.label}</span>
        <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden>
          <path
            d="M2 4l4 4 4-4"
            stroke="currentColor"
            strokeWidth="1.5"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {open && (
        <div className={styles.settingsDropdownMenu} role="listbox">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={
                styles.settingsDropdownOption +
                (option.value === value ? " " + styles.settingsDropdownOptionActive : "")
              }
              style={option.style}
              onClick={() => { onChange(option.value); setOpen(false); }}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const FONT_SELECT_OPTIONS: DropdownOption<FontKey>[] = FONT_OPTIONS.map((font) => ({
  value: font,
  label: FONT_LABELS[font],
  style: { fontFamily: fontStack(font) },
}));

const LANG_OPTIONS: DropdownOption<Locale>[] = [
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
];

/** Eighteen palette colours kept in sync with agent-style.PALETTE so
 *  the swatches in /settings match what bubbles can actually display. */
const AGENT_COLORS = [
  "#4f8ef7", "#5aad4e", "#d4843a", "#9d6fe0", "#e0445a", "#2db3d5",
  "#e0b020", "#35b89a", "#e066b3", "#6b8dd6", "#8fbf3f", "#d9694f",
  "#52c4c4", "#b08be0", "#c79a4a", "#e08a3a", "#6fae6f", "#d05fa0",
];

/** Profile fields shared by the Agent and You sections. */
interface ProfilePrefs {
  name: string;
  initial: string;
  color: string;
  avatar?: AvatarConfig;
}

/** The avatar / name / colour editor body — reused by both the Agent
 *  and You sections (identical controls, different profile store). */
function ProfileEditor({
  profile,
  onChange,
  colors,
  namePlaceholder,
}: {
  profile: ProfilePrefs;
  onChange: (next: ProfilePrefs) => void;
  colors: string[];
  namePlaceholder: string;
}) {
  const { t } = useTranslation();

  function updateName(name: string) {
    onChange({ ...profile, name: name.slice(0, 32) });
  }
  function updateInitial(raw: string) {
    const cleaned = raw.trim();
    const next =
      cleaned.length === 0
        ? Array.from(profile.name.trim())[0]?.toUpperCase() ?? "?"
        : Array.from(cleaned)[0]!.toUpperCase();
    onChange({ ...profile, initial: next });
  }
  function updateColor(color: string) {
    onChange({ ...profile, color });
  }
  function updateAvatar(next: AvatarConfig) {
    onChange({ ...profile, avatar: next });
  }

  return (
    <div className={styles.card}>
        <div className={styles.row}>
          <div className={styles.label}>{t("general.agent.preview")}</div>
          <div className={styles.control}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 10,
              }}
            >
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
              <span style={{ fontWeight: 600 }}>{profile.name}</span>
            </span>
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>{t("general.agent.name")}</div>
          <div className={styles.control}>
            <input
              type="text"
              value={profile.name}
              maxLength={32}
              placeholder={namePlaceholder}
              onChange={(e) => updateName(e.target.value)}
              style={{
                padding: "6px 10px",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                borderRadius: "var(--ui-button-radius)",
                color: "var(--text-primary)",
                font: "inherit",
                width: 200,
              }}
            />
          </div>
        </div>

        <div className={styles.row + " " + styles.rowTop}>
          <div className={styles.label}>{t("general.avatar")}</div>
          <div className={styles.control}>
            <AvatarPicker
              value={profile.avatar}
              onChange={updateAvatar}
              name={profile.name}
              letterBg={profile.color}
              letterText={profile.initial}
              onLetterBgChange={updateColor}
              onLetterTextChange={updateInitial}
              colors={colors}
            />
          </div>
        </div>
      </div>
  );
}

function AgentSection() {
  const { t } = useTranslation();
  const profile = useAgentProfile();
  return (
    <section>
      <h3 className={styles.sectionTitle}>{t("general.section.agent")}</h3>
      <ProfileEditor
        profile={profile}
        onChange={setAgentProfile}
        colors={AGENT_COLORS}
        namePlaceholder={t("general.agent.name.placeholder")}
      />
    </section>
  );
}

function UserSection() {
  const { t } = useTranslation();
  const profile = useUserProfile();
  return (
    <section>
      <h3 className={styles.sectionTitle}>{t("general.section.you")}</h3>
      <ProfileEditor
        profile={profile}
        onChange={setUserProfile}
        colors={AGENT_COLORS}
        namePlaceholder={t("general.you.name.placeholder")}
      />
    </section>
  );
}

function ApplicationSection() {
  const { t, text } = useTranslation();
  const [updateState, setUpdateState] = useState<DesktopUpdateState | null>(null);
  const [hostVersion, setHostVersion] = useState("unknown");
  const [installType, setInstallType] = useState("unknown");
  const [bridge, setBridge] = useState<DesktopBridge | null>(null);
  const [updateActionError, setUpdateActionError] = useState<string | null>(null);

  useEffect(() => {
    const currentBridge = desktopBridge();
    setBridge(currentBridge);
    const updates = currentBridge?.updates;
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
  }, []);

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
    <section>
      <h3 className={styles.sectionTitle}>{t("general.section.application")}</h3>
      <div className={styles.card}>
        <div className={styles.row}>
          <div className={styles.label}>{t("general.version")}</div>
          <div className={styles.control}>{updateState?.currentVersion || hostVersion}</div>
        </div>
        {bridge?.updates ? (
          <>
            <div className={styles.row}>
              <label className={styles.label} htmlFor="automatic-update-checks">
                {text("Automatically check for updates", "自动检查更新")}
              </label>
              <div className={styles.control}>
                <input
                  id="automatic-update-checks"
                  type="checkbox"
                  checked={updateState?.automaticChecks ?? true}
                  onChange={(event) => { void runUpdateAction(() => bridge.updates.setAutomaticChecks(event.target.checked)); }}
                />
              </div>
            </div>
            <div className={styles.row}>
              <div className={styles.label}>{text("Update status", "更新状态")}</div>
              <div
                className={styles.control}
                role={updateState?.status === "downloading" ? "progressbar" : "status"}
                aria-live={updateState?.status === "downloading" ? undefined : "polite"}
                aria-atomic="true"
                aria-valuemin={progress ? 0 : undefined}
                aria-valuemax={progress?.total}
                aria-valuenow={progress?.downloaded}
                aria-valuetext={progress ? statusText : undefined}
                title={statusDetail}
              >
                {statusText}
              </div>
            </div>
            {updateState?.release?.publishedAt && updateState.release.status === "available" && (
              <div className={styles.row}>
                <div className={styles.label}>{text("Published", "发布时间")}</div>
                <div className={styles.control}>{new Date(updateState.release.publishedAt).toLocaleDateString()}</div>
              </div>
            )}
            {updateState?.release?.releaseNotes && updateState.release.status === "available" && (
              <div className={`${styles.row} ${styles.rowTop}`}>
                <div className={styles.label}>{text("Release notes", "版本说明")}</div>
                <div className={styles.control} style={{ whiteSpace: "pre-wrap", textAlign: "left" }}>
                  {updateState.release.releaseNotes.slice(0, 600)}
                </div>
              </div>
            )}
            <div className={styles.row}>
              <div className={styles.label}>{text("Actions", "操作")}</div>
              <div className={styles.control} style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <Button variant="outline" size="sm" className={"text-fs-base " + styles.settingsAction} disabled={busy} onClick={() => { void runUpdateAction(() => bridge.updates.check()); }}>
                  {text("Check now", "立即检查")}
                </Button>
                {updateState?.release?.status === "available" && (
                  <Button size="sm" className={"text-fs-base " + styles.settingsAction} disabled={busy} onClick={() => { void runUpdateAction(() => bridge.updates.download()); }}>
                    {text("Download and open installer", "下载并打开安装程序")}
                  </Button>
                )}
                {updateState?.release && (
                  <Button variant="outline" size="sm" className={"text-fs-base " + styles.settingsAction} onClick={() => { void runUpdateAction(() => bridge.updates.openRelease()); }}>
                    {text("View release", "查看 Release")}
                  </Button>
                )}
              </div>
            </div>
          </>
        ) : (
          <>
            <div className={styles.row}>
              <div className={styles.label}>{text("Installation", "安装类型")}</div>
              <div className={styles.control}>{installType}</div>
            </div>
            <div className={styles.row}>
              <div className={styles.label}>{text("Check for updates", "检查更新")}</div>
              <div className={styles.value} style={{ display: "grid", gap: 4 }}>
                <code>openprogram upgrade --check</code>
                <code>openprogram upgrade</code>
              </div>
            </div>
          </>
        )}
        <div className={styles.row}>
          <div className={styles.label}>{t("general.framework")}</div>
          <div className={styles.control}>Agentic Programming</div>
        </div>
      </div>
    </section>
  );
}

export function GeneralSection() {
  const { t, text, locale, setLocale } = useTranslation();
  const { font, setFont } = useFontPref();
  const {
    style,
    mode,
    setStyle,
    setMode,
    accent,
    setAccent,
    resetAccent,
    packageAccent,
    customCss,
    customCssEnabled,
    setCustomCssEnabled,
    setCustomCss,
    insertCustomCssTemplate,
    clearCustomCss,
  } = useThemePref();

  const MODE_LABELS: Record<ThemeMode, string> = {
    auto: text("Auto", "跟随系统"),
    dark: text("Dark", "深色"),
    light: text("Light", "浅色"),
  };

  const STYLE_LABELS: Record<ThemeStyle, string> = {
    beige: text("Beige", "暖色"),
    neutral: text("Neutral", "中性"),
    aurora: text("Aurora", "极光"),
  };

  const accentValue = accent ?? packageAccent;

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <h2 className={styles.pageTitle}>{t("general.title")}</h2>
        <p className={styles.pageMeta}>{t("general.meta")}</p>
      </div>
      <div className={styles.pageBody}>
        <section>
          <h3 className={styles.sectionTitle}>{t("general.section.preferences")}</h3>
          <div className={styles.card}>
            <div className={styles.row + " " + styles.rowTop}>
              <div className={styles.label}>{text("Mode", "明暗模式")}</div>
              <div className={styles.control}>
                <div className={styles.themeGrid}>
                  {THEME_MODES.map((m) => (
                    <button
                      key={m}
                      type="button"
                      className={
                        styles.themeCard +
                        (mode === m ? " " + styles.active : "")
                      }
                      onClick={() => setMode(m)}
                      title={MODE_LABELS[m]}
                      aria-pressed={mode === m}
                    >
                      <span
                        className={styles.themeSwatch}
                        data-theme={
                          m === "auto"
                            ? THEME_STYLE_PAIRS[style].dark
                            : THEME_STYLE_PAIRS[style][m]
                        }
                        aria-hidden="true"
                      >
                        <span
                          className={styles.themeDot}
                          style={{ background: "var(--accent-orange)" }}
                        />
                        <span
                          className={styles.themeDot}
                          style={{ background: "var(--accent-green)" }}
                        />
                        <span
                          className={styles.themeDot}
                          style={{ background: "var(--text-primary)" }}
                        />
                      </span>
                      <span className={styles.themeCardLabel}>
                        {MODE_LABELS[m]}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className={styles.row + " " + styles.rowTop}>
              <div className={styles.label}>{text("Color style", "颜色风格")}</div>
              <div className={styles.control}>
                <div className={styles.themeGrid}>
                  {THEME_STYLES.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={
                        styles.themeCard +
                        (style === s ? " " + styles.active : "")
                      }
                      onClick={() => setStyle(s)}
                      title={STYLE_LABELS[s]}
                      aria-pressed={style === s}
                    >
                      <span
                        className={styles.themeSwatch}
                        data-theme={
                          mode === "auto"
                            ? THEME_STYLE_PAIRS[s].dark
                            : THEME_STYLE_PAIRS[s][mode]
                        }
                        aria-hidden="true"
                      >
                        <span
                          className={styles.themeDot}
                          style={{ background: "var(--accent-orange)" }}
                        />
                        <span
                          className={styles.themeDot}
                          style={{ background: "var(--accent-green)" }}
                        />
                        <span
                          className={styles.themeDot}
                          style={{ background: "var(--text-primary)" }}
                        />
                      </span>
                      <span className={styles.themeCardLabel}>
                        {STYLE_LABELS[s]}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className={styles.row + " " + styles.rowTop}>
              <div className={styles.label}>
                {text("Accent color", "强调色")}
              </div>
              <div className={styles.control}>
                <div className={styles.accentControls}>
                  <input
                    type="color"
                    className={styles.accentColorInput}
                    aria-label={text("Accent color", "强调色")}
                    value={accentValue}
                    onChange={(e) => setAccent(e.target.value)}
                  />
                  <div className={styles.accentPresets} role="group" aria-label={text("Accent presets", "强调色预设")}>
                    {ACCENT_PRESETS.map((hex) => (
                      <button
                        key={hex}
                        type="button"
                        className={styles.accentPreset}
                        style={{ background: hex }}
                        aria-label={hex}
                        title={hex}
                        aria-pressed={accentValue.toLowerCase() === hex.toLowerCase()}
                        onClick={() => setAccent(hex)}
                      />
                    ))}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className={"text-fs-base " + styles.settingsAction}
                    onClick={() => resetAccent()}
                    disabled={!accent}
                  >
                    {text("Reset to theme default", "重置为主题默认")}
                  </Button>
                </div>
                <div className={styles.customCssHint}>
                  {text(
                    "Overrides the current theme package accent. Empty / reset uses that package's default.",
                    "覆盖当前主题包的强调色。留空或重置则使用该主题包的默认强调色。",
                  )}
                </div>
              </div>
            </div>

            <div className={styles.row + " " + styles.rowTop}>
              <div className={styles.label}>
                {text("Custom CSS", "自定义 CSS")}
              </div>
              <div className={styles.control}>
                <div className={styles.customCssToolbar}>
                  <label className={styles.customCssEnable}>
                    <input
                      type="checkbox"
                      checked={customCssEnabled}
                      onChange={(e) => setCustomCssEnabled(e.target.checked)}
                    />
                    {text("Enable custom CSS", "启用自定义 CSS")}
                  </label>
                  <Button variant="outline" size="sm" className={"text-fs-base " + styles.settingsAction} onClick={() => insertCustomCssTemplate()}>
                    {text("Insert template", "插入模板")}
                  </Button>
                  <Button variant="outline" size="sm" className={"text-fs-base " + styles.settingsAction} onClick={() => clearCustomCss()}>
                    {text("Clear", "清空")}
                  </Button>
                </div>
                <textarea
                  className={styles.customCssArea}
                  aria-label={text("Custom CSS", "自定义 CSS")}
                  value={customCss}
                  spellCheck={false}
                  autoComplete="off"
                  autoCorrect="off"
                  autoCapitalize="off"
                  placeholder={CUSTOM_CSS_TEMPLATE}
                  onChange={(e) => setCustomCss(e.target.value)}
                />
                <div className={styles.customCssHint}>
                  {text(
                    "Overlay on the current theme package. Turn on Enable custom CSS to apply it. Use Insert template for a starter snippet, or Clear to remove it. Applies live, saved in this browser. You can still target any data-theme.",
                    "叠加在当前主题包之上。打开「启用自定义 CSS」后生效。可用「插入模板」写入示例，或「清空」删除。即时生效，仅保存在本浏览器。仍可针对任意 data-theme。",
                  )}
                </div>
              </div>
            </div>

            <div className={styles.row}>
              <div className={styles.label}>{t("general.font")}</div>
              <div className={styles.control}>
                <SettingsDropdown
                  value={font}
                  options={FONT_SELECT_OPTIONS}
                  onChange={setFont}
                  label={t("general.font")}
                />
              </div>
            </div>

            <div className={styles.row}>
              <div className={styles.label}>{t("general.language")}</div>
              <div className={styles.control}>
                <SettingsDropdown
                  value={locale}
                  options={LANG_OPTIONS}
                  onChange={setLocale}
                  label={t("general.language")}
                />
              </div>
            </div>
          </div>
        </section>

        <AgentSection />

        <UserSection />

        <ApplicationSection />
      </div>
    </div>
  );
}
