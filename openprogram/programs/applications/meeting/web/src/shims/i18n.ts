// The kit's i18n hook inside the application window. The host's locale reaches
// the sandboxed frame as its <html lang> (openprogramApp applies the host's
// appearance), so the hook reads that and follows it when it changes.
import { useEffect, useState } from "react";

export const LOCALES = ["en", "zh"] as const;
export type Locale = (typeof LOCALES)[number];

export const getLocale = (): Locale => (document.documentElement.lang.startsWith("zh") ? "zh" : "en");
export const setLocale = (_: Locale) => {};
const pick = (locale: Locale, variants: string[]) => variants[LOCALES.indexOf(locale)] ?? variants[0] ?? "";
export const translateText = (...variants: string[]) => pick(getLocale(), variants);

export function useTranslation() {
  const [locale, setCurrent] = useState<Locale>(getLocale);
  useEffect(() => {
    const observer = new MutationObserver(() => setCurrent(getLocale()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
    return () => observer.disconnect();
  }, []);
  return { t: (key: string) => String(key), text: (...variants: string[]) => pick(locale, variants), locale, setLocale };
}
