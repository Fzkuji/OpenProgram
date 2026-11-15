import localFont from "next/font/local";

// Inter's Latin face through next/font: the build adds a <link rel="preload">
// for it, so the file is requested with the HTML instead of after the CSS,
// and `block` keeps text from being painted in the system font first and
// swapped (a cold restart could show that switch). Other scripts still use
// the @fontsource-variable/inter faces imported in globals.css. JetBrains
// Mono's Latin face loads the same way: ids, paths and code would otherwise
// paint in Menlo first and visibly reflow when the swap lands.
export const interLatin = localFont({
  src: "../../../node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2",
  weight: "100 900",
  style: "normal",
  display: "block",
  variable: "--font-inter-latin",
  adjustFontFallback: false,
});

export const monoLatin = localFont({
  src: "../../../node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2",
  weight: "100 800",
  style: "normal",
  display: "block",
  variable: "--font-mono-latin",
  adjustFontFallback: false,
});
