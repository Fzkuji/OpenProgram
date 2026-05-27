"use client";

/** General settings — theme, font, language, app metadata. */
import { useEffect, useState } from "react";

import { useTranslation, type Locale } from "@/lib/i18n";
import { useFontPref, FONT_LABELS, type FontKey } from "@/lib/font-pref";
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

  const FONT_OPTIONS: FontKey[] = ["system", "inter", "serif", "mono", "rounded"];
  const LANG_OPTIONS: { value: Locale; label: string }[] = [
    { value: "en", label: "English" },
    { value: "zh", label: "中文" },
  ];

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
                <select
                  className={styles.prefSelect}
                  value={font}
                  onChange={(e) => setFont(e.target.value as FontKey)}
                >
                  {FONT_OPTIONS.map((f) => (
                    <option key={f} value={f}>
                      {FONT_LABELS[f][locale] ?? FONT_LABELS[f].en}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className={styles.row}>
              <div className={styles.label}>{t("general.language")}</div>
              <div className={styles.value}>
                <select
                  className={styles.prefSelect}
                  value={locale}
                  onChange={(e) => setLocale(e.target.value as Locale)}
                >
                  {LANG_OPTIONS.map((l) => (
                    <option key={l.value} value={l.value}>
                      {l.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>
        </section>

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
