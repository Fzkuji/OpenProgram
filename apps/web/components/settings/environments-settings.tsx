"use client";

/**
 * Environments — where conversations run their commands and file tools.
 *
 * Lists this computer plus every SSH host and Docker container the user
 * added, marks the default for new conversations, and adds / tests /
 * removes entries. Same registry `openprogram environments` edits; every
 * change is broadcast so open composers refresh their picker.
 */
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SolarIcon } from "@/components/solar-icons";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import {
  ensureEnvironments,
  environmentIconName,
  environmentWhere,
  useEnvironments,
  type EnvironmentInfo,
} from "@/lib/environments/store";
import { SimpleSelect } from "@/components/ui/select";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { SettingsPage } from "./settings-ui";
import styles from "./settings-page.module.css";

interface Result {
  ok: boolean;
  error?: string;
  id?: string;
}

interface TestResult {
  id: string;
  ok: boolean;
  detail: string;
}

type Kind = "ssh" | "docker";

const EMPTY_FORM = { kind: "ssh" as Kind, id: "", name: "", target: "", workdir: "", image: "ubuntu:24.04" };

export function EnvironmentsSettings() {
  const { text } = useTranslation();
  const items = useEnvironments((s) => s.items);
  const defaultId = useEnvironments((s) => s.default);
  const loaded = useEnvironments((s) => s.loaded);
  const [status, setStatus] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [form, setForm] = useState(EMPTY_FORM);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void ensureEnvironments(true);
  }, []);

  function note(id: string, ok: boolean, msg: string) {
    setStatus((s) => ({ ...s, [id]: { ok, msg } }));
  }

  async function mutate(action: string, payload: Record<string, unknown>): Promise<Result> {
    const noAnswer = { ok: false, error: text("The worker did not answer.", "后台没有响应。") };
    try {
      return (await wsRequest<Result>(action, payload, "environment_result", undefined, 8000)) ?? noAnswer;
    } catch {
      return noAnswer;
    }
  }

  async function makeDefault(env: EnvironmentInfo) {
    const res = await mutate("set_default_environment", { id: env.id });
    if (!res.ok) note(env.id, false, res.error || "");
  }

  async function remove(env: EnvironmentInfo) {
    if (!(await confirmAction({
      title: text(`Remove "${env.name}"?`, `删除“${env.name}”？`),
      description: text(
        "New conversations can no longer pick it. Nothing on that machine is deleted.",
        "新会话将不能再选择它。那台机器上的内容不会被删除。",
      ),
      confirmLabel: text("Remove", "删除"),
      destructive: true,
    }))) return;
    const res = await mutate("delete_environment", { id: env.id });
    if (!res.ok) note(env.id, false, res.error || "");
  }

  async function test(env: EnvironmentInfo) {
    note(env.id, true, text("Testing…", "测试中…"));
    const res = await wsRequest<TestResult>(
      "test_environment",
      { id: env.id },
      "environment_test",
      (d) => d?.id === env.id,
      30000,
    );
    if (!res) note(env.id, false, text("The worker did not answer.", "后台没有响应。"));
    else note(env.id, res.ok, res.detail);
  }

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    if (items.some((env) => env.id === form.id.trim())) {
      setFormError(text(
        `An environment with the ID "${form.id.trim()}" already exists. Pick another ID.`,
        `ID 为“${form.id.trim()}”的环境已存在，请换一个 ID。`,
      ));
      return;
    }
    setFormError("");
    setBusy(true);
    const environment: Record<string, string> = {
      id: form.id.trim(),
      name: form.name.trim(),
      kind: form.kind,
    };
    if (form.kind === "ssh") {
      environment.ssh_target = form.target.trim();
      environment.workdir = form.workdir.trim();
    } else {
      environment.image = form.image.trim();
    }
    const res = await mutate("save_environment", { environment });
    setBusy(false);
    if (!res.ok) {
      setFormError(res.error || "");
      return;
    }
    setForm(EMPTY_FORM);
  }

  return (
    <SettingsPage
      title={text("Environments", "运行环境")}
      description={text(
        "Where a conversation runs its commands and reads and writes files. A new conversation starts in the default; its environment is fixed once you send the first message.",
        "会话在哪里执行命令、读写文件。新会话使用默认环境；发出第一条消息后，会话的环境就固定下来。",
      )}
    >
        <section>
          <h3 className={styles.sectionTitle}>{text("Environments", "环境列表")}</h3>
          <div className={styles.card}>
            {!loaded ? (
              <div className={styles.row}>
                <div className={styles.rowHelp}>{text("Loading…", "加载中…")}</div>
              </div>
            ) : (
              items.map((env) => {
                const st = status[env.id];
                const where = env.kind === "local"
                  ? text("This computer", "这台电脑")
                  : environmentWhere(env);
                const isDefault = env.id === defaultId;
                return (
                  <div key={env.id} className={`${styles.row} ${styles.rowTop}`}>
                    <SolarIcon name={environmentIconName(env.kind)} size={20} aria-hidden="true" />
                    <div className={`${styles.label} min-w-0 flex-1`}>
                      <div>
                        {env.name}
                        {isDefault ? (
                          <span className={styles.rowHelp}>{"  ·  "}{text("Default", "默认")}</span>
                        ) : null}
                      </div>
                      <div className={`${styles.rowHelp} ${styles.authMono}`}>
                        {env.id} · {kindLabel(env.kind, text)}{where ? ` · ${where}` : ""}
                      </div>
                      {st ? (
                        <div className={`${st.ok ? styles.rowStatusOk : styles.rowStatusError} [overflow-wrap:anywhere]`}>{st.msg}</div>
                      ) : null}
                    </div>
                    <div className="ml-auto flex shrink-0 flex-wrap justify-end gap-2">
                      {!isDefault ? (
                        <Button variant="elevated" onClick={() => makeDefault(env)}>
                          {text("Make default", "设为默认")}
                        </Button>
                      ) : null}
                      {env.kind !== "local" ? (
                        <Button variant="elevated" onClick={() => test(env)}>
                          {text("Test", "测试")}
                        </Button>
                      ) : null}
                      {env.kind !== "local" ? (
                        <Button variant="destructive" onClick={() => remove(env)}>
                          {text("Remove", "删除")}
                        </Button>
                      ) : null}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </section>

        <section>
          <h3 className={styles.sectionTitle}>{text("Add environment", "添加环境")}</h3>
          <form onSubmit={onAdd} className={`${styles.card} ${styles.authForm}`}>
            <label className={styles.authField}>
              {text("Type", "类型")}
              <SimpleSelect label={text("Type", "类型")}
                className="max-w-none"
                value={form.kind}
                onValueChange={(value) => setForm((f) => ({ ...f, kind: value }))}
                options={[
                  { value: "ssh" as Kind, label: text("SSH host", "SSH 主机") },
                  { value: "docker" as Kind, label: text("Docker container", "Docker 容器") },
                ]}
              />
            </label>
            <label className={styles.authField}>
              {text("ID", "ID")}
              <Input
                placeholder="devbox"
                spellCheck={false}
                value={form.id}
                onChange={(e) => setForm((f) => ({ ...f, id: e.target.value }))}
                required
              />
            </label>
            <label className={`${styles.authField} ${styles.authFieldWide}`}>
              {text("Name (optional)", "名称（可选）")}
              <Input
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </label>
            {form.kind === "ssh" ? (
              <>
                <label className={styles.authField}>
                  {text("Host", "主机")}
                  <Input
                    placeholder="user@host"
                    spellCheck={false}
                    className={styles.authMono}
                    value={form.target}
                    onChange={(e) => setForm((f) => ({ ...f, target: e.target.value }))}
                    required
                  />
                </label>
                <label className={styles.authField}>
                  {text("Remote folder (blank = home)", "远程文件夹（留空为家目录）")}
                  <Input
                    placeholder="~/work"
                    spellCheck={false}
                    className={styles.authMono}
                    value={form.workdir}
                    onChange={(e) => setForm((f) => ({ ...f, workdir: e.target.value }))}
                  />
                </label>
                <p className={`${styles.rowHelp} ${styles.authFieldWide}`}>
                  {text(
                    "Uses your ssh config and keys; password prompts are not supported.",
                    "使用你的 ssh 配置和密钥，不支持密码交互。",
                  )}
                </p>
              </>
            ) : (
              <>
                <label className={`${styles.authField} ${styles.authFieldWide}`}>
                  {text("Image", "镜像")}
                  <Input
                    spellCheck={false}
                    className={styles.authMono}
                    value={form.image}
                    onChange={(e) => setForm((f) => ({ ...f, image: e.target.value }))}
                    required
                  />
                </label>
                <p className={`${styles.rowHelp} ${styles.authFieldWide}`}>
                  {text(
                    "Each conversation gets its own container; the conversation's folders are mounted at the same paths.",
                    "每个会话一个容器；会话的文件夹以相同路径挂载进去。",
                  )}
                </p>
              </>
            )}
            {formError ? (
              <p className={`${styles.rowStatusError} ${styles.authFieldWide}`}>{formError}</p>
            ) : null}
            <div className={styles.authFieldWide}>
              <Button type="submit" disabled={busy}>{text("Add", "添加")}</Button>
            </div>
          </form>
        </section>
    </SettingsPage>
  );
}

function kindLabel(kind: string, text: (en: string, zh: string) => string): string {
  if (kind === "ssh") return "SSH";
  if (kind === "docker") return "Docker";
  return text("Local", "本地");
}
