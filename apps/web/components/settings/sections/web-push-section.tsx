"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useDesktopBridge } from "@/lib/desktop/use-desktop-bridge";
import {
  choosePushCategories, disablePush, enablePush, pushBlocker, readPush, testPush,
  type PushBlocker, type PushCategory,
} from "@/lib/push/web-push";
import { SettingsRow, SettingsSection } from "../settings-ui";

const NAMES: Record<PushCategory, [string, string]> = {
  attention: ["When a conversation needs you", "对话在等你时"],
  replies: ["When a reply is ready", "回复好了时"],
  failures: ["When a conversation stops with an error", "对话出错停下时"],
};

const WHY: Record<Exclude<PushBlocker, null>, [string, string]> = {
  insecure: ["Open OpenProgram through Anywhere (Settings → Devices) to get notifications here.",
    "通过“任何地方”打开 OpenProgram（设置 → 设备）才能在这里收通知。"],
  home_screen: ["Add OpenProgram to the Home Screen and open it from there.",
    "先把 OpenProgram 添加到主屏幕，再从主屏幕打开。"],
  unsupported: ["This browser has no notifications.", "这个浏览器不支持通知。"],
  denied: ["Notifications are blocked for this site in the browser's settings.",
    "浏览器设置里屏蔽了这个网站的通知。"],
};

/** Notifications on a phone or browser, through Web Push from this computer.
 *  The desktop App has its own (DesktopAttentionSection). */
export function WebPushSection() {
  const { text, locale } = useTranslation();
  const desktop = useDesktopBridge();
  const [blocker, setBlocker] = useState<PushBlocker>("unsupported");
  const [categories, setCategories] = useState<PushCategory[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const found = pushBlocker();
    setBlocker(found);
    if (found !== null) return;
    let active = true;
    void readPush(locale).then((next) => { if (active) setCategories(next); }).catch(() => {});
    return () => { active = false; };
  }, [locale]);

  if (desktop) return null;

  const on = categories !== null;
  const run = (work: () => Promise<void>) => {
    setBusy(true);
    void work().catch(() => {}).finally(() => { setBusy(false); setBlocker(pushBlocker()); });
  };
  const toggle = (checked: boolean) => run(async () => {
    if (checked) setCategories(await enablePush(locale));
    else { await disablePush(); setCategories(null); }
  });
  const choose = (name: PushCategory, checked: boolean) => run(async () => {
    const next = checked ? [...(categories ?? []), name] : (categories ?? []).filter((item) => item !== name);
    setCategories(await choosePushCategories(locale, next));
  });

  return (
    <SettingsSection title={text("Notifications", "通知")}>
      <SettingsRow
        label={text("Notify this device", "在这台设备上通知")}
        description={blocker ? text(...WHY[blocker]) : undefined}
        htmlFor="web-push"
      >
        <Switch id="web-push" checked={on} disabled={blocker !== null || busy} onCheckedChange={toggle} />
      </SettingsRow>
      {on ? (Object.keys(NAMES) as PushCategory[]).map((name) => (
        <SettingsRow key={name} label={text(...NAMES[name])} htmlFor={`web-push-${name}`}>
          <Switch
            id={`web-push-${name}`}
            checked={categories.includes(name)}
            disabled={busy}
            onCheckedChange={(checked) => choose(name, checked)}
          />
        </SettingsRow>
      )) : null}
      {on ? (
        <SettingsRow label={text("Send a test notification", "发一条测试通知")}>
          <Button variant="elevated" disabled={busy} onClick={() => run(testPush)}>{text("Send", "发送")}</Button>
        </SettingsRow>
      ) : null}
    </SettingsSection>
  );
}
