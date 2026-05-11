"use client";

/** General settings — port of /js/shared/settings-general.js. */
import { useEffect, useState } from "react";
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

  function set(t: Theme) {
    localStorage.setItem("agentic_theme", t);
    setThemeState(t);
    applyTheme(t);
  }

  return (
    <>
      <div className={styles.section}>
        <h2 className={styles.sectionTitle}>Appearance</h2>
        <div className={styles.card}>
          <div className={styles.row}>
            <div className={styles.label}>Color mode</div>
            <div className={styles.value}>
              <div className={styles.themeSwitcher}>
                {(["light", "auto", "dark"] as const).map((t) => (
                  <button
                    key={t}
                    className={
                      styles.themeBtn + (theme === t ? " " + styles.active : "")
                    }
                    onClick={() => set(t)}
                  >
                    {t[0].toUpperCase() + t.slice(1)}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className={styles.section}>
        <h2 className={styles.sectionTitle}>Application</h2>
        <div className={styles.card}>
          <div className={styles.row}>
            <div className={styles.label}>Version</div>
            <div className={styles.value}>0.1.0</div>
          </div>
          <div className={styles.row}>
            <div className={styles.label}>Framework</div>
            <div className={styles.value}>Agentic Programming</div>
          </div>
        </div>
      </div>
    </>
  );
}
