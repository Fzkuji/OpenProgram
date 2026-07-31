import type { Config } from "tailwindcss";

/**
 * Tailwind config — mirrors every design token defined in
 * `app/styles/01-base.css` (`:root`) so Tailwind utilities can use
 * them directly:
 *   text-fs-base, text-fs-sm     ← typography scale
 *   text-accent-orange, bg-accent-blue/15  ← accents
 *   text-text-bright, bg-bg-primary        ← text/bg roles (auto theme-aware)
 *   rounded-composer-corner                 ← composer geometry
 *   font-mono / font-sans                   ← families
 *
 * Tokens still live in the CSS file (single source of truth), this
 * config just hands Tailwind the names. Existing `.module.css` files
 * keep using `var(--xxx)` and remain interchangeable.
 */
const config: Config = {
  darkMode: ["class"],
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        /* generic shadcn-style slots (already present, kept). */
        border: "var(--border)",
        "border-light": "var(--border-light)",
        input: "var(--input)",
        ring: "var(--ring)",
        background: "var(--background)",
        foreground: "var(--foreground)",
        primary: {
          DEFAULT: "var(--primary)",
          foreground: "var(--primary-foreground)",
        },
        secondary: {
          DEFAULT: "var(--secondary)",
          foreground: "var(--secondary-foreground)",
        },
        destructive: {
          DEFAULT: "var(--destructive)",
          foreground: "var(--destructive-foreground)",
        },
        muted: {
          DEFAULT: "var(--muted)",
          foreground: "var(--muted-foreground)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          foreground: "var(--accent-foreground)",
        },
        popover: {
          DEFAULT: "var(--popover)",
          foreground: "var(--popover-foreground)",
        },
        card: {
          DEFAULT: "var(--card)",
          foreground: "var(--card-foreground)",
        },
        /* text / bg roles — auto theme-aware via `var(--xxx)`. */
        "text-primary": "var(--text-primary)",
        "text-secondary": "var(--text-secondary)",
        "text-bright": "var(--text-bright)",
        "text-muted": "var(--text-muted)",
        "bg-primary": "var(--bg-primary)",
        "bg-secondary": "var(--bg-secondary)",
        "bg-tertiary": "var(--bg-tertiary)",
        "bg-hover": "var(--bg-hover)",
        "bg-hover-contrast": "var(--bg-hover-contrast)",
        "bg-input": "var(--bg-input)",
        "bg-selected": "var(--bg-selected)",
        "user-msg-bg": "var(--user-msg-bg)",
        "assistant-msg-bg": "var(--assistant-msg-bg)",
        /* fixed accents. */
        "accent-blue": "var(--accent-blue)",
        "accent-green": "var(--accent-green)",
        "accent-red": "var(--accent-red)",
        "accent-yellow": "var(--accent-yellow)",
        "accent-purple": "var(--accent-purple)",
        "accent-cyan": "var(--accent-cyan)",
        "accent-orange": "var(--accent-orange)",
        /* sidebar nav default / hover colors — diverge from text-* in
           light mode so we can't alias them to text-primary. */
        "nav-color": "var(--nav-color)",
        "nav-color-hover": "var(--nav-color-hover)",
      },
      borderRadius: {
        lg: "var(--radius-lg)",
        md: "var(--radius)",
        sm: "6px",
        "composer-corner": "var(--composer-corner)",
      },
      fontFamily: {
        mono: "var(--font-mono)",
        sans: "var(--font-sans)",
      },
      fontSize: {
        "fs-sm": "var(--fs-sm)",
        "fs-base": "var(--fs-base)",
        "fs-md": "var(--fs-md)",
        "fs-lg": "var(--fs-lg)",
      },
      spacing: {
        "composer-button": "var(--composer-button-size)",
        "composer-offset": "var(--composer-button-offset)",
        "composer-pad": "var(--composer-bottom-row-pad)",
        "composer-row": "var(--composer-bottom-row-h)",
        "sidebar-w": "var(--sidebar-width)",
        "detail-w": "var(--detail-width)",
      },
      transitionTimingFunction: {
        composer: "cubic-bezier(0.25, 0.1, 0.25, 1)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "spin-refresh": {
          from: { transform: "rotate(0deg)" },
          to: { transform: "rotate(360deg)" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "spin-refresh": "spin-refresh 0.6s ease-in-out",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
