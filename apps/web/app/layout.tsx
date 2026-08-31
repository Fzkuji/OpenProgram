import type { Metadata } from "next";
import "./globals.css";
import "@xterm/xterm/css/xterm.css";
import "katex/dist/katex.min.css";
import { Providers } from "./providers";
import { THEME_BOOTSTRAP_SCRIPT } from "@/lib/prefs/theme-bootstrap";

// Google Fonts (next/font/google) was hitting fonts.googleapis.com at
// build/request time. When that domain is unreachable (locally proxied,
// offline, GFW), Next.js falls back to a default serif. We dropped the
// network fetch and bundle Inter Variable locally (see globals.css);
// font selection is a runtime CSS-variable override, not next/font.

export const metadata: Metadata = {
  title: "OpenProgram",
  description: "Agentic programming runtime",
  // Register both SVG and ICO. The ICO covers browser fallbacks and
  // framework error pages that request /favicon.ico directly.
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/favicon.ico", sizes: "any" },
    ],
    shortcut: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // Static export: no request-time cookies. The synchronous <head>
  // script below reads the font cookie and sets --font-sans before
  // first paint, so the chosen font still shows without a flash.
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/*
          Apply persisted theme + language before React hydrates so the
          page paints in the correct mode/locale. Font is already set
          server-side via the inline style above; this script only
          re-syncs it from the cookie (source of truth) as defence in
          depth and to pick up a change made in another tab.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function () {
              try {
                ${THEME_BOOTSTRAP_SCRIPT}

                // Font is already inlined by SSR from the cookie. Re-read
                // the cookie (source of truth) and re-apply only if it
                // differs — covers a font changed in another tab since
                // this document was served. Cookie first, localStorage
                // as a legacy fallback.
                var FONTS = {
                  system: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', sans-serif",
                  inter:  "'Inter Variable', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', sans-serif",
                  serif:  "'Source Serif Pro', 'Iowan Old Style', Georgia, 'Times New Roman', 'Songti SC', SimSun, 'Noto Serif CJK SC', serif",
                  mono:   "'JetBrains Mono Variable', 'JetBrains Mono', ui-monospace, Menlo, Monaco, Consolas, 'PingFang SC', 'Microsoft YaHei', monospace"
                };
                var cm = document.cookie.match(/(?:^|;\\s*)agentic_font=([^;]+)/);
                var cookieFont = cm && cm[1];
                var f = cookieFont || localStorage.getItem('agentic_font') || 'inter';
                if (FONTS[f]) document.documentElement.style.setProperty('--font-sans', FONTS[f]);
                // Migrate/heal: if the cookie is missing (legacy user with
                // only localStorage, or first load after this change), write
                // it now so the NEXT SSR paint is already correct. This runs
                // on every page — useFontPref only mounts on Settings, so it
                // can't be relied on to seed the cookie.
                if (!cookieFont && FONTS[f]) {
                  document.cookie = 'agentic_font=' + f + '; path=/; max-age=31536000; samesite=lax';
                }

                // 没存过就看浏览器语言（zh* → zh，其余 en）。和
                // lib/i18n/index.ts 的 readStored() 逻辑必须一致。
                var lang = localStorage.getItem('agentic_locale')
                  || (/^zh/i.test(navigator.language || '') ? 'zh' : 'en');
                document.documentElement.setAttribute('lang', lang === 'zh' ? 'zh-CN' : 'en');

                // 左侧栏收起状态也要赶在首帧之前打上：SSR HTML 是展开
                // 的，等 React 水合再改就会先画一帧展开、再播收起动画。
                // CSS 里 html[data-sidebar-closed] #sidebar 强制收起宽度；
                // 水合完成后由 Sidebar 组件移除该属性接管。
                if (localStorage.getItem('sidebarOpen') === '0') {
                  document.documentElement.setAttribute('data-sidebar-closed', '');
                }
              } catch (e) {}
            })();`,
          }}
        />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
