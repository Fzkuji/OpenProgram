"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { Switch } from "@/components/ui/switch";
import { SimpleSelect } from "@/components/ui/select";
import { useDesktopBridge } from "@/lib/desktop/use-desktop-bridge";
import type { DesktopAttentionApi, DesktopAttentionPrefs } from "@/lib/desktop/desktop-bridge-types";
import { FieldError } from "@/components/ui/field";
import { SettingsRow, SettingsSection } from "../settings-ui";

/** Desktop notifications and keep-awake. Rendered only inside the desktop shell. */
export function DesktopAttentionSection() {
  const { text } = useTranslation();
  const attention = useDesktopBridge()?.attention ?? null;
  const [prefs, setPrefs] = useState<DesktopAttentionPrefs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sounds, setSounds] = useState<string[]>([]);

  useEffect(() => {
    const api = attention;
    if (!api) return;
    let active = true;
    void api.getPrefs().then((next) => { if (active) setPrefs(next); })
      .catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); });
    void api.sounds?.().then((names) => { if (active) setSounds(names); }).catch(() => {});
    const unsubscribe = api.onPrefs((next) => { if (active) setPrefs(next); });
    return () => { active = false; unsubscribe(); };
  }, [attention]);

  if (!attention) return null;

  const update = (patch: Partial<DesktopAttentionPrefs>) => {
    setError(null);
    void attention.setPrefs(patch).then(setPrefs).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : String(reason));
    });
  };

  return (
    <SettingsSection title={text("Notifications", "通知")}>
      <SettingsRow
        label={text("Notify when a session finishes or needs you while OpenProgram is in the background",
          "OpenProgram 在后台时，会话完成或需要你回复就发通知")}
        htmlFor="desktop-notifications"
      >
        <Switch
          id="desktop-notifications"
          checked={prefs?.notifications ?? true}
          disabled={!prefs}
          onCheckedChange={(checked) => update({ notifications: checked })}
        />
      </SettingsRow>
      <SettingsRow label={text("Notification sound", "通知声音")} htmlFor="desktop-notification-sound">
        <SimpleSelect
          id="desktop-notification-sound"
          value={prefs?.sound ?? "default"}
          disabled={!prefs || !prefs.notifications}
          aria-label={text("Notification sound", "通知声音")}
          onValueChange={(sound) => {
            update({ sound });
            void attention.previewSound?.(sound);
          }}
          options={[
            { value: "default", label: text("Default", "默认") },
            { value: "none", label: text("None", "无") },
            ...(sounds.length ? [{
              label: text("System sounds", "系统声音"),
              options: sounds.map((name) => ({ value: `system:${name}`, label: name })),
            }] : []),
          ]}
        />
      </SettingsRow>
      <SettingsRow
        label={text("Keep the computer awake while a session is running", "会话运行时保持电脑不休眠")}
        htmlFor="desktop-keep-awake"
      >
        <Switch
          id="desktop-keep-awake"
          checked={prefs?.keepAwake ?? true}
          disabled={!prefs}
          onCheckedChange={(checked) => update({ keepAwake: checked })}
        />
      </SettingsRow>
      {error && (
        <FieldError className="py-4">{error}</FieldError>
      )}
    </SettingsSection>
  );
}
