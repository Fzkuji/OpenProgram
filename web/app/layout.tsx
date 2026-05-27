import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

// Google Fonts (next/font/google) was hitting fonts.googleapis.com at
// build/request time. When that domain is unreachable (locally proxied,
// offline, GFW), Next.js falls back to a default serif and the chat
// header / mono columns render in an ugly system serif instead of the
// monospace we wanted. Drop the network-dependent fetch entirely and
// rely on the CSS fallback chain in styles/base.css
// (Menlo / Monaco / Consolas for mono, system-ui for sans). The
// --font-inter / --font-jetbrains-mono vars stay undefined; CSS's
// font-family list resolves to the next named fallback, which is what
// users have on macOS / Windows anyway.

export const metadata: Metadata = {
  title: "OpenProgram",
  description: "Agentic programming runtime",
  // Explicit icon path keeps browsers from requesting /favicon.ico
  // (which would 404). app/icon.svg + this metadata combine to set
  // <link rel="icon" href="/icon.svg"> in the rendered <head>.
  icons: {
    icon: "/icon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/*
          Apply persisted theme before React hydrates so the page
          paints in the correct mode. Without this, every route
          renders as dark (CSS default) until a component that
          imports applyTheme mounts — which only happens on the
          Settings page. The listener also keeps `auto` reactive to
          system color-scheme changes app-wide, not just in settings.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function () {
              try {
                var saved = localStorage.getItem('agentic_theme') || 'auto';
                function apply(t) {
                  if (t === 'auto') {
                    var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
                    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
                  } else {
                    document.documentElement.setAttribute('data-theme', t);
                  }
                }
                apply(saved);
                window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
                  if ((localStorage.getItem('agentic_theme') || 'auto') === 'auto') apply('auto');
                });

                // Apply persisted font + language before hydrate so the first
                // paint already shows the user's choice (otherwise the page
                // flashes in the default font/locale for ~200ms while React
                // mounts useFontPref / useTranslation).
                var FONTS = {
                  system: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', sans-serif",
                  inter:  "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', 'Hiragino Sans GB', sans-serif",
                  serif:  "'Source Serif Pro', 'Iowan Old Style', Georgia, 'Times New Roman', 'Songti SC', SimSun, 'Noto Serif CJK SC', serif",
                  mono:   "'JetBrains Mono', ui-monospace, Menlo, Monaco, Consolas, 'PingFang SC', 'Microsoft YaHei', monospace"
                };
                var f = localStorage.getItem('agentic_font') || 'system';
                if (FONTS[f]) document.documentElement.style.setProperty('--font-sans', FONTS[f]);

                var lang = localStorage.getItem('agentic_locale') || 'en';
                document.documentElement.setAttribute('lang', lang === 'zh' ? 'zh-CN' : 'en');
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
