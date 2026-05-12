import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "OpenProgram",
  description: "Agentic programming runtime",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
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
