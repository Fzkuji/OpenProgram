/**
 * The spoken languages speech-to-text can be told to expect, as ISO 639-1
 * codes with each language's own name. Settings → Voice offers them for the
 * composer microphone and the Meetings window for a recording; both also
 * offer automatic detection, which is not in this list.
 */
export const SPOKEN_LANGUAGES = [
  { code: "zh", name: "中文" },
  { code: "en", name: "English" },
  { code: "ja", name: "日本語" },
  { code: "ko", name: "한국어" },
  { code: "fr", name: "Français" },
  { code: "de", name: "Deutsch" },
  { code: "es", name: "Español" },
] as const;

export type SpokenLanguage = (typeof SPOKEN_LANGUAGES)[number]["code"];

/** A language's own name, or the code itself for one this list does not name. */
export function spokenLanguageName(code: string): string {
  return SPOKEN_LANGUAGES.find(language => language.code === code)?.name ?? code;
}
