"use client";

/**
 * Add device: first what it is, then one step.
 *
 * - Phone or tablet: a one-time QR code (Codex: "Connect your phone to this Mac").
 * - Another computer with OpenProgram: show its conversations here (paste
 *   the link it shows), or let it use this one (show this one's code).
 * - Server over SSH: a host from ~/.ssh/config, or ``user@host`` (Claude
 *   desktop's Add SSH connection); saved, then tested.
 * - Something else: described in a sentence and handed to the agent in a
 *   new chat, which finds the details and adds and tests it with
 *   ``openprogram machines``.
 * - Set up by hand: the full form.
 */
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ChevronRight, Monitor, Server, Smartphone, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { useSessionStore } from "@/lib/session-store";
import { openNewChat } from "@/lib/tabs/navigation/open-session";
import styles from "../settings-page.module.css";
import type { Ways } from "./access-section";
import { machineRequest, SAVE_WAIT_MS, sshHosts, TEST_WAIT_MS, type Text } from "./device-requests";
import { PairingCode } from "./pairing-code";

type Step = "choose" | "phone" | "computer" | "ssh" | "agent";

function Choice({ icon, title, sub, onClick }: { icon: ReactNode; title: string; sub: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 rounded-lg border bg-bg-input px-3 py-2.5 text-left shadow-raised hover:shadow-raised-hover"
    >
      <span className="text-text-muted [&_svg]:size-5">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-fs-base font-medium">{title}</span>
        <span className="block text-fs-sm text-text-muted">{sub}</span>
      </span>
      <ChevronRight className="size-4 text-text-muted" aria-hidden="true" />
    </button>
  );
}

/** Nothing reaches this computer yet: one click switches Same Wi-Fi on. */
function NoWayIn({ change, text }: { change: (body: { lan: boolean }) => Promise<string>; text: Text }) {
  const [problem, setProblem] = useState("");
  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-fs-base">
        {text(
          "Phones reach this computer over Wi-Fi. Switch on Same Wi-Fi to show the code.",
          "手机通过 Wi-Fi 连到这台电脑。打开“同一 Wi-Fi”后即可显示二维码。",
        )}
      </p>
      <Button onClick={() => void change({ lan: true }).then(setProblem)}>{text("Switch on and continue", "打开并继续")}</Button>
      {problem ? <p className="text-fs-sm text-destructive">{problem}</p> : null}
    </div>
  );
}

function hasWayIn(ways: Ways | null): boolean {
  return !!(ways?.lan.on || (ways?.anywhere?.on && ways.anywhere.state === "running"));
}

export function AddDeviceDialog({
  ways,
  change,
  onManual,
  onClose,
}: {
  ways: Ways | null;
  change: (body: Partial<Record<"lan" | "anywhere", boolean>>) => Promise<string>;
  onManual: () => void;
  onClose: () => void;
}) {
  const { text } = useTranslation();
  const [step, setStep] = useState<Step>("choose");

  const titles: Record<Step, string> = {
    choose: text("Add device", "添加设备"),
    phone: text("Add a phone or tablet", "添加手机或平板"),
    computer: text("Add another computer", "添加另一台电脑"),
    ssh: text("Add a server over SSH", "添加 SSH 服务器"),
    agent: text("Let the agent set it up", "交给智能体设置"),
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{titles[step]}</DialogTitle>
          {step === "choose" ? <DialogDescription>{text("What do you want to add?", "要添加什么设备？")}</DialogDescription> : null}
        </DialogHeader>
        {step === "choose" ? (
          <div className="flex flex-col gap-2">
            <Choice
              icon={<Smartphone />}
              title={text("Phone or tablet", "手机或平板")}
              sub={text("Use this computer's OpenProgram from it. Scan a code.", "在它上面用这台电脑的 OpenProgram，扫码即可。")}
              onClick={() => setStep("phone")}
            />
            <Choice
              icon={<Monitor />}
              title={text("Another computer with OpenProgram", "另一台装了 OpenProgram 的电脑")}
              sub={text("See its conversations here, or yours there.", "在这里看它的对话，或在那里用这台。")}
              onClick={() => setStep("computer")}
            />
            <Choice
              icon={<Server />}
              title={text("Server over SSH", "SSH 服务器")}
              sub={text("Run conversations in a folder on it.", "在它上面的文件夹里运行对话。")}
              onClick={() => setStep("ssh")}
            />
            <Choice
              icon={<Sparkles />}
              title={text("Something else — let the agent set it up", "其他设备，交给智能体")}
              sub={text("A virtual machine, Docker, a remote desktop… describe it.", "虚拟机、Docker、远程桌面……描述一下就行。")}
              onClick={() => setStep("agent")}
            />
            <button type="button" className="self-start pt-1 text-fs-sm text-text-muted hover:text-foreground" onClick={onManual}>
              {text("Set up by hand", "手动设置")}
            </button>
          </div>
        ) : null}
        {step === "phone" ? (
          hasWayIn(ways) ? (
            <PairingCode
              hint={text(
                "Scan with the phone's camera. It opens OpenProgram in the phone's browser, already signed in.",
                "用手机相机扫码，会在手机浏览器里打开 OpenProgram，并且已经登录。",
              )}
            />
          ) : <NoWayIn change={change} text={text} />
        ) : null}
        {step === "computer" ? <AnotherComputer ways={ways} change={change} onDone={onClose} /> : null}
        {step === "ssh" ? <SshServer onDone={onClose} /> : null}
        {step === "agent" ? <AskAgent onDone={onClose} /> : null}
        {step !== "choose" ? (
          <DialogFooter className="sm:justify-start">
            <Button variant="ghost" onClick={() => setStep("choose")}>{text("Back", "返回")}</Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function AnotherComputer({
  ways,
  change,
  onDone,
}: {
  ways: Ways | null;
  change: (body: { lan: boolean }) => Promise<string>;
  onDone: () => void;
}) {
  const { text } = useTranslation();
  const [direction, setDirection] = useState<"here" | "there">("here");
  const [link, setLink] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function connect(event: FormEvent) {
    event.preventDefault();
    if (!link.trim()) return;
    setBusy(true);
    setError("");
    const res = await machineRequest("connect_openprogram", { link: link.trim() }, text, SAVE_WAIT_MS);
    setBusy(false);
    if (res.ok) onDone();
    else setError(res.error || "");
  }

  return (
    <div className="flex flex-col gap-4">
      <ToggleGroup
        type="single"
        variant="segmented"
        value={direction}
        onValueChange={(value) => { if (value) setDirection(value as "here" | "there"); }}
        aria-label={text("Direction", "方向")}
      >
        <ToggleGroupItem value="here">{text("Show its conversations here", "在这里看它的对话")}</ToggleGroupItem>
        <ToggleGroupItem value="there">{text("Let it use this one", "让它用这台")}</ToggleGroupItem>
      </ToggleGroup>
      {direction === "here" ? (
        <form onSubmit={connect} className="flex flex-col gap-3">
          <p className="text-fs-sm text-text-muted">
            {text(
              "On the other computer open Settings → Devices → Add device → Another computer → Let it use this one, and paste its link here.",
              "在另一台电脑上打开 设置 → 设备 → 添加设备 → 另一台电脑 → 让它用这台，把链接粘贴到这里。",
            )}
          </p>
          <Input
            autoFocus
            spellCheck={false}
            className={styles.authMono}
            placeholder="http://192.168.1.20:18100/#pair=…"
            value={link}
            onChange={(e) => setLink(e.target.value)}
          />
          {error ? <p className={styles.rowStatusError}>{error}</p> : null}
          <Button type="submit" className="self-end" disabled={busy || !link.trim()}>{text("Connect", "连接")}</Button>
        </form>
      ) : hasWayIn(ways) ? (
        <PairingCode
          hint={text(
            "On the other computer: Add device → Another computer → Show its conversations here, then paste this link.",
            "在另一台电脑上：添加设备 → 另一台电脑 → 在这里看它的对话，然后粘贴这个链接。",
          )}
        />
      ) : <NoWayIn change={change} text={text} />}
    </div>
  );
}

function SshServer({ onDone }: { onDone: () => void }) {
  const { text } = useTranslation();
  const [hosts, setHosts] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [state, setState] = useState<{ busy: boolean; error: string }>({ busy: false, error: "" });

  useEffect(() => {
    void sshHosts().then(setHosts);
  }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    const value = target.trim();
    if (!value) return;
    setState({ busy: true, error: "" });
    const res = await machineRequest(
      "save_machine", { machine: { name: value, shell: { kind: "ssh", target: value } } }, text, SAVE_WAIT_MS,
    );
    if (!res.ok || !res.id) {
      setState({ busy: false, error: res.error || "" });
      return;
    }
    const tested = await wsRequest<{ id: string; ok: boolean; message: string }>(
      "test_machine", { id: res.id }, "machine_test", (d) => d?.id === res.id, TEST_WAIT_MS, { requestId: true },
    ).catch(() => null);
    if (tested && !tested.ok) {
      setState({ busy: false, error: tested.message });
      return;
    }
    onDone();
  }

  return (
    <form onSubmit={add} className="flex flex-col gap-3">
      {hosts.length ? (
        <>
          <span className="text-fs-sm text-text-muted">{text("From your SSH configuration", "来自你的 SSH 配置")}</span>
          <div className="flex flex-wrap gap-2">
            {hosts.map((host) => (
              <Button
                key={host}
                type="button"
                variant="elevated"
                size="sm"
                aria-pressed={target === host}
                className="font-mono"
                onClick={() => setTarget(host)}
              >
                {host}
              </Button>
            ))}
          </div>
          <span className="pt-1 text-fs-sm text-text-muted">{text("Or type an address", "或者输入地址")}</span>
        </>
      ) : null}
      <Input
        autoFocus={!hosts.length}
        spellCheck={false}
        className={styles.authMono}
        placeholder="user@host"
        value={target}
        onChange={(e) => setTarget(e.target.value)}
      />
      <p className="text-fs-sm text-text-muted">
        {text(
          "Keys come from your SSH agent and ~/.ssh/config. The connection is tested before you use it.",
          "密钥来自你的 SSH agent 和 ~/.ssh/config。添加后会先测试连接。",
        )}
      </p>
      {state.error ? <p className={`${styles.rowStatusError} [overflow-wrap:anywhere]`}>{state.error}</p> : null}
      <Button type="submit" className="self-end" disabled={state.busy || !target.trim()}>{text("Add", "添加")}</Button>
    </form>
  );
}

function agentPrompt(description: string, text: Text): string {
  return text(
    `Add this device to OpenProgram so you can use it: ${description}\n\nFind the details yourself and ask me only for what you can't find. Add it with \`openprogram machines add\`, then check it with \`openprogram machines test\`.`,
    `把这台设备加到 OpenProgram 里，让你可以使用它：${description}\n\n需要的信息你自己去查，查不到的再问我。用 \`openprogram machines add\` 添加，再用 \`openprogram machines test\` 测试。`,
  );
}

function AskAgent({ onDone }: { onDone: () => void }) {
  const { text } = useTranslation();
  const [description, setDescription] = useState("");

  function start(event: FormEvent) {
    event.preventDefault();
    const value = description.trim();
    if (!value) return;
    const draft = openNewChat();
    useSessionStore.getState().setComposerInputFor(draft, agentPrompt(value, text));
    onDone();
  }

  return (
    <form onSubmit={start} className="flex flex-col gap-3">
      <Textarea
        autoFocus
        rows={3}
        value={description}
        placeholder={text(
          "The Ubuntu VM in UTM on this Mac; I want the agent to use its screen",
          "这台 Mac 上 UTM 里的 Ubuntu 虚拟机，想让智能体操作它的屏幕",
        )}
        onChange={(e) => setDescription(e.target.value)}
      />
      <p className="text-fs-sm text-text-muted">
        {text(
          "Opens a new chat with this request. The agent finds the details, asks what it can't find, adds the device and tests it.",
          "会打开一个新对话并填好请求。智能体自己查参数，查不到的再问你，然后添加并测试。",
        )}
      </p>
      <Button type="submit" className="self-end" disabled={!description.trim()}>{text("Start", "开始")}</Button>
    </form>
  );
}
