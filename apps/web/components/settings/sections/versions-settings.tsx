"use client";

/**
 * Settings > Versions. Each change OpenProgram makes to itself is kept as a
 * version; this page shows which one runs, which it falls back to, and lets
 * the owner undo. Going back and undoing run in a conversation, so the page
 * only starts them and opens that conversation.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { settingsSend } from "@/lib/settings/settings-request";
import { openSession } from "@/lib/tabs/navigation/open-session";
import {
  fallbackDescription,
  keptDescription,
  runningDescription,
  savedEditsTitle,
  versionName,
  versionsQuery,
} from "@/lib/settings/versions";
import { SettingsPage, SettingsRow, SettingsSection } from "../settings-ui";

export function VersionsSettings() {
  const { t, text } = useTranslation();
  const client = useQueryClient();
  const versions = useQuery(versionsQuery);
  const [starting, setStarting] = useState(false);
  const page = versions.data;

  async function start(url: string, body: unknown) {
    setStarting(true);
    const started = await settingsSend<{ session_id: string }>(url, {
      body,
      failure: text("That could not be started.", "没能开始。"),
    });
    setStarting(false);
    void client.invalidateQueries({ queryKey: versionsQuery.queryKey });
    if (started?.session_id) openSession(started.session_id);
  }

  return (
    <SettingsPage
      title={t("settings.tab.versions")}
      description={text(
        "Each change OpenProgram makes to itself is kept as a version. Come here when something stopped working after a change.",
        "OpenProgram 对自己的每次修改都会保留成一个版本。修改之后有东西不正常了，就来这里。",
      )}
    >
      {versions.isError ? (
        <SettingsSection>
          <SettingsRow
            label={text("The list of versions could not be read", "读不到版本列表")}
            description={versions.error.message}
          >
            <Button variant="elevated" onClick={() => void versions.refetch()}>{text("Retry", "重试")}</Button>
          </SettingsRow>
        </SettingsSection>
      ) : null}
      {page && !page.managed ? (
        <SettingsSection>
          <SettingsRow
            label={text("This copy doesn't keep versions", "这个副本不保留版本")}
            description={text(
              "It runs from a fixed App or a source checkout, so there is nothing here to go back to. Installs that update themselves in place keep their versions on this page.",
              "它运行在固定的 App 或源码目录上，所以这里没有可以回去的版本。能原地更新自己的安装才会在这一页列出版本。",
            )}
          />
        </SettingsSection>
      ) : null}
      {page?.managed ? (
        <>
          <SettingsSection title={text("Right now", "当前")}>
            <SettingsRow
              label={<>{text("Running now", "正在运行")} <Badge>{versionName(page.current?.number, text)}</Badge></>}
              description={runningDescription(page, text)}
            />
            <SettingsRow
              label={text("Returns here if something breaks", "出问题时回到这里")}
              description={fallbackDescription(page, text)}
            />
          </SettingsSection>

          <SettingsSection
            title={text("Kept versions", "保留的版本")}
            description={text(
              "Use these when a change made things worse. Go back returns to exactly that version and removes everything after it. Undo change removes only that one change and keeps the rest. OpenProgram tests the result first, then restarts for about a minute. Conversations, settings and files stay as they are.",
              "某次修改让情况变差时用这里。“回到此版本”会精确回到那个版本，并去掉它之后的所有修改。“撤销修改”只去掉那一次修改，其余保留。OpenProgram 会先测试结果，然后重启大约一分钟。对话、设置和文件都不受影响。",
            )}
          >
            {page.versions.map((version) => (
              <SettingsRow
                key={version.id}
                label={<>{versionName(version.number, text)} {version.current ? <Badge>{text("Current", "当前")}</Badge> : null}</>}
                description={keptDescription(version, text)}
              >
                {version.can_undo_change ? (
                  <Button
                    variant="elevated"
                    disabled={starting}
                    onClick={() => void start("/api/versions/undo", { version: version.id, how: "undo_change" })}
                  >
                    {text("Undo change", "撤销修改")}
                  </Button>
                ) : null}
                {version.current ? null : (
                  <Button
                    variant="elevated"
                    disabled={starting}
                    onClick={() => void start("/api/versions/undo", { version: version.id, how: "go_back" })}
                  >
                    {text("Go back", "回到此版本")}
                  </Button>
                )}
              </SettingsRow>
            ))}
          </SettingsSection>

          {page.unapplied.length ? (
            <SettingsSection
              title={text("Edits saved but not in use", "已保存但未生效的改动")}
              description={text(
                "Files changed by hand inside a running version are set aside so the version can start clean. Touch this only if you want those edits back.",
                "在运行中的版本里手动改过的文件会被放到一边，好让版本干净地启动。只有想要回这些改动时才用这里。",
              )}
            >
              {page.unapplied.map((edits) => (
                <SettingsRow
                  key={edits.stamp}
                  label={savedEditsTitle(edits, text)}
                  description={text(
                    "Apply edits asks the Agent to bring them into a new version, tested like any other change.",
                    "“应用改动”会让 Agent 把它们做进一个新版本，和其他修改一样经过测试。",
                  )}
                >
                  <Button
                    variant="elevated"
                    disabled={starting}
                    onClick={() => void start("/api/versions/edits/apply", { stamp: edits.stamp })}
                  >
                    {text("Apply edits", "应用改动")}
                  </Button>
                </SettingsRow>
              ))}
            </SettingsSection>
          ) : null}
        </>
      ) : null}
    </SettingsPage>
  );
}
