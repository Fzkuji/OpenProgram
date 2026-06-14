"use client";

/** General settings — theme, font, language, app metadata. */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { useTranslation, type Locale } from "@/lib/i18n";
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
import styles from "./settings-page.module.css";

type Theme = "light" | "auto" | "dark";

function applyTheme(theme: Theme) {
  if (theme === "auto") {
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
}

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
}: {
  value: T;
  options: DropdownOption<T>[];
  onChange: (v: T) => void;
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

export function GeneralSection() {
  const { t, locale, setLocale } = useTranslation();
  const { font, setFont } = useFontPref();
  const [theme, setThemeState] = useState<Theme>("auto");

  useEffect(() => {
    const saved = (localStorage.getItem("agentic_theme") || "auto") as Theme;
    setThemeState(saved);
    applyTheme(saved);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (localStorage.getItem("agentic_theme") === "auto") applyTheme("auto");
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  function pickTheme(next: Theme) {
    localStorage.setItem("agentic_theme", next);
    setThemeState(next);
    applyTheme(next);
  }

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
            <div className={styles.row}>
              <div className={styles.label}>{t("general.appearance")}</div>
              <div className={styles.value}>
                <div className={styles.themeSwitcher}>
                  {(["light", "auto", "dark"] as const).map((th) => (
                    <button
                      key={th}
                      className={
                        styles.themeBtn + (theme === th ? " " + styles.active : "")
                      }
                      onClick={() => pickTheme(th)}
                    >
                      {t(`general.theme.${th}` as
                        | "general.theme.light"
                        | "general.theme.auto"
                        | "general.theme.dark")}
                    </button>
                  ))}
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
                />
              </div>
            </div>
          </div>
        </section>

        <AgentSection />

        <UserSection />

        <section>
          <h3 className={styles.sectionTitle}>{t("general.section.application")}</h3>
          <div className={styles.card}>
            <div className={styles.row}>
              <div className={styles.label}>{t("general.version")}</div>
              <div className={styles.value}>0.1.0</div>
            </div>
            <div className={styles.row}>
              <div className={styles.label}>{t("general.framework")}</div>
              <div className={styles.value}>Agentic Programming</div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
