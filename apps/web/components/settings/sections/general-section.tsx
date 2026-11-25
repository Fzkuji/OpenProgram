"use client";

/** General settings — language, suggestions, user, notifications and the
 *  web server. How the interface looks lives in Appearance
 *  (appearance-section.tsx); the version and updates in Versions and Updates. */
import { useId, type CSSProperties } from "react";

import { useTranslation, type Locale } from "@/lib/i18n";
import { Switch } from "@/components/ui/switch";
import { setUserProfile, useUserProfile, type UserProfilePrefs } from "@/lib/prefs/user-profile";
import {
  Avatar,
  AvatarPicker,
  type AvatarConfig,
} from "@/components/avatar";
import { DesktopAttentionSection } from "./desktop-attention-section";
import { WebPushSection } from "./web-push-section";
import { SchemaSettings } from "./schema-settings";
import { SimpleSelect } from "@/components/ui/select";
import { setComposerPredictions, useComposerPredictions } from "@/lib/prefs/composer-predictions";
import { Input } from "@/components/ui/input";
import { SettingsPage, SettingsRow, SettingsSection } from "../settings-ui";

export type DropdownOption<T extends string> = {
  value: T;
  label: string;
  style?: CSSProperties;
};

export function SettingsDropdown<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: DropdownOption<T>[];
  onChange: (v: T) => void;
  /** Name of the setting this dropdown controls. The visible row label
   *  is a plain <div>, so it can't be associated with htmlFor — pass the
   *  same string here and the trigger announces "Font, Inter" instead of
   *  a bare value. */
  label?: string;
}) {
  return (
    <SimpleSelect
      value={value}
      onValueChange={onChange}
      aria-label={label}
      options={options.map((option) => ({
        value: option.value,
        label: option.style ? <span style={option.style}>{option.label}</span> : option.label,
      }))}
    />
  );
}

const LANG_OPTIONS: DropdownOption<Locale>[] = [
  { value: "en", label: "English" },
  { value: "zh", label: "中文" },
];

/** Letter-avatar background swatches. */
const PROFILE_COLORS = [
  "#4f8ef7", "#5aad4e", "#d4843a", "#9d6fe0", "#e0445a", "#2db3d5",
  "#e0b020", "#35b89a", "#e066b3", "#6b8dd6", "#8fbf3f", "#d9694f",
  "#52c4c4", "#b08be0", "#c79a4a", "#e08a3a", "#6fae6f", "#d05fa0",
];

/** The local user's name and avatar, shown at the bottom of the sidebar. */
function UserSection() {
  const { t } = useTranslation();
  const profile = useUserProfile();
  const nameId = useId();

  function update(next: Partial<UserProfilePrefs>) {
    setUserProfile({ ...profile, ...next });
  }
  function updateInitial(raw: string) {
    const cleaned = raw.trim();
    const initial =
      cleaned.length === 0
        ? Array.from(profile.name.trim())[0]?.toUpperCase() ?? "?"
        : Array.from(cleaned)[0]!.toUpperCase();
    update({ initial });
  }

  return (
    <SettingsSection title={t("general.section.you")}>
      <SettingsRow label={t("general.profile.preview")}>
        <span className="inline-flex items-center gap-2.5">
          <Avatar
            size={40}
            name={profile.name}
            config={
              profile.avatar?.kind === "letter"
                ? {
                    kind: "letter",
                    letter: profile.initial,
                    bg: profile.color,
                  }
                : profile.avatar
            }
          />
          <span className="font-medium">{profile.name}</span>
        </span>
      </SettingsRow>
      <SettingsRow label={t("general.profile.name")} htmlFor={nameId}>
        <Input
          id={nameId}
          value={profile.name}
          maxLength={32}
          placeholder={t("general.you.name.placeholder")}
          onChange={(e) => update({ name: e.target.value.slice(0, 32) })}
        />
      </SettingsRow>
      <SettingsRow label={t("general.avatar")} stacked>
        <AvatarPicker
          value={profile.avatar}
          onChange={(avatar: AvatarConfig) => update({ avatar })}
          name={profile.name}
          letterBg={profile.color}
          letterText={profile.initial}
          onLetterBgChange={(color) => update({ color })}
          onLetterTextChange={updateInitial}
          colors={PROFILE_COLORS}
        />
      </SettingsRow>
    </SettingsSection>
  );
}

export function GeneralSection() {
  const { t, text, locale, setLocale } = useTranslation();
  const predictions = useComposerPredictions();

  return (
    <SettingsPage title={t("general.title")} description={t("general.meta")}>
      <SettingsSection title={t("general.section.preferences")}>
        <SettingsRow label={t("general.language")}>
          <SettingsDropdown
            value={locale}
            options={LANG_OPTIONS}
            onChange={setLocale}
            label={t("general.language")}
          />
        </SettingsRow>

        <SettingsRow
          label={text("Suggest your next message after each reply (Tab to use it; one extra model call per turn)",
            "每次回复后建议下一句（按 Tab 采用；每轮多一次模型调用）")}
          htmlFor="composer-predictions"
        >
          <Switch id="composer-predictions" checked={predictions} onCheckedChange={setComposerPredictions} />
        </SettingsRow>
      </SettingsSection>

      <UserSection />

      <DesktopAttentionSection />

      <WebPushSection />

      <SchemaSettings group="Ports" title={text("Web server", "网页服务")} />

    </SettingsPage>
  );
}
