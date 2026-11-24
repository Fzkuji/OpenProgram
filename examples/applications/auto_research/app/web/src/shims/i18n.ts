// The kit's i18n hook, fixed to Chinese: the app page cannot reach the host's
// locale (its storage is not readable from the sandboxed frame).
export type Locale = "en" | "zh";
const pick = (variants: string[]) => variants[1] ?? variants[0] ?? "";
export const getLocale = (): Locale => "zh";
export const setLocale = (_: Locale) => {};
export const translateText = (...variants: string[]) => pick(variants);
export function useTranslation() {
  return { t: (key: string) => String(key), text: (...variants: string[]) => pick(variants), locale: "zh" as Locale, setLocale };
}
