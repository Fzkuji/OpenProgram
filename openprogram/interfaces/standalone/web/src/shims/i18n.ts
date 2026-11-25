// The kit's i18n hook in the standalone page: the page sets its <html lang>
// from the browser's language, and the hook follows it.
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
