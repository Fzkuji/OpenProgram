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
  sourceOf,
  type AvatarConfig,
} from "@/components/avatar";
import {
  useThemePref,
  AUTO_DARK,
  type ThemePref,
} from "@/lib/prefs/theme-pref";
import styles from "./settings-page.module.css";

/** 选择器里的条目顺序。'auto' 排最前（默认值）。 */
const THEME_CHOICES: ThemePref[] = [
  "auto",
  "beige-dark",
  "beige-light",
  "dark",
  "light",
  "aurora",
  "custom",
];

/** 自定义 CSS 的起手模板 —— 直接写在 placeholder 里，复制即用。
 *  只列必须覆写的一组；其余 token 不写就继承 :root 兜底值。 */
const CUSTOM_CSS_TEMPLATE = `[data-theme="custom"] {
  color-scheme: dark;

  /* 表面 */
  --bg-primary: #1e1e20;
  --bg-secondary: #171719;
  --bg-tertiary: #252529;
  --bg-input: #2a2a2e;
  --bg-hover: rgba(255, 255, 255, 0.06);
  --bg-selected: rgba(255, 255, 255, 0.10);

  /* 文字（四档） */
  --text-bright: #ededf0;
  --text-primary: #b6b6bb;
  --text-secondary: #92929a;
  --text-muted: #74747c;

  /* 边框 */
  --border: rgba(255, 255, 255, 0.07);
  --border-light: rgba(255, 255, 255, 0.12);

  /* 强调色 */
  --accent-orange: #d97757;
  --accent-fill: #d97757;
}`;

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

  const source = sourceOf(profile.avatar);
  const isLetterMode = source === "letter";

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
          <div className={styles.value}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 10,
              }}
            >
              <Avatar size={40} name={profile.name} config={profile.avatar} />
              <span style={{ fontWeight: 600 }}>{profile.name}</span>
            </span>
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.label}>{t("general.agent.name")}</div>
          <div className={styles.value}>
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

        {/* Avatar style + seed + upload — all owned by the avatar
            feature module. This page just hands it the current
            config and a setter; the picker decides what controls to
            render based on which source the user has chosen.
            ``rowTop`` keeps the "Avatar style" label pinned to the
            top of the tall picker block instead of centring it. */}
        <div className={`${styles.row} ${styles.rowTop}`}>
          <div className={styles.label}>Avatar style</div>
          <div className={styles.value}>
            <AvatarPicker
              value={profile.avatar}
              onChange={updateAvatar}
              name={profile.name}
              letterBg={profile.color}
              letterText={profile.initial}
            />
          </div>
        </div>

        {/* Letter-mode initial + colour — owned by the page
            (because they live on ``AgentProfilePrefs`` directly,
            not on ``avatar``). Hidden when the picker is on a
            DiceBear / upload source to keep the panel focused. */}
        {isLetterMode && (
          <>
            <div className={styles.row}>
              <div className={styles.label}>{t("general.agent.initial")}</div>
              <div className={styles.value}>
                <input
                  type="text"
                  value={profile.initial}
                  maxLength={2}
                  onChange={(e) => updateInitial(e.target.value)}
                  style={{
                    padding: "6px 10px",
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--ui-button-radius)",
                    color: "var(--text-primary)",
                    font: "inherit",
                    width: 64,
                    textAlign: "center",
                  }}
                />
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--text-muted)",
                    marginTop: 4,
                  }}
                >
                  {t("general.agent.initial.hint")}
                </div>
              </div>
            </div>

            <div className={styles.row}>
              <div className={styles.label}>{t("general.agent.color")}</div>
              <div className={styles.value}>
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 6,
                    maxWidth: 280,
                  }}
                >
                  {colors.map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => updateColor(c)}
                      aria-label={c}
                      title={c}
                      style={{
                        width: 24,
                        height: 24,
                        borderRadius: 6,
                        background: c,
                        border:
                          profile.color === c
                            ? "2px solid var(--text-primary)"
                            : "1px solid var(--border)",
                        cursor: "pointer",
                        padding: 0,
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </>
        )}
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

  useEffect(() => {
    const currentBridge = desktopBridge();
    setBridge(currentBridge);
    const updates = currentBridge?.updates;
    if (updates) {
      let active = true;
      void updates.getState().then((state) => {
        if (active && state) setUpdateState(state);
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

  const statusText = (() => {
    switch (updateState?.status) {
      case "checking": return text("Checking…", "正在检查…");
      case "up-to-date": return text("Up to date", "已是最新版本");
      case "available": return text(`OpenProgram ${updateState.release?.latestVersion} is available`, `OpenProgram ${updateState.release?.latestVersion} 可用`);
      case "downloading": {
        const progress = updateState.progress;
        const percentage = progress?.total ? Math.floor(progress.downloaded / progress.total * 100) : 0;
        return text(`Downloading… ${percentage}%`, `正在下载… ${percentage}%`);
      }
      case "downloaded": return text("DMG opened", "DMG 已打开");
      case "error": return updateState.error || text("Update check failed", "更新检查失败");
      default: return text("Not checked", "尚未检查");
    }
  })();

  const busy = updateState?.status === "checking" || updateState?.status === "downloading";

  return (
    <section>
      <h3 className={styles.sectionTitle}>{t("general.section.application")}</h3>
      <div className={styles.card}>
        <div className={styles.row}>
          <div className={styles.label}>{t("general.version")}</div>
          <div className={styles.value}>{updateState?.currentVersion || hostVersion}</div>
        </div>
        {bridge?.updates ? (
          <>
            <div className={styles.row}>
              <label className={styles.label} htmlFor="automatic-update-checks">
                {text("Automatically check for updates", "自动检查更新")}
              </label>
              <input
                id="automatic-update-checks"
                type="checkbox"
                checked={updateState?.automaticChecks ?? true}
                onChange={(event) => { void bridge.updates.setAutomaticChecks(event.target.checked); }}
              />
            </div>
            <div className={styles.row}>
              <div className={styles.label}>{text("Update status", "更新状态")}</div>
              <div className={styles.value}>{statusText}</div>
            </div>
            {updateState?.release?.releaseNotes && updateState.status === "available" && (
              <div className={`${styles.row} ${styles.rowTop}`}>
                <div className={styles.label}>{text("Release notes", "版本说明")}</div>
                <div className={`${styles.value} ${styles.valueWide}`} style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>
                  {updateState.release.releaseNotes.slice(0, 600)}
                </div>
              </div>
            )}
            <div className={styles.row}>
              <div className={styles.label}>{text("Actions", "操作")}</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" }}>
                <Button variant="outline" size="sm" disabled={busy} onClick={() => { void bridge.updates.check(); }}>
                  {text("Check now", "立即检查")}
                </Button>
                {updateState?.status === "available" && (
                  <Button size="sm" disabled={busy} onClick={() => { void bridge.updates.download(); }}>
                    {text("Download and open DMG", "下载并打开 DMG")}
                  </Button>
                )}
                {updateState?.release && (
                  <Button variant="outline" size="sm" onClick={() => { void bridge.updates.openRelease(); }}>
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
              <div className={styles.value}>{installType}</div>
            </div>
            <div className={styles.row}>
              <div className={styles.label}>{text("Check for updates", "检查更新")}</div>
              <code>openprogram upgrade --check</code>
            </div>
          </>
        )}
        <div className={styles.row}>
          <div className={styles.label}>{t("general.framework")}</div>
          <div className={styles.value}>Agentic Programming</div>
        </div>
      </div>
    </section>
  );
}

export function GeneralSection() {
  const { t, text, locale, setLocale } = useTranslation();
  const { font, setFont } = useFontPref();
  const { theme, setTheme, customCss, setCustomCss } = useThemePref();

  // 卡片上的预览块直接挂对应的 data-theme，让 var() 解析成那套主题的
  // 真实取值——不用在 TS 里再抄一份色号。'auto' 预览用它解析到的深色。
  const previewTheme = (id: ThemePref) => (id === "auto" ? AUTO_DARK : id);

  const THEME_LABELS: Record<ThemePref, string> = {
    auto: text("Auto", "跟随系统"),
    "beige-dark": text("Beige Dark", "暖色深"),
    "beige-light": text("Beige Light", "暖色浅"),
    dark: text("Dark", "深色"),
    light: text("Light", "浅色"),
    aurora: text("Aurora", "极光"),
    custom: text("Custom", "自定义"),
  };

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
              <div className={styles.label}>{t("general.appearance")}</div>
              <div className={styles.value + " " + styles.valueWide}>
                <div className={styles.themeGrid}>
                  {THEME_CHOICES.map((id) => (
                    <button
                      key={id}
                      type="button"
                      className={
                        styles.themeCard +
                        (theme === id ? " " + styles.active : "")
                      }
                      onClick={() => setTheme(id)}
                      title={THEME_LABELS[id]}
                      /* The active card is marked only by a CSS class;
                         aria-pressed tells a screen reader which theme
                         is currently in effect. */
                      aria-pressed={theme === id}
                    >
                      <span
                        className={styles.themeSwatch}
                        data-theme={previewTheme(id)}
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
                        {THEME_LABELS[id]}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className={styles.row + " " + styles.rowTop}>
              <div className={styles.label}>
                {text("Custom CSS", "自定义 CSS")}
              </div>
              <div className={styles.value + " " + styles.valueWide}>
                <textarea
                  className={styles.customCssArea}
                  aria-label={text("Custom CSS", "自定义 CSS")}
                  value={customCss}
                  spellCheck={false}
                  // Chrome 会在刷新时"恢复"表单里上一次的文本，这会盖掉
                  // 受控值——用户看到的和实际生效的 CSS 就对不上了。
                  // 真值在 localStorage，这里必须关掉浏览器自动恢复。
                  autoComplete="off"
                  autoCorrect="off"
                  autoCapitalize="off"
                  placeholder={CUSTOM_CSS_TEMPLATE}
                  onChange={(e) => setCustomCss(e.target.value)}
                />
                <div className={styles.customCssHint}>
                  {text(
                    'Define [data-theme="custom"] and pick the "Custom" theme above. Unset tokens fall back to the default palette. Applies live, saved in this browser.',
                    '定义 [data-theme="custom"] 后在上方选择"自定义"主题。未覆写的 token 回落到默认取值。即时生效，仅保存在本浏览器。',
                  )}
                </div>
              </div>
            </div>

            <div className={styles.row}>
              <div className={styles.label}>{t("general.font")}</div>
              <div className={styles.value}>
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
              <div className={styles.value}>
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
