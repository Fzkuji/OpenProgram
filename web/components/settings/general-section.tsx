"use client";

/** General settings — theme, font, language, app metadata. */
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { useTranslation, type Locale } from "@/lib/i18n";
import { useFontPref, FONT_LABELS, fontStack, type FontKey } from "@/lib/font-pref";
import {
  DEFAULT_AGENT_PROFILE,
  setAgentProfile,
  useAgentProfile,
  type AgentAvatarConfig,
} from "@/lib/agent-style";
import { Avatar, AVATAR_STYLES, type AvatarStyle } from "@/components/avatar/Avatar";
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

// Avatar source the picker exposes. Maps 1:1 onto ``AgentAvatarConfig.kind``
// for ``letter`` / ``upload``, and treats each DiceBear style as its own
// "source" so the user picks via a single row instead of two cascaded
// controls (kind + style).
type AvatarSource = AvatarStyle | "letter" | "upload";

// Hard cap on uploaded files. 4 MB comfortably fits any sensible avatar
// GIF / PNG / SVG; anything bigger usually means the user dropped the
// wrong file (a screenshot, a video frame). The cap is enforced
// client-side; the data URL ends up in localStorage so we also avoid
// blowing the per-origin storage quota.
const UPLOAD_MAX_BYTES = 4 * 1024 * 1024;
const UPLOAD_ACCEPT =
  "image/png,image/jpeg,image/gif,image/webp,image/svg+xml,image/apng";

function _sourceOf(cfg: AgentAvatarConfig | undefined): AvatarSource {
  if (!cfg) return "shapes";
  if (cfg.kind === "upload") return "upload";
  if (cfg.kind === "letter") return "letter";
  return (cfg.style ?? "shapes") as AvatarSource;
}

function AgentSection() {
  const { t } = useTranslation();
  const profile = useAgentProfile();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const source = _sourceOf(profile.avatar);
  const isDicebear =
    source !== "letter" && source !== "upload";

  function updateName(name: string) {
    setAgentProfile({ ...profile, name: name.slice(0, 32) });
  }
  function updateInitial(raw: string) {
    const cleaned = raw.trim();
    const next = cleaned.length === 0
      ? DEFAULT_AGENT_PROFILE.initial
      : Array.from(cleaned)[0]!.toUpperCase();
    setAgentProfile({ ...profile, initial: next });
  }
  function updateColor(color: string) {
    setAgentProfile({ ...profile, color });
  }

  // Picking a source rewrites the entire ``avatar`` config so the
  // surrounding rows show the right controls. We deliberately keep
  // ``seed`` / ``file`` across switches when they exist — flipping
  // shapes → bottts shouldn't reset the user's seed; flipping
  // upload → shapes shouldn't blow away the uploaded file (they can
  // flip back).
  function pickSource(src: AvatarSource) {
    setUploadError(null);
    if (src === "letter") {
      setAgentProfile({
        ...profile,
        avatar: { kind: "letter" },
      });
      return;
    }
    if (src === "upload") {
      setAgentProfile({
        ...profile,
        avatar: { kind: "upload", file: profile.avatar?.file },
      });
      return;
    }
    setAgentProfile({
      ...profile,
      avatar: {
        kind: "dicebear",
        style: src,
        seed: profile.avatar?.seed,
      },
    });
  }

  function updateSeed(raw: string) {
    setAgentProfile({
      ...profile,
      avatar: {
        kind: "dicebear",
        style: (profile.avatar?.style ?? "shapes") as AvatarStyle,
        seed: raw,
      },
    });
  }

  function randomSeed() {
    const fresh =
      Math.random().toString(36).slice(2, 10) +
      Math.random().toString(36).slice(2, 6);
    updateSeed(fresh);
  }

  // Wire the <input type="file"> through to a data URL so the file
  // lives in localStorage alongside the rest of the profile. The
  // browser handles GIF / WebP animation natively when this URL ends
  // up in an <img src=…>, so no extra animation runtime needed.
  function onFilePicked(file: File) {
    setUploadError(null);
    if (file.size > UPLOAD_MAX_BYTES) {
      setUploadError(
        `File is ${(file.size / 1024 / 1024).toFixed(1)} MB, max ${UPLOAD_MAX_BYTES / 1024 / 1024} MB`,
      );
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      setAgentProfile({
        ...profile,
        avatar: { kind: "upload", file: dataUrl },
      });
    };
    reader.onerror = () => setUploadError("Failed to read file.");
    reader.readAsDataURL(file);
  }

  return (
    <section>
      <h3 className={styles.sectionTitle}>{t("general.section.agent")}</h3>
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
              <Avatar
                size={40}
                name={profile.name}
                config={profile.avatar}
              />
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
              placeholder={t("general.agent.name.placeholder")}
              onChange={(e) => updateName(e.target.value)}
              style={{
                padding: "6px 10px",
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                borderRadius: 6,
                color: "var(--text-primary)",
                font: "inherit",
                width: 200,
              }}
            />
          </div>
        </div>

        {/* Avatar style — picker drives which set of rows render
            below. A DiceBear style + a seed = generative SVG; the
            same seed always gets the same glyph. ``letter`` and
            ``upload`` are explicit alternatives. */}
        <div className={styles.row}>
          <div className={styles.label}>Avatar style</div>
          <div className={styles.value}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, 64px)",
                gap: 10,
                maxWidth: 520,
              }}
            >
              {AVATAR_STYLES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => pickSource(s.id)}
                  title={s.hint}
                  style={_pickerBtn(source === s.id)}
                >
                  <Avatar
                    size={40}
                    name={profile.name}
                    config={{
                      kind: "dicebear",
                      style: s.id,
                      seed: profile.avatar?.seed ?? profile.name,
                    }}
                  />
                  <span style={_pickerLabel}>{s.label}</span>
                </button>
              ))}
              {/* Letter / upload tiles share the picker grid so the
                  visual choice is one decision, not two. */}
              <button
                type="button"
                onClick={() => pickSource("letter")}
                title="Coloured circle with one letter"
                style={_pickerBtn(source === "letter")}
              >
                <Avatar
                  size={40}
                  name={profile.name}
                  config={{
                    kind: "letter",
                    letter: profile.initial,
                    bg: profile.color,
                  }}
                />
                <span style={_pickerLabel}>Letter</span>
              </button>
              <button
                type="button"
                onClick={() => pickSource("upload")}
                title="Upload your own PNG / JPG / SVG / GIF"
                style={_pickerBtn(source === "upload")}
              >
                {profile.avatar?.kind === "upload" && profile.avatar.file ? (
                  <Avatar
                    size={40}
                    name={profile.name}
                    config={profile.avatar}
                  />
                ) : (
                  <span
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: 9999,
                      border: "1px dashed var(--border-light)",
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "var(--text-muted)",
                      fontSize: 18,
                    }}
                    aria-hidden
                  >
                    +
                  </span>
                )}
                <span style={_pickerLabel}>Custom</span>
              </button>
            </div>
          </div>
        </div>

        {/* DiceBear seed input + randomise — only shown for the
            generative styles. The seed string fully determines the
            glyph, so users can dial it in or just randomise until
            they like one. */}
        {isDicebear && (
          <div className={styles.row}>
            <div className={styles.label}>Seed</div>
            <div className={styles.value}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  type="text"
                  value={profile.avatar?.seed ?? profile.name}
                  placeholder={profile.name}
                  onChange={(e) => updateSeed(e.target.value)}
                  style={{
                    padding: "6px 10px",
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    color: "var(--text-primary)",
                    font: "inherit",
                    width: 200,
                  }}
                />
                <button
                  type="button"
                  onClick={randomSeed}
                  title="Roll a fresh random seed"
                  style={_smallBtn}
                >
                  ↻ Random
                </button>
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  marginTop: 4,
                }}
              >
                Same seed always renders the same glyph.
              </div>
            </div>
          </div>
        )}

        {/* Letter-mode controls — initial + colour. Hidden for the
            DiceBear and upload paths to keep the panel focused. */}
        {source === "letter" && (
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
                    borderRadius: 6,
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
                  {AGENT_COLORS.map((c) => (
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

        {/* Upload-mode controls — file picker + preview. The file
            is read into a data URL and stashed in localStorage with
            the rest of the profile, which is the cheapest "no
            backend" path. Animated GIF / WebP play natively when
            the URL ends up in an ``<img src=…>``. */}
        {source === "upload" && (
          <div className={styles.row}>
            <div className={styles.label}>Upload</div>
            <div className={styles.value}>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  style={_smallBtn}
                >
                  Choose file…
                </button>
                {profile.avatar?.kind === "upload" && profile.avatar.file && (
                  <button
                    type="button"
                    onClick={() =>
                      setAgentProfile({
                        ...profile,
                        avatar: { kind: "upload", file: undefined },
                      })
                    }
                    style={_smallBtn}
                  >
                    Clear
                  </button>
                )}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept={UPLOAD_ACCEPT}
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onFilePicked(f);
                  // Reset so picking the same filename a second time
                  // still triggers onChange.
                  e.target.value = "";
                }}
              />
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  marginTop: 6,
                }}
              >
                PNG · JPG · SVG · GIF · WebP · APNG · max{" "}
                {UPLOAD_MAX_BYTES / 1024 / 1024} MB. Animated GIF / WebP
                play in place.
              </div>
              {uploadError && (
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--accent-red)",
                    marginTop: 4,
                  }}
                >
                  {uploadError}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

const _pickerBtn = (selected: boolean): CSSProperties => ({
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: 4,
  padding: 6,
  borderRadius: 8,
  background: selected ? "var(--bg-hover)" : "transparent",
  border: selected
    ? "1px solid color-mix(in srgb, var(--accent-orange) 50%, transparent)"
    : "1px solid var(--border)",
  cursor: "pointer",
  transition: "background-color 0.15s, border-color 0.15s",
});

const _pickerLabel: CSSProperties = {
  fontSize: 11,
  color: "var(--text-secondary)",
  fontWeight: 500,
};

const _smallBtn: CSSProperties = {
  padding: "6px 12px",
  borderRadius: 6,
  fontSize: 13,
  background: "var(--bg-hover)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
  cursor: "pointer",
  transition: "background-color 0.15s, color 0.15s, border-color 0.15s",
};

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
